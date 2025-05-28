#!/usr/bin/env python3
"""
DICOM Utility CLI Tool.

This script provides a command-line interface for common DICOM network operations
(C-ECHO, C-FIND, C-MOVE, C-STORE) using pynetdicom.
"""
import argparse
import logging
import os
import sys
from typing import List, Tuple, Optional, Any, Iterator

from pydicom import dcmread
from pydicom.dataset import Dataset
from pynetdicom import AE, debug_logger, evt
from pynetdicom.association import Association
from pynetdicom.dimse_primitives import C_FIND # C_ECHO, C_MOVE, C_STORE not directly used as types
from pynetdicom.sop_class import (
    VerificationSOPClass,
    PatientRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelFind,
    PatientRootQueryRetrieveInformationModelMove,
    StudyRootQueryRetrieveInformationModelMove,
    RTPlanStorage,
    CTImageStorage,
    MRImageStorage,
    RTStructureSetStorage,
    RTDoseStorage,
    RTBeamsTreatmentRecordStorage,
)
from pynetdicom.status import Status


# Configure logger for this module
logger = logging.getLogger("dicom_utils")
# Default handler if no other configuration is set (e.g., when run as script)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler(sys.stdout))
    logger.setLevel(logging.INFO)  # Default level


# --- Custom Exceptions ---
class DicomUtilsError(Exception):
    """Base class for exceptions in this module."""


class DicomConnectionError(DicomUtilsError):
    """Raised for errors during DICOM association or network issues."""


class DicomOperationError(DicomUtilsError):
    """Raised when a DICOM operation (C-ECHO, C-FIND, etc.) fails."""


class InvalidInputError(DicomUtilsError):
    """Raised for invalid user input, like file paths."""


# --- DICOM Association Helper ---
def _establish_association(
    ae_title: str,
    peer_ae_title: str,
    peer_host: str,
    peer_port: int,
    contexts: List[Any],
    event_handlers: Optional[List[Tuple[evt.EventType, callable]]] = None,
) -> Association: # Return type changed from Optional[Association] as it now raises on failure
    """
    Establishes a DICOM association.

    Args:
        ae_title: The calling AE title.
        peer_ae_title: The called AE title (SCP).
        peer_host: Hostname or IP address of the SCP.
        peer_port: Port number of the SCP.
        contexts: A list of presentation contexts to request.
        event_handlers: Optional list of (event, handler) tuples for pynetdicom events.

    Returns:
        An established Association object.

    Raises:
        DicomConnectionError: If association fails.
    """
    ae = AE(ae_title=ae_title)
    for context in contexts:
        ae.add_requested_context(context)

    logger.info(
        f"Attempting association with {peer_ae_title} at {peer_host}:{peer_port} from AET {ae_title}"
    )
    try:
        assoc = ae.associate(
            peer_host, peer_port, ae_title=peer_ae_title, evt_handlers=event_handlers
        )
        if assoc.is_established:
            logger.info("Association established.")
            return assoc
        # This else block might be unreachable if assoc throws exception on failure
        # For robustness, explicitly raise if not established for any reason.
        raise DicomConnectionError(
            f"Association rejected, aborted or never connected to {peer_ae_title}. "
            f"Reason: {assoc.acceptor.primitive.result_str if assoc.acceptor and assoc.acceptor.primitive else 'Unknown'}"
        )
    except Exception as e:
        # Catching pynetdicom internal errors (like ConnectionRefusedError) or socket errors
        raise DicomConnectionError(f"Association failed: {str(e)}")


