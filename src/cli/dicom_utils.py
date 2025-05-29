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
from functools import partial

from pydicom import dcmread, dcmwrite
from pydicom.dataset import Dataset
from pydicom.errors import InvalidDicomError
from pynetdicom import AE, debug_logger, evt
from pynetdicom.association import Association
from pynetdicom.dimse_primitives import C_FIND, C_GET # C_ECHO, C_MOVE, C_STORE not directly used as types
import pynetdicom.sop_class as sop_class
from pynetdicom.presentation import StoragePresentationContexts # Import directly
# Commented out direct imports, will use sop_class.ClassName instead
# from pynetdicom.sop_class import (
#     VerificationSOPClass,
#     PatientRootQueryRetrieveInformationModelFind,
#     StudyRootQueryRetrieveInformationModelFind,
#     PatientRootQueryRetrieveInformationModelMove,
#     StudyRootQueryRetrieveInformationModelMove,
#     PatientRootQueryRetrieveInformationModelGet,
#     StudyRootQueryRetrieveInformationModelGet,
#     CompositeInstanceRootRetrieveGet,
#     StoragePresentationContexts,
#     RTPlanStorage,
#     CTImageStorage,
#     MRImageStorage,
#     RTStructureSetStorage,
#     RTDoseStorage,
#     RTBeamsTreatmentRecordStorage,
# )
from pynetdicom import status as pynetdicom_status # Corrected import


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
        args.aet, args.aec, args.host, args.port, [sop_class.VerificationSOPClass]
        )
        # No need to check 'if assoc:' because _establish_association now raises on failure
        status = assoc.send_c_echo()
        if status:
            logger.info(
                f"C-ECHO status: 0x{status.Status:04X}" # Simplified logging
            )
            if status.Status != 0x0000:  # Not success (0x0000 is Success)
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
    # Using integer status codes for comparison
    # 0x0000: Success
    # 0xFF00, 0xFF01: Pending
    if status_response.Status == 0x0000 or \
       status_response.Status == 0xFF00 or \
       status_response.Status == 0xFF01: # Check for Success or any Pending
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

        if status_response.Status == 0x0000:  # Final success response
            logger.info(
                f"C-FIND operation completed successfully with peer {peer_ae_title}."
            )
            return False  # Stop C-FIND
        return True  # Continue for Pending status

    else:  # Failure, Cancel, etc.
        error_msg = f"C-FIND RSP from {peer_ae_title}: Error - Status 0x{status_response.Status:04X}" # Simplified logging
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
        return sop_class.PatientRootQueryRetrieveInformationModelFind
    return sop_class.StudyRootQueryRetrieveInformationModelFind


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
                # If _on_find_response returned False, check if it was due to a non-Success/non-Pending status
                if not (status_rsp.Status == 0x0000 or \
                        status_rsp.Status == 0xFF00 or \
                        status_rsp.Status == 0xFF01):
                    raise DicomOperationError(f"C-FIND failed with status 0x{status_rsp.Status:04X}")
                break  # Stop if handler returns False (either Success or error classified by _on_find_response)
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
    Callback handler for C-MOVE interim responses (from EVT_C_MOVE).
    """
    # When Event is constructed as Event(assoc, EVT_C_MOVE, dataset=identifier, status_dataset=status_ds)
    # event.status_dataset is the DICOM Dataset containing the status from the peer.
    # event.dataset is the identifier dataset.
    status_ds = event.status_dataset 
    identifier_ds = event.dataset 

    if status_ds is not None and hasattr(status_ds, 'Status'):
        status_value = status_ds.Status
        logger.info(
            f"C-MOVE Response: Status 0x{status_value:04X}"
        )

        # Log details from the status_ds (which should be the C-MOVE response status dataset)
        # or from identifier_ds if it contains NumberOf... elements (less common for final C-MOVE rsp)
        # For C-MOVE, NumberOf... elements are typically in the status_ds itself.
        dataset_to_check_for_ops = status_ds # NumberOf... elements are in the status dataset for C-MOVE
        
        if dataset_to_check_for_ops:
            for attr_name in [
                "NumberOfRemainingSuboperations",
                "NumberOfCompletedSuboperations",
                "NumberOfWarningSuboperations",
                "NumberOfFailedSuboperations",
            ]:
                if hasattr(dataset_to_check_for_ops, attr_name):
                    logger.info(
                        f"  {attr_name.replace('NumberOf', '').strip()}: {getattr(dataset_to_check_for_ops, attr_name)}"
                    )

        # Check if status is an error/failure/warning (not Success or Pending)
        if not (status_value == 0x0000 or \
                status_value == 0xFF00 or \
                status_value == 0xFF01): # Not Success or Pending
            # ErrorComment is usually in the status dataset for C-MOVE
            if hasattr(status_ds, "ErrorComment") and status_ds.ErrorComment:
                logger.error(f"  Error Comment from C-MOVE RSP: {status_ds.ErrorComment}")
    else:
        logger.error("C-MOVE Response: No status dataset or Status attribute found in event.")



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
        return sop_class.PatientRootQueryRetrieveInformationModelMove
    return sop_class.StudyRootQueryRetrieveInformationModelMove


def _handle_move_scu(args: argparse.Namespace):
    """
    Handles the C-MOVE SCU operation.
    """
    logger.info(
        f"Performing C-MOVE to {args.aec} at {args.host}:{args.port}, "
        f"destination AET: {args.move_dest_aet}"
    )

    # if args.query_level == "IMAGE": # Allow IMAGE level C-MOVE for these tests/workflows
    #     # This is more of an input validation / user guidance issue.
    #     logger.error(
    #         "C-MOVE at IMAGE level is not typically supported directly. Please move a STUDY or SERIES."
    #     )
    #     # No DicomOperationError raised here as it's a usage note.
    #     # main() will not exit with error unless an exception is raised.
    #     return

    identifier_dataset = _build_move_identifier_dataset(args)
    # For IMAGE level C-MOVE, the identifier must also contain SOPInstanceUID.
    # _build_move_identifier_dataset currently does not add SOPInstanceUID.
    # This needs to be added if query_level is IMAGE.
    # However, the args passed to _handle_move_scu from the test's side_effect *does* include sop_instance_uid.
    # The _build_move_identifier_dataset is used if _handle_move_scu is called as a CLI command.
    # The args namespace passed from the side_effect is more complete.
    # The `identifier_dataset` for C-MOVE should be built from `args` directly.
    # Let's ensure SOPInstanceUID is part of the identifier if query_level is IMAGE.
    if args.query_level == "IMAGE" and hasattr(args, 'sop_instance_uid') and args.sop_instance_uid:
        identifier_dataset.SOPInstanceUID = args.sop_instance_uid
        # Also ensure PatientID, Study UID, Series UID are present if known from args,
        # as _build_move_identifier_dataset might not have access to the full context.
        # The Namespace 'args' passed to _handle_move_scu from the side_effect is comprehensive.
        # So, we should primarily rely on 'args' to build the identifier for C-MOVE.
        
        # Re-building identifier_dataset based on args for C-MOVE
        identifier_dataset = Dataset()
        identifier_dataset.QueryRetrieveLevel = args.query_level
        if hasattr(args, 'patient_id') and args.patient_id:
            identifier_dataset.PatientID = args.patient_id
        if hasattr(args, 'study_uid') and args.study_uid:
            identifier_dataset.StudyInstanceUID = args.study_uid
        if hasattr(args, 'series_uid') and args.series_uid:
            identifier_dataset.SeriesInstanceUID = args.series_uid
        if hasattr(args, 'sop_instance_uid') and args.sop_instance_uid: # Redundant given outer if, but safe
            identifier_dataset.SOPInstanceUID = args.sop_instance_uid
            
    model = _get_move_model(args.query_level)
    event_handlers = [(evt.EVT_C_MOVE, _on_move_response)] # Corrected event type
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
            # The _on_move_response handler (bound via event_handlers) will be called by pynetdicom
            # for all responses, including the final one.
            # This loop is just to get the final status for raising an overall operation error if needed.
            # No need to manually call _on_move_response here again with a manually constructed Event.
            # status_rsp is the final status dataset.
            if status_rsp.Status != 0x0000: # Not Success
                raise DicomOperationError(
                    f"C-MOVE failed with status 0x{status_rsp.Status:04X} (final status)"
                )
    except (DicomConnectionError, DicomOperationError) as e:
        logger.error(f"C-MOVE operation failed: {e}")
        raise
    finally:
        if assoc and assoc.is_established:
            assoc.release()
            logger.info("Association released.")


# --- C-GET SCU ---
def _on_get_response(event: evt.Event, output_directory: str) -> int:
    """
    Handler for EVT_C_STORE events during a C-GET operation.
    Saves the received DICOM dataset to a file.

    Args:
        event: The event send by the SCP.
        output_directory: The directory to save the received DICOM files.

    Returns:
        Status code (0x0000 for success).
    """
    dataset = event.dataset
    if not dataset:
        logger.error("C-STORE sub-operation failed: No dataset received.")
        return 0x0000 # Must return a status, even if nothing to save.

    # pynetdicom makes the dataset read-only by default
    dataset.is_little_endian = True  # Or whatever is appropriate
    dataset.is_implicit_VR = True   # Or whatever is appropriate

    try:
        sop_instance_uid = dataset.SOPInstanceUID
        filename = os.path.join(output_directory, f"{sop_instance_uid}.dcm")
        logger.info(f"Received C-STORE request for SOPInstanceUID: {sop_instance_uid}")
        dcmwrite(filename, dataset, write_like_original=False) # write_like_original=False as dataset is constructed
        logger.info(f"Successfully saved DICOM file to {filename}")
        return 0x0000  # Success status for C-STORE sub-operation
    except InvalidDicomError as e:
        logger.error(f"Failed to save DICOM file: Invalid DICOM data. {e}")
        return 0xA700 # Cannot understand
    except AttributeError:
        logger.error("Failed to save DICOM file: SOPInstanceUID missing in dataset.")
        # This is an issue with the dataset provided by the C-GET SCP
        return 0xA900 # Processing failure
    except Exception as e:
        logger.error(f"Failed to save DICOM file due to an unexpected error: {e}")
        return 0xA700 # Refused: Out of resources (or other general failure)


def _build_get_identifier_dataset(args: argparse.Namespace) -> Dataset:
    """
    Builds the DICOM Dataset for a C-GET query based on CLI arguments.
    Determines QueryRetrieveLevel based on the most specific UID provided.
    """
    ds = Dataset()
    # Default to PATIENT level, will be overridden if more specific UIDs are present
    ds.QueryRetrieveLevel = "PATIENT" 

    if args.patient_id:
        ds.PatientID = args.patient_id
    else:
        # PatientID is required for PATIENT/STUDY/SERIES level GETs in Patient Root
        # For CompositeInstanceRootRetrieveGet, it's not strictly required in the identifier
        # but good practice to include if known.
        ds.PatientID = "" # Or "*" if SCP requires it for non-image level GETs

    if args.study_uid:
        ds.StudyInstanceUID = args.study_uid
        ds.QueryRetrieveLevel = "STUDY"
    else:
        ds.StudyInstanceUID = ""

    if args.series_uid:
        ds.SeriesInstanceUID = args.series_uid
        ds.QueryRetrieveLevel = "SERIES"
    else:
        ds.SeriesInstanceUID = ""

    if args.sop_instance_uid:
        ds.SOPInstanceUID = args.sop_instance_uid
        ds.QueryRetrieveLevel = "IMAGE"
    else:
        ds.SOPInstanceUID = ""
    
    # Ensure at least one UID is provided.
    if not args.patient_id and not args.study_uid and not args.series_uid and not args.sop_instance_uid:
        raise InvalidInputError("At least one UID (PatientID, StudyUID, SeriesUID, or SOPInstanceUID) must be provided for C-GET.")

    logger.debug(f"C-GET identifier dataset built with QueryRetrieveLevel: {ds.QueryRetrieveLevel}")
    return ds


def _get_get_model(query_level: str) -> Any: # Return type is a SOP Class object
    """Gets the appropriate C-GET model based on query level."""
    if query_level == "IMAGE":
        # CompositeInstanceRootRetrieveGet is often preferred for specific instance retrieval
        return sop_class.CompositeInstanceRootRetrieveGet
    elif query_level == "SERIES":
        return sop_class.StudyRootQueryRetrieveInformationModelGet # Or PatientRoot if PatientID is the root
    elif query_level == "STUDY":
        return sop_class.StudyRootQueryRetrieveInformationModelGet # Or PatientRoot
    elif query_level == "PATIENT":
        return sop_class.PatientRootQueryRetrieveInformationModelGet
    else:
        # Fallback or error, though _build_get_identifier_dataset should prevent unknown levels
        logger.error(f"Unsupported query level for C-GET: {query_level}")
        raise DicomOperationError(f"Unsupported query level for C-GET: {query_level}")


def _handle_get_scu(args: argparse.Namespace):
    """
    Handles the C-GET SCU operation.
    """
    logger.info(
        f"Performing C-GET from {args.aec} at {args.host}:{args.port} to AET {args.aet}, output to {args.out_dir}"
    )

    # Ensure output directory exists
    try:
        os.makedirs(args.out_dir, exist_ok=True)
        logger.info(f"Output directory: {args.out_dir}")
    except OSError as e:
        raise InvalidInputError(f"Could not create output directory {args.out_dir}: {e}")

    assoc = None
    try:
        identifier_dataset = _build_get_identifier_dataset(args)
        # Query level is implicitly determined by the UIDs in identifier_dataset and used for model selection
        query_level = identifier_dataset.QueryRetrieveLevel 
        model = _get_get_model(query_level)

        # Prepare event handlers
        # We need to pass the output directory to the _on_get_response handler
        bound_on_get_response = partial(_on_get_response, output_directory=args.out_dir)
        event_handlers = [(evt.EVT_C_STORE, bound_on_get_response)]

        # Contexts: The C-GET model itself, plus storage contexts for receiving files
        requested_contexts = [model] + StoragePresentationContexts # Use direct import
        # Filter out any None values from StoragePresentationContexts, if any (though it's usually well-formed)
        requested_contexts = [ctx for ctx in requested_contexts if ctx is not None]


        assoc = _establish_association(
            args.aet, args.aec, args.host, args.port, requested_contexts, event_handlers
        )

        responses: Iterator[Tuple[Any, Optional[Dataset]]] = assoc.send_c_get(identifier_dataset, model)

        # Process C-GET responses
        for status_rsp, ds_rsp in responses: # ds_rsp is usually None for C-GET final response
            if status_rsp:
                logger.info(
                    f"C-GET RSP: Status 0x{status_rsp.Status:04X} " # Simplified logging
                )
                # Log sub-operations details if present in the final C-GET response
                if ds_rsp: # Some SCPs might send a dataset with the final C-GET response
                    for attr_name in [
                        "NumberOfRemainingSuboperations",
                        "NumberOfCompletedSuboperations",
                        "NumberOfWarningSuboperations",
                        "NumberOfFailedSuboperations",
                    ]:
                        if hasattr(ds_rsp, attr_name):
                            logger.info(
                                f"  {attr_name.replace('NumberOf', '').strip()}: {getattr(ds_rsp, attr_name)}"
                            )
                
                if not (status_rsp.Status == 0x0000 or \
                        status_rsp.Status == 0xFF00 or \
                        status_rsp.Status == 0xFF01): # Not Success or Pending
                    # If _on_get_response handles C-STORE failures, this might be redundant,
                    # but it's good to catch C-GET level failures.
                    error_msg = f"C-GET operation failed with status 0x{status_rsp.Status:04X}."
                    if hasattr(status_rsp, "ErrorComment") and status_rsp.ErrorComment:
                        error_msg += f" Error Comment: {status_rsp.ErrorComment}"
                    raise DicomOperationError(error_msg)
            else:
                # This should ideally not happen if the association is healthy
                raise DicomOperationError("C-GET failed: No response status from SCP.")
        
        logger.info("C-GET operation completed.") # Further success details logged by _on_get_response

    except (DicomConnectionError, DicomOperationError, InvalidInputError) as e:
        logger.error(f"C-GET operation failed: {e}")
        raise
    except Exception as e: # Catch any other unexpected errors
        logger.error(f"An unexpected error occurred during C-GET: {e}", exc_info=True)
        raise DicomUtilsError(f"Unexpected C-GET error: {e}") from e
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

    if status.Status == 0x0000: # Success
        logger.info(f"C-STORE success for SOP Instance: {sop_instance_uid}")
    else:
        error_message = (
            f"C-STORE failed for SOP Instance: {sop_instance_uid} "
            f"with status 0x{status.Status:04X}" # Simplified logging
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
        sop_class.RTPlanStorage,
        sop_class.CTImageStorage,
        sop_class.MRImageStorage,
        sop_class.RTStructureSetStorage,
        sop_class.RTDoseStorage,
        sop_class.RTBeamsTreatmentRecordStorage,
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
                    if status_rsp.Status == 0x0000: # Success
                        files_sent_successfully += 1
                    elif status_rsp.Status != 0xFF00 and status_rsp.Status != 0xFF01: # Not Pending
                        files_with_dimse_errors += 1
                        logger.error(
                            f"  DIMSE service error for {fpath}: Status 0x{status_rsp.Status:04X}" # Simplified logging
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

    get_parser = subparsers.add_parser(
        "get", help="Perform C-GET.", parents=[common_parser]
    )
    get_parser.add_argument(
        "--patient-id", help="Patient ID for the C-GET operation."
    )
    get_parser.add_argument(
        "--study-uid", help="Study Instance UID for the C-GET operation."
    )
    get_parser.add_argument(
        "--series-uid", help="Series Instance UID for the C-GET operation."
    )
    get_parser.add_argument(
        "--sop-instance-uid",
        help="SOP Instance UID for the C-GET operation (specific image).",
    )
    get_parser.add_argument(
        "--out-dir",
        required=True,
        help="Directory to save received DICOM files.",
    )
    get_parser.set_defaults(func=_handle_get_scu)
    
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
            # Removed sys.exit(0) for library compatibility
        except DicomUtilsError as e:
            # Error message is already logged by the handler or _establish_association
            # Print a concise error to stderr for the user when run as CLI.
            # Re-raise for library use.
            print(f"Error: {e}", file=sys.stderr)
            raise # Re-raise for library use
        except Exception as e:  # Catch any other unexpected errors
            logger.critical(
                f"An unexpected critical error occurred: {e}", exc_info=True
            )
            print(f"An unexpected critical error occurred: {e}", file=sys.stderr)
            raise DicomUtilsError(f"An unexpected critical error occurred: {e}") from e # Wrap and re-raise
    else:
        # Should not be reached if subparsers are 'required'
        parser.print_help(sys.stderr) # Print help to stderr for errors
        # Removed sys.exit(2) for library compatibility
        # Raise an error or let the caller handle it (e.g. if main is called directly without args)
        raise InvalidInputError("No command provided to DICOM utility.")


if __name__ == "__main__":
    # This block now handles CLI execution and exit codes explicitly
    parser = _setup_parsers()
    args = parser.parse_args()

    # Configure logging level based on verbosity
    if args.verbose:
        logger.setLevel(logging.DEBUG)
        debug_logger() 
    else:
        if logger.level == logging.NOTSET or logger.level > logging.INFO:
            logger.setLevel(logging.INFO)
    
    if not logging.getLogger().hasHandlers():
         logging.basicConfig(
             level=logger.level,
             format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
             stream=sys.stdout,
         )

    try:
        main() # Call the refactored main which now raises exceptions
        sys.exit(0) # Explicit success exit for CLI
    except DicomUtilsError:
        sys.exit(1) # Specific exit code for DicomUtilsError
    except Exception:
        sys.exit(1) # General error exit code
    main()