# --- C-ECHO SCU ---
def _handle_echo_scu(args: argparse.Namespace):
    """
    Handles the C-ECHO SCU operation.

    Args:
        args: Parsed command-line arguments.
    """
    logger.info(
        f"Performing C-ECHO to {args.aec} at {args.host}:{args.port} from AET {args.aet}"
    )
    assoc = None # Ensure assoc is defined for finally block
    try:
        assoc = _establish_association(
            args.aet, args.aec, args.host, args.port, [VerificationSOPClass]
        )
        # No need to check 'if assoc:' because _establish_association now raises on failure
        status = assoc.send_c_echo()
        if status:
            logger.info(
                f"C-ECHO status: 0x{status.Status:04X} ({Status.STATUS_SUCCESS.get(status.Status, 'Unknown Status')})"
            )
            if status.Status != Status.Success:  # Not success
                raise DicomOperationError(
                    f"C-ECHO failed with status 0x{status.Status:04X}"
                )
        else:
            # This case might indicate a more severe issue if no status is returned at all
            raise DicomOperationError("C-ECHO failed: No response status from SCP.")
    except (DicomConnectionError, DicomOperationError) as e:
        # Log the specific error from DICOM operations
        logger.error(f"C-ECHO operation failed: {e}")
        raise  # Re-raise for main to handle exit code
    finally:
        if assoc and assoc.is_established:
            assoc.release()
            logger.info("Association released.")


# --- C-FIND SCU ---
def _on_find_response(
    status_response: C_FIND, identifier: Optional[Dataset], peer_ae_title: str
) -> bool:
    """
    Callback handler for C-FIND responses.

    Args:
        status_response: The C-FIND response status from the SCP.
        identifier: The C-FIND response identifier Dataset, if any.
        peer_ae_title: The AE title of the peer that sent the response.

    Returns:
        True to continue the C-FIND operation (for Pending responses), False to stop.
    """
    # Using pynetdicom.status.Status constants for comparison
    if status_response.Status in (
        Status.Success,
        Status.Pending,
        Status.PendingWarning,
    ):
        if identifier:
            logger.info(
                f"C-FIND RSP from {peer_ae_title}: Status 0x{status_response.Status:04X} (Pending/Success) - Found identifier:"
            )
            for elem in identifier:
                logger.info(f"  {elem.name}: {elem.value}")
        else:
            logger.info(
                f"C-FIND RSP from {peer_ae_title}: Status 0x{status_response.Status:04X} (Pending/Success) - No identifier data in this response."
            )

        if status_response.Status == Status.Success:  # Final success response
            logger.info(
                f"C-FIND operation completed successfully with peer {peer_ae_title}."
            )
            return False  # Stop C-FIND
        return True  # Continue for Pending status

    else:  # Failure, Cancel, etc.
        error_msg = f"C-FIND RSP from {peer_ae_title}: Error - Status 0x{status_response.Status:04X} ({Status.STATUS_FAILURE.get(status_response.Status, 'Unknown Failure Status')})"
        if identifier and hasattr(identifier, "ErrorComment"):
            error_msg += f" - Error Comment: {identifier.ErrorComment}"
        logger.error(error_msg)
        # Consider raising DicomOperationError here if immediate failure propagation is desired
        return False  # Stop C-FIND


def _build_find_query_dataset(args: argparse.Namespace) -> Dataset:
    """Builds the DICOM Dataset for a C-FIND query based on CLI arguments."""
    ds = Dataset()
    ds.QueryRetrieveLevel = args.query_level

    ds.PatientID = args.patient_id if args.patient_id else "*"
    ds.StudyInstanceUID = args.study_uid if args.study_uid else ""
    ds.SeriesInstanceUID = args.series_uid if args.series_uid else ""

    if args.modality:
        ds.Modality = args.modality

    # Universal matching and specific keys for return
    ds.PatientName = "*"  # Request PatientName
    ds.StudyDate = ""
    ds.StudyTime = ""
    ds.AccessionNumber = ""
    ds.SOPInstanceUID = ""
    ds.SeriesNumber = ""
    ds.InstanceNumber = ""
    return ds


def _get_find_model(query_level: str) -> Any: # Return type is a SOP Class object
    """Gets the appropriate C-FIND model based on query level."""
    if query_level == "PATIENT":
        return PatientRootQueryRetrieveInformationModelFind
    return StudyRootQueryRetrieveInformationModelFind


def _handle_find_scu(args: argparse.Namespace):
    """
    Handles the C-FIND SCU operation.
    """
    logger.info(
        f"Performing C-FIND to {args.aec} at {args.host}:{args.port} from AET {args.aet}"
    )
    query_dataset = _build_find_query_dataset(args)
    model = _get_find_model(args.query_level)
    assoc = None
    try:
        assoc = _establish_association(
            args.aet, args.aec, args.host, args.port, [model]
        )
        responses: Iterator[
            Tuple[C_FIND, Optional[Dataset]]
        ] = assoc.send_c_find(query_dataset, model)
        for status_rsp, identifier_rsp in responses:
            if not _on_find_response(status_rsp, identifier_rsp, args.aec):
                if status_rsp.Status != Status.Success and status_rsp.Status not in (Status.Pending, Status.PendingWarning):
                    # If _on_find_response returned False due to an error status
                    raise DicomOperationError(f"C-FIND failed with status 0x{status_rsp.Status:04X}")
                break  # Stop if handler returns False (e.g., on success or error)
    except (DicomConnectionError, DicomOperationError) as e:
        logger.error(f"C-FIND operation failed: {e}")
        raise
    finally:
        if assoc and assoc.is_established:
            assoc.release()
            logger.info("Association released.")


# --- C-MOVE SCU ---
def _on_move_response(event: evt.Event):
    """
    Callback handler for C-MOVE interim responses (from EVT_C_MOVE_RSP).
    """
    status = event.status
    dataset = event.dataset

    logger.info(
        f"C-MOVE Response: Status 0x{status.Status:04X} ({Status.STATUS_CHOICES.get(status.Status, 'Unknown Status')})"
    )

    if dataset:
        # Standard C-MOVE response elements
        for attr_name in [
            "NumberOfRemainingSuboperations",
            "NumberOfCompletedSuboperations",
            "NumberOfWarningSuboperations",
            "NumberOfFailedSuboperations",
        ]:
            if hasattr(dataset, attr_name):
                logger.info(
                    f"  {attr_name.replace('NumberOf', '').strip()}: {getattr(dataset, attr_name)}"
                )

    if status.Status not in (
        Status.Success,
        Status.Pending,
        Status.PendingWarning,
    ):
        if hasattr(status, "ErrorComment") and status.ErrorComment:
            logger.error(f"  Error Comment from C-MOVE RSP: {status.ErrorComment}")


def _build_move_identifier_dataset(args: argparse.Namespace) -> Dataset:
    """Builds the DICOM Dataset (identifier) for a C-MOVE operation."""
    ds = Dataset()
    ds.QueryRetrieveLevel = args.query_level

    if args.patient_id:
        ds.PatientID = args.patient_id
    if args.study_uid:
        ds.StudyInstanceUID = args.study_uid
    if args.series_uid:
        if args.query_level == "SERIES":
            ds.SeriesInstanceUID = args.series_uid
        else:
            logger.warning(
                "Series UID provided but query level is not SERIES. It might be ignored by SCP."
            )
    return ds


def _get_move_model(query_level: str) -> Any: # Return type is a SOP Class object
    """Gets the appropriate C-MOVE model based on query level."""
    if query_level == "PATIENT":
        return PatientRootQueryRetrieveInformationModelMove
    return StudyRootQueryRetrieveInformationModelMove


def _handle_move_scu(args: argparse.Namespace):
    """
    Handles the C-MOVE SCU operation.
    """
    logger.info(
        f"Performing C-MOVE to {args.aec} at {args.host}:{args.port}, "
        f"destination AET: {args.move_dest_aet}"
    )

    if args.query_level == "IMAGE":
        # This is more of an input validation / user guidance issue.
        logger.error(
            "C-MOVE at IMAGE level is not typically supported directly. Please move a STUDY or SERIES."
        )
        # No DicomOperationError raised here as it's a usage note.
        # main() will not exit with error unless an exception is raised.
        return

    identifier_dataset = _build_move_identifier_dataset(args)
    model = _get_move_model(args.query_level)
    event_handlers = [(evt.EVT_C_MOVE_RSP, _on_move_response)]
    assoc = None
    try:
        assoc = _establish_association(
            args.aet, args.aec, args.host, args.port, [model], event_handlers
        )
        logger.info(f"Requesting C-MOVE to destination AET: {args.move_dest_aet}")
        responses: Iterator[
            Tuple[Any, Optional[Dataset]] # Type of status_rsp is C_MOVE
        ] = assoc.send_c_move(identifier_dataset, args.move_dest_aet, model)

        # Process final C-MOVE response
        for status_rsp, _ in responses: # identifier_rsp is usually None or not useful for final C-MOVE
            # The _on_move_response handler also logs interim responses.
            # We call it again for the final response for consistent logging.
            # A more refined approach might be to have a separate final response log.
            _on_move_response(evt.Event(assoc, status_rsp, None)) # Pass None as identifier for final
            if status_rsp.Status != Status.Success:
                raise DicomOperationError(
                    f"C-MOVE failed with status 0x{status_rsp.Status:04X}"
                )
    except (DicomConnectionError, DicomOperationError) as e:
        logger.error(f"C-MOVE operation failed: {e}")
        raise
    finally:
        if assoc and assoc.is_established:
            assoc.release()
            logger.info("Association released.")


# --- C-STORE SCU ---
def _on_store_response(event: evt.Event):
    """
    Callback handler for C-STORE responses (from EVT_C_STORE_RSP).
    """
    status = event.status
    sop_instance_uid = "Unknown SOPInstanceUID"
    if (
        event.context
        and event.context.dataset
        and hasattr(event.context.dataset, "SOPInstanceUID")
    ):
        sop_instance_uid = event.context.dataset.SOPInstanceUID

    if status.Status == Status.Success:
        logger.info(f"C-STORE success for SOP Instance: {sop_instance_uid}")
    else:
        error_message = (
            f"C-STORE failed for SOP Instance: {sop_instance_uid} "
            f"with status 0x{status.Status:04X} ({Status.STATUS_FAILURE.get(status.Status, 'Unknown Failure')})"
        )
        if hasattr(status, "ErrorComment") and status.ErrorComment:
            error_message += f" - Error: {status.ErrorComment}"
        logger.error(error_message)
        # Consider raising DicomOperationError here if a single store failure should halt the batch


def _get_dicom_files_from_path(filepath: str) -> List[str]:
    """
    Scans a file or directory path and returns a list of valid DICOM file paths.
    """
    if not os.path.exists(filepath):
        raise InvalidInputError(f"File or directory not found: {filepath}")

    dicom_files: List[str] = []
    if os.path.isfile(filepath):
        try:
            dcmread(filepath, stop_before_pixels=True)
            dicom_files.append(filepath)
        except Exception as e:
            logger.warning(
                f"File {filepath} is not a readable DICOM file or an error occurred: {e}"
            )
    elif os.path.isdir(filepath):
        for root, _, files in os.walk(filepath):
            for file in files:
                full_path = os.path.join(root, file)
                try:
                    dcmread(full_path, stop_before_pixels=True)
                    dicom_files.append(full_path)
                except Exception:
                    logger.debug(
                        f"Skipping non-DICOM or unreadable file: {full_path}"
                    )

    if not dicom_files:
        raise InvalidInputError(f"No valid DICOM files found at path: {filepath}")

    logger.info(f"Found {len(dicom_files)} DICOM file(s) to send from {filepath}.")
    return dicom_files


def _get_storage_contexts() -> List[Any]:
    """Returns a list of common DICOM storage presentation contexts."""
    return [
        RTPlanStorage,
        CTImageStorage,
        MRImageStorage,
        RTStructureSetStorage,
        RTDoseStorage,
        RTBeamsTreatmentRecordStorage,
        "1.2.840.10008.5.1.4.1.1.2",  # CT Image Storage (string UID)
        "1.2.840.10008.5.1.4.1.1.4",  # MR Image Storage (string UID)
        "1.2.840.10008.5.1.4.1.1.481.1",  # RT Plan Storage (string UID)
    ]


def _handle_store_scu(args: argparse.Namespace):
    """
    Handles the C-STORE SCU operation.
    """
    logger.info(
        f"Performing C-STORE to {args.aec} at {args.host}:{args.port} from AET {args.aet}"
    )
    assoc = None
    try:
        dicom_files = _get_dicom_files_from_path(args.filepath)
    except InvalidInputError as e:
        logger.error(f"C-STORE setup failed: {e}")
        raise # Re-raise for main to handle exit

    storage_contexts = _get_storage_contexts()
    event_handlers = [(evt.EVT_C_STORE_RSP, _on_store_response)]

    try:
        assoc = _establish_association(
            args.aet, args.aec, args.host, args.port, storage_contexts, event_handlers
        )
        files_sent_successfully = 0 # Based on DIMSE request status, actual storage is async
        files_with_dimse_errors = 0

        for fpath in dicom_files:
            try:
                logger.info(f"Attempting to send: {fpath}")
                dataset_to_send = dcmread(fpath)

                file_sop_class_uid = dataset_to_send.SOPClassUID
                if not any(
                    ctx.abstract_syntax == file_sop_class_uid
                    for ctx in assoc.accepted_contexts
                ):
                    logger.warning(
                        f"SOP Class {file_sop_class_uid} for file {fpath} "
                        "was not in accepted presentation contexts. Store might fail."
                    )

                status_rsp: Optional[Any] = assoc.send_c_store(dataset_to_send) # Type is C_STORE

                if status_rsp:
                    logger.debug(
                        f"C-STORE DIMSE service for {fpath} reported status: 0x{status_rsp.Status:04X}"
                    )
                    if status_rsp.Status == Status.Success:
                        files_sent_successfully += 1
                    elif status_rsp.Status not in (
                        Status.Pending,
                        Status.PendingWarning,
                    ):
                        files_with_dimse_errors += 1
                        logger.error(
                            f"  DIMSE service error for {fpath}: {Status.STATUS_FAILURE.get(status_rsp.Status, 'Unknown Failure')}"
                        )
                        if hasattr(status_rsp, "ErrorComment"):
                            logger.error(
                                f"    Error Comment: {status_rsp.ErrorComment}"
                            )
                else:
                    files_with_dimse_errors +=1
                    logger.error(
                        f"C-STORE request for {fpath} failed: No DIMSE status returned (association issue?)."
                    )
                    # If association drops, further sends in this loop will likely fail.
                    # Consider breaking, but _establish_association should handle critical connection loss.
                    # If it's a per-file issue, loop might continue.
            except Exception as e:
                files_with_dimse_errors +=1
                logger.error(
                    f"Error processing or sending file {fpath}: {e}", exc_info=True
                )
        
        total_files = len(dicom_files)
        logger.info(
            f"Finished sending files. {files_sent_successfully}/{total_files} requests were initially processed "
            f"successfully by DIMSE service. {files_with_dimse_errors} had DIMSE errors. "
            "(Check handler logs for SCP storage status from EVT_C_STORE_RSP)."
        )
        if files_with_dimse_errors > 0:
            raise DicomOperationError(f"{files_with_dimse_errors} files encountered DIMSE service errors during C-STORE.")

    except (DicomConnectionError, DicomOperationError) as e:
        logger.error(f"C-STORE operation failed: {e}")
        raise
    except Exception as e:
        logger.error(
            f"An unexpected error occurred during C-STORE: {e}", exc_info=True
        )
        raise DicomUtilsError(f"Unexpected C-STORE error: {e}") from e # Wrap for main
    finally:
        if assoc and assoc.is_established:
            assoc.release()
            logger.info("Association released.")


# --- Argument Parsing and Main Function ---
def _setup_parsers() -> argparse.ArgumentParser:
    """Sets up and returns the main argument parser with subparsers."""
    parser = argparse.ArgumentParser(description="DICOM Network Utility Tool.")
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging for pynetdicom and this script.",
    )

    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "--aet", default="DICOMUTILS", help="Calling AE Title (default: DICOMUTILS)."
    )
    common_parser.add_argument("--aec", required=True, help="Called AE Title (SCP).")
    common_parser.add_argument(
        "--host", required=True, help="Hostname or IP address of the SCP."
    )
    common_parser.add_argument(
        "--port", required=True, type=int, help="Port number of the SCP."
    )

    subparsers = parser.add_subparsers(
        title="Commands",
        dest="command",
        required=True,
        help="DICOM operation to perform",
    )

    echo_parser = subparsers.add_parser(
        "echo", help="Perform C-ECHO.", parents=[common_parser]
    )
    echo_parser.set_defaults(func=_handle_echo_scu)

    find_parser = subparsers.add_parser(
        "find", help="Perform C-FIND.", parents=[common_parser]
    )
    find_parser.add_argument(
        "--query-level",
        default="STUDY",
        choices=["PATIENT", "STUDY", "SERIES", "IMAGE"],
        help="Query retrieve level (default: STUDY).",
    )
    find_parser.add_argument("--patient-id", help="Patient ID for the query.")
    find_parser.add_argument(
        "--study-uid", help="Study Instance UID for the query."
    )
    find_parser.add_argument(
        "--series-uid", help="Series Instance UID for the query."
    )
    find_parser.add_argument(
        "--modality", help="Modality for the query (e.g., CT, MR, RTPLAN)."
    )
    find_parser.set_defaults(func=_handle_find_scu)

    move_parser = subparsers.add_parser(
        "move", help="Perform C-MOVE.", parents=[common_parser]
    )
    move_parser.add_argument(
        "--move-dest-aet",
        required=True,
        help="Destination AE Title for the C-MOVE operation.",
    )
    move_parser.add_argument(
        "--query-level",
        default="STUDY",
        choices=["PATIENT", "STUDY", "SERIES"],
        help="Query retrieve level for selecting what to move (default: STUDY).",
    )
    move_parser.add_argument(
        "--patient-id", help="Patient ID for selecting what to move."
    )
    move_parser.add_argument(
        "--study-uid", help="Study Instance UID for selecting what to move."
    )
    move_parser.add_argument(
        "--series-uid",
        help="Series Instance UID for selecting what to move (for SERIES level).",
    )
    move_parser.set_defaults(func=_handle_move_scu)

    store_parser = subparsers.add_parser(
        "store", help="Perform C-STORE.", parents=[common_parser]
    )
    store_parser.add_argument(
        "--filepath",
        required=True,
        help="Path to a single DICOM file or a directory containing DICOM files to send.",
    )
    store_parser.set_defaults(func=_handle_store_scu)

    return parser


def main():
    """Main function to parse arguments and dispatch to handlers."""
    parser = _setup_parsers()
    args = parser.parse_args()

    # Configure logging level based on verbosity
    # Ensure the module-level logger is configured.
    if args.verbose:
        logger.setLevel(logging.DEBUG)
        # Enable pynetdicom's verbose logging if requested
        # Note: debug_logger() sets up its own handlers, which might duplicate.
        # For fine-grained control, one might configure pynetdicom's logger directly.
        debug_logger() 
    else:
        # Ensure logger level is INFO if not already set lower by another config
        if logger.level == logging.NOTSET or logger.level > logging.INFO:
            logger.setLevel(logging.INFO)
    
    # Basic check if any handlers are configured on the root logger,
    # if not, add a default one for CLI usage.
    # This avoids duplicate logs if imported into a system with existing logging.
    if not logging.getLogger().hasHandlers():
         logging.basicConfig(
             level=logger.level, # Use the level set for our specific logger
             format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
             stream=sys.stdout, # Ensure logs go to stdout by default for CLI
         )


    if hasattr(args, "func"):
        try:
            args.func(args)
            # Assuming success if no DicomUtilsError was raised by handlers
            sys.exit(0) 
        except DicomUtilsError as e:
            # Error message is already logged by the handler or _establish_association
            # Print a concise error to stderr for the user.
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:  # Catch any other unexpected errors
            logger.critical(
                f"An unexpected critical error occurred: {e}", exc_info=True
            )
            print(f"An unexpected critical error occurred: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # Should not be reached if subparsers are 'required'
        parser.print_help(sys.stderr) # Print help to stderr for errors
        sys.exit(2) # Standard exit code for command line usage errors


if __name__ == "__main__":
    main()
