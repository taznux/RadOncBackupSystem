#!/usr/bin/env python3
"""
DICOM Utility CLI Tool.

This script provides a command-line interface for common DICOM network operations
(C-ECHO, C-FIND, C-MOVE, C-STORE, C-GET) using pynetdicom.
It can also be used as a library for these DICOM operations.
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
from pynetdicom.dimse_primitives import C_FIND, C_GET
import pynetdicom.sop_class as sop_class
from pynetdicom.presentation import StoragePresentationContexts


# Configure logger for this module
logger = logging.getLogger("dicom_utils")
# Default handler if no other configuration is set (e.g., when run as script)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler(sys.stdout)) # Changed from sys.stdout to sys.stderr for CLI tool
    logger.setLevel(logging.INFO)


# --- Custom Exceptions ---
class DicomUtilsError(Exception):
    """Base class for exceptions in this module."""

class DicomConnectionError(DicomUtilsError):
    """Raised for errors during DICOM association or network issues."""

class DicomOperationError(DicomUtilsError):
    """Raised when a DICOM operation (C-ECHO, C-FIND, etc.) fails."""
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status # DICOM status code, if applicable

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
) -> Association:
    """Establishes a DICOM association."""
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
        # This else block handles cases where associate() returns but not established
        failure_reason = "Unknown reason"
        if assoc.acceptor and assoc.acceptor.primitive:
            failure_reason = assoc.acceptor.primitive.result_str
        logger.error(f"Association rejected, aborted or never connected to {peer_ae_title}. Reason: {failure_reason}")
        raise DicomConnectionError(
            f"Association rejected, aborted or never connected to {peer_ae_title}. Reason: {failure_reason}"
        )
    except Exception as e: # Catches pynetdicom internal errors (e.g. ConnectionRefusedError) or socket errors
        logger.error(f"Association failed: {str(e)}", exc_info=True if logger.level == logging.DEBUG else False)
        raise DicomConnectionError(f"Association failed: {str(e)}")


# --- C-ECHO SCU ---
def perform_c_echo(calling_aet: str, peer_aet: str, peer_host: str, peer_port: int) -> None:
    """Performs a DICOM C-ECHO operation."""
    logger.info(
        f"Performing C-ECHO to {peer_aet} at {peer_host}:{peer_port} from AET {calling_aet}"
    )
    assoc = None
    try:
        assoc = _establish_association(
            calling_aet, peer_aet, peer_host, peer_port, [sop_class.Verification]
        )
        status = assoc.send_c_echo()
        if status:
            logger.info(f"C-ECHO RSP: Status 0x{status.Status:04X}")
            if status.Status != 0x0000:
                raise DicomOperationError(
                    f"C-ECHO failed with status 0x{status.Status:04X}", status=status.Status
                )
        else:
            raise DicomOperationError("C-ECHO failed: No response status from SCP.")
        logger.info("C-ECHO successful.")
    finally:
        if assoc and assoc.is_established:
            assoc.release()
            logger.info("Association released after C-ECHO.")

def _handle_echo_scu(args: argparse.Namespace):
    """Handles the C-ECHO SCU operation for CLI."""
    logger.info(
        f"CLI: Performing C-ECHO to {args.aec} at {args.host}:{args.port} from AET {args.aet}"
    )
    try:
        perform_c_echo(args.aet, args.aec, args.host, args.port)
        logger.info("CLI C-ECHO successful.")
    except (DicomConnectionError, DicomOperationError) as e:
        logger.error(f"CLI C-ECHO operation failed: {e}")
        raise


# --- C-FIND SCU ---
def _build_query_dataset_from_params(
    query_level: str, patient_id: str = "*", study_uid: str = "",
    series_uid: str = "", sop_instance_uid: str = "", modality: str = ""
) -> Dataset:
    """Builds a C-FIND or C-MOVE query dataset from parameters."""
    ds = Dataset()
    ds.QueryRetrieveLevel = query_level
    ds.PatientID = patient_id if patient_id is not None else "*" # Default to wildcard if None
    ds.StudyInstanceUID = study_uid if study_uid is not None else ""
    ds.SeriesInstanceUID = series_uid if series_uid is not None else ""
    ds.SOPInstanceUID = sop_instance_uid if sop_instance_uid is not None else ""
    if modality: ds.Modality = modality

    # Universal matching and specific keys for return (especially for C-FIND)
    # These ensure the SCP populates these fields in the response if available.
    if query_level != "IMAGE" or not sop_instance_uid : # Don't add these if querying for specific image
        ds.PatientName = "*"
        ds.StudyDate = ""
        ds.StudyTime = ""
        ds.AccessionNumber = ""
        ds.SeriesNumber = ""
        ds.InstanceNumber = ""
    return ds

def _get_query_model(query_level: str, operation: str = "FIND") -> Any:
    """Gets the appropriate C-FIND, C-MOVE, or C-GET model."""
    model_map = {
        "FIND": {"PATIENT": sop_class.PatientRootQueryRetrieveInformationModelFind,
                 "STUDY": sop_class.StudyRootQueryRetrieveInformationModelFind,
                 "SERIES": sop_class.StudyRootQueryRetrieveInformationModelFind,
                 "IMAGE": sop_class.StudyRootQueryRetrieveInformationModelFind},
        "MOVE": {"PATIENT": sop_class.PatientRootQueryRetrieveInformationModelMove,
                 "STUDY": sop_class.StudyRootQueryRetrieveInformationModelMove,
                 "SERIES": sop_class.StudyRootQueryRetrieveInformationModelMove,
                 "IMAGE": sop_class.StudyRootQueryRetrieveInformationModelMove},
        "GET": {"PATIENT": sop_class.PatientRootQueryRetrieveInformationModelGet,
                "STUDY": sop_class.StudyRootQueryRetrieveInformationModelGet,
                "SERIES": sop_class.StudyRootQueryRetrieveInformationModelGet,
                "IMAGE": sop_class.CompositeInstanceRootRetrieveGet} # Often preferred for specific instance
    }
    try:
        return model_map[operation.upper()][query_level.upper()]
    except KeyError:
        raise InvalidInputError(f"Unsupported combination of operation '{operation}' and query level '{query_level}'.")

def perform_c_find(
    calling_aet: str, peer_aet: str, peer_host: str, peer_port: int, query_level: str,
    patient_id: str = "*", study_uid: str = "", series_uid: str = "",
    sop_instance_uid: str = "", modality: str = ""
) -> List[Dataset]:
    """Performs a DICOM C-FIND operation."""
    logger.info(
        f"Performing C-FIND to {peer_aet} at {peer_host}:{peer_port} (AET {calling_aet}) "
        f"for QL={query_level}, PID={patient_id or '*'}, Study={study_uid or 'Any'}, "
        f"Series={series_uid or 'Any'}, SOP={sop_instance_uid or 'Any'}"
    )
    query_dataset = _build_query_dataset_from_params(
        query_level, patient_id, study_uid, series_uid, sop_instance_uid, modality
    )
    if query_level == "IMAGE" and not query_dataset.SOPInstanceUID:
        raise InvalidInputError("SOPInstanceUID is required for IMAGE level C-FIND.")

    model = _get_query_model(query_level, "FIND")
    assoc = None
    found_identifiers: List[Dataset] = []
    try:
        assoc = _establish_association(calling_aet, peer_aet, peer_host, peer_port, [model])
        responses = assoc.send_c_find(query_dataset, model)
        last_status = None
        for status_rsp, identifier_rsp in responses:
            last_status = status_rsp.Status
            if status_rsp.Status in (0xFF00, 0xFF01): # Pending
                if identifier_rsp:
                    found_identifiers.append(identifier_rsp)
            elif status_rsp.Status == 0x0000: # Success
                if identifier_rsp: found_identifiers.append(identifier_rsp)
                logger.info(f"C-FIND operation with {peer_aet} completed successfully.")
                break
            else: # Failure
                error_msg = f"C-FIND failed with status 0x{status_rsp.Status:04X}"
                comment = getattr(identifier_rsp, "ErrorComment", getattr(status_rsp, "ErrorComment", None))
                if comment: error_msg += f" - Error Comment: {comment}"
                raise DicomOperationError(error_msg, status=status_rsp.Status)

        if last_status == 0x0000 and not found_identifiers:
            raise DicomOperationError("No instances found", status=0x0000)
        return found_identifiers
    finally:
        if assoc and assoc.is_established: assoc.release(); logger.info("Association released.")

def _handle_find_scu(args: argparse.Namespace):
    """Handles the C-FIND SCU operation for CLI."""
    logger.info(f"CLI: C-FIND to {args.aec}@{args.host}:{args.port} from {args.aet}")
    try:
        results = perform_c_find(
            args.aet, args.aec, args.host, args.port, args.query_level,
            args.patient_id, args.study_uid, args.series_uid,
            args.sop_instance_uid if hasattr(args, 'sop_instance_uid') else "", # Added for completeness
            args.modality
        )
        logger.info(f"CLI C-FIND found {len(results)} matching instance(s).")
        for i, ds in enumerate(results):
            logger.info(f"  Result {i+1}: PatientID={ds.PatientID if 'PatientID' in ds else 'N/A'}, "
                        f"StudyUID={ds.StudyInstanceUID if 'StudyInstanceUID' in ds else 'N/A'}, "
                        f"SeriesUID={ds.SeriesInstanceUID if 'SeriesInstanceUID' in ds else 'N/A'}, "
                        f"SOP_UID={ds.SOPInstanceUID if 'SOPInstanceUID' in ds else 'N/A'}")
    except DicomOperationError as e:
        if e.status == 0x0000 and "No instances found" in str(e): logger.info(f"CLI C-FIND: {e}")
        else: logger.error(f"CLI C-FIND failed: {e}")
        raise
    except (DicomConnectionError, InvalidInputError) as e: logger.error(f"CLI C-FIND failed: {e}"); raise


# --- C-MOVE SCU ---
def _on_move_response(event: evt.Event): # For CLI logging
    """Callback handler for C-MOVE interim responses."""
    status_ds = event.status_dataset
    if status_ds and hasattr(status_ds, 'Status'):
        logger.info(f"C-MOVE RSP Status: 0x{status_ds.Status:04X}")
        for attr in ["Remaining", "Completed", "Warning", "Failed"]:
            tag_name = f"NumberOf{attr}Suboperations"
            if hasattr(status_ds, tag_name): logger.info(f"  {attr} Sub-ops: {getattr(status_ds, tag_name)}")
        if hasattr(status_ds, "ErrorComment") and status_ds.ErrorComment: logger.error(f"  Error: {status_ds.ErrorComment}")

def _handle_move_scu(args: argparse.Namespace): # Kept for CLI
    """Handles the C-MOVE SCU operation for CLI."""
    logger.info(f"CLI: C-MOVE to {args.aec}@{args.host}:{args.port}, dest AET: {args.move_dest_aet}")
    identifier_dataset = _build_query_dataset_from_params(
        args.query_level, args.patient_id, args.study_uid, args.series_uid,
        args.sop_instance_uid if hasattr(args, 'sop_instance_uid') and args.query_level == "IMAGE" else ""
    )
    if args.query_level == "IMAGE" and not identifier_dataset.SOPInstanceUID: # Check for IMAGE level move
        raise InvalidInputError("SOPInstanceUID is required for IMAGE level C-MOVE.")

    model = _get_query_model(identifier_dataset.QueryRetrieveLevel, "MOVE")
    assoc = None
    try:
        assoc = _establish_association(args.aet, args.aec, args.host, args.port, [model],
                                       event_handlers=[(evt.EVT_C_MOVE_RSP, _on_move_response)])
        final_status = None
        for status_rsp, _ in assoc.send_c_move(identifier_dataset, args.move_dest_aet, model):
            if status_rsp: final_status = status_rsp.Status
            else: raise DicomOperationError("C-MOVE failed: No/invalid intermediate status from SCP.")

        if final_status != 0x0000:
            raise DicomOperationError(f"C-MOVE failed with final status 0x{final_status:04X}", status=final_status)
        logger.info("CLI C-MOVE operation reported success by SCP.")
    except (DicomConnectionError, DicomOperationError, InvalidInputError) as e: logger.error(f"CLI C-MOVE failed: {e}"); raise
    finally:
        if assoc and assoc.is_established: assoc.release(); logger.info("Association released.")


# --- C-GET SCU ---
def _on_get_response(event: evt.Event, output_directory: str) -> int: # For C-GET's C-STORE sub-op
    """Handler for EVT_C_STORE during C-GET. Saves received DICOM dataset."""
    dataset = event.dataset
    if not dataset: logger.error("C-STORE sub-op (from C-GET) failed: No dataset."); return 0x0000
    dataset.is_little_endian = True; dataset.is_implicit_VR = True
    try:
        filename = os.path.join(output_directory, f"{dataset.SOPInstanceUID}.dcm")
        dcmwrite(filename, dataset, write_like_original=False)
        logger.info(f"Stored: {filename} (from C-GET)")
        return 0x0000 # Success for C-STORE sub-operation
    except Exception as e: logger.error(f"Failed to save DICOM from C-GET: {e}"); return 0xA700

def perform_c_get(
    calling_aet: str, peer_aet: str, peer_host: str, peer_port: int, output_directory: str,
    patient_id: str = "", study_uid: str = "", series_uid: str = "", sop_instance_uid: str = ""
) -> None:
    """Performs a DICOM C-GET operation."""
    logger.info(f"Performing C-GET from {peer_aet}@{peer_host}:{peer_port} (AET {calling_aet}), output: {output_directory}")
    os.makedirs(output_directory, exist_ok=True)
    
    query_level = "PATIENT"
    if sop_instance_uid: query_level = "IMAGE"
    elif series_uid: query_level = "SERIES"
    elif study_uid: query_level = "STUDY"
    identifier_dataset = _build_query_dataset_from_params(query_level, patient_id, study_uid, series_uid, sop_instance_uid)
    if query_level == "IMAGE" and not identifier_dataset.SOPInstanceUID:
        raise InvalidInputError("SOPInstanceUID required for IMAGE level C-GET.")

    model = _get_query_model(query_level, "GET")
    event_handlers = [(evt.EVT_C_STORE, partial(_on_get_response, output_directory=output_directory))]
    contexts = [model] + [ctx for ctx in StoragePresentationContexts if ctx is not None]
    assoc = None
    try:
        assoc = _establish_association(calling_aet, peer_aet, peer_host, peer_port, contexts, event_handlers)
        final_status_data = None
        for status_rsp, _ in assoc.send_c_get(identifier_dataset, model): # ds_rsp often None for C-GET
            if status_rsp: final_status_data = status_rsp
            else: logger.warning("C-GET intermediate response missing status.")

        if not final_status_data: raise DicomOperationError("C-GET failed: No final status from SCP.")
        
        status_code = final_status_data.Status
        completed = getattr(final_status_data, 'NumberOfCompletedSuboperations', 0)
        failed = getattr(final_status_data, 'NumberOfFailedSuboperations', 0)
        warnings = getattr(final_status_data, 'NumberOfWarningSuboperations', 0)
        logger.info(f"C-GET Final Status: 0x{status_code:04X}, Completed: {completed}, Failed: {failed}, Warnings: {warnings}")

        if status_code != 0x0000:
            err_cmt = getattr(final_status_data, "ErrorComment", "N/A")
            raise DicomOperationError(f"C-GET failed with status 0x{status_code:04X}. Comment: {err_cmt}", status=status_code)
        if failed > 0:
            raise DicomOperationError(f"{failed} sub-operations failed during C-GET.", status=0x0000) # Overall C-GET might be success
        if query_level == "IMAGE" and completed == 0 and failed == 0:
             logger.warning(f"C-GET for {sop_instance_uid} had 0 completed/failed ops. File might not exist or wasn't sent.")
        logger.info("C-GET operation completed from SCU perspective.")
    finally:
        if assoc and assoc.is_established: assoc.release(); logger.info("Association released.")

def _handle_get_scu(args: argparse.Namespace): # Kept for CLI
    """Handles the C-GET SCU operation for CLI."""
    logger.info(f"CLI: C-GET from {args.aec}@{args.host}:{args.port} (AET {args.aet}), output: {args.out_dir}")
    try:
        perform_c_get(args.aet, args.aec, args.host, args.port, args.out_dir,
                        args.patient_id, args.study_uid, args.series_uid, args.sop_instance_uid)
        logger.info("CLI C-GET successful.")
    except (DicomConnectionError, DicomOperationError, InvalidInputError) as e: logger.error(f"CLI C-GET failed: {e}"); raise


# --- C-STORE SCU ---
_COMMON_STORAGE_CONTEXTS = list(dict.fromkeys(StoragePresentationContexts + [
    sop_class.RTImageStorage, sop_class.PositronEmissionTomographyImageStorage,
    sop_class.UltrasoundMultiFrameImageStorage,
]))

def _on_store_response(event: evt.Event): # For CLI logging
    """Callback handler for C-STORE responses."""
    status = event.status
    sop_uid = getattr(event.context.dataset, "SOPInstanceUID", "Unknown SOP") if event.context and event.context.dataset else "Unknown SOP"
    if status.Status == 0x0000: logger.info(f"C-STORE success for {sop_uid}")
    else: logger.error(f"C-STORE failed for {sop_uid}: Status 0x{status.Status:04X} (Comment: {status.ErrorComment or 'N/A'})")

def _get_dicom_files_from_path(filepath: str) -> List[str]:
    """Scans and returns a list of valid DICOM file paths."""
    if not os.path.exists(filepath): raise InvalidInputError(f"Path not found: {filepath}")
    dicom_files: List[str] = []
    if os.path.isfile(filepath):
        try: dcmread(filepath, stop_before_pixels=True); dicom_files.append(filepath)
        except Exception as e: logger.warning(f"Not a readable DICOM file: {filepath} ({e})")
    elif os.path.isdir(filepath):
        for root, _, files in os.walk(filepath):
            for file in files:
                full_path = os.path.join(root, file)
                try: dcmread(full_path, stop_before_pixels=True); dicom_files.append(full_path)
                except Exception: logger.debug(f"Skipping non-DICOM: {full_path}")
    if not dicom_files: raise InvalidInputError(f"No valid DICOM files at: {filepath}")
    logger.info(f"Found {len(dicom_files)} DICOM file(s) from {filepath}.")
    return dicom_files

def perform_c_store(
    calling_aet: str, peer_aet: str, peer_host: str, peer_port: int, dicom_filepaths: List[str]
) -> Tuple[int, int]:
    """Performs DICOM C-STORE for a list of files."""
    logger.info(f"C-STORE to {peer_aet}@{peer_host}:{peer_port} from {calling_aet} ({len(dicom_filepaths)} files)")
    for fpath in dicom_filepaths:
        if not os.path.exists(fpath) or not os.path.isfile(fpath):
            raise InvalidInputError(f"File not found: {fpath}")

    assoc = None
    successful_stores, failed_stores = 0, 0
    try:
        assoc = _establish_association(calling_aet, peer_aet, peer_host, peer_port,
                                       _COMMON_STORAGE_CONTEXTS,
                                       event_handlers=[(evt.EVT_C_STORE_RSP, _on_store_response)])
        for fpath in dicom_filepaths:
            try:
                ds = dcmread(fpath)
                status_rsp = assoc.send_c_store(ds)
                if status_rsp and (status_rsp.Status == 0x0000 or \
                                   (status_rsp.Status >= 0xB000 and status_rsp.Status <= 0xBFFF)): # Success or Warning
                    successful_stores += 1
                    if status_rsp.Status != 0x0000: logger.warning(f"C-STORE for {fpath} warning status: 0x{status_rsp.Status:04X}")
                else:
                    failed_stores += 1
                    err_msg = f"C-STORE for {fpath} failed DIMSE status: 0x{status_rsp.Status:04X}" if status_rsp else "No DIMSE status"
                    if status_rsp and hasattr(status_rsp, "ErrorComment"): err_msg += f" ({status_rsp.ErrorComment})"
                    logger.error(err_msg)
            except Exception as e: failed_stores += 1; logger.error(f"Error storing {fpath}: {e}", exc_info=True)
        
        if dicom_filepaths and successful_stores == 0 and failed_stores == len(dicom_filepaths):
            raise DicomOperationError(f"All {len(dicom_filepaths)} C-STOREs failed at DIMSE/local level.")
        return successful_stores, failed_stores
    finally:
        if assoc and assoc.is_established: assoc.release(); logger.info("Association released.")

def _handle_store_scu(args: argparse.Namespace): # Kept for CLI
    """Handles C-STORE SCU for CLI."""
    logger.info(f"CLI: C-STORE to {args.aec}@{args.host}:{args.port} from {args.aet}")
    try:
        files = _get_dicom_files_from_path(args.filepath)
        ok, fail = perform_c_store(args.aet, args.aec, args.host, args.port, files)
        logger.info(f"CLI C-STORE summary: Success/Warning: {ok}, Failed/Error: {fail}")
        if files and ok == 0 and fail == len(files): raise DicomOperationError("All CLI C-STOREs failed.")
        elif fail > 0: logger.warning(f"{fail} files had issues during CLI C-STORE.")
    except (DicomConnectionError, DicomOperationError, InvalidInputError) as e: logger.error(f"CLI C-STORE failed: {e}"); raise


# --- Argument Parsing and Main Function ---
def _setup_parsers() -> argparse.ArgumentParser:
    """Sets up and returns the main argument parser with subparsers."""
    parser = argparse.ArgumentParser(description="DICOM Network Utility Tool.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging.")
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument("--aet", default="DICOMUTILS", help="Calling AE Title.")
    common_parser.add_argument("--aec", required=True, help="Called AE Title (SCP).")
    common_parser.add_argument("--host", required=True, help="Hostname/IP of SCP.")
    common_parser.add_argument("--port", required=True, type=int, help="Port of SCP.")
    subparsers = parser.add_subparsers(title="Commands", dest="command", required=True)

    echo_parser = subparsers.add_parser("echo", help="C-ECHO.", parents=[common_parser])
    echo_parser.set_defaults(func=_handle_echo_scu)

    find_parser = subparsers.add_parser("find", help="C-FIND.", parents=[common_parser])
    find_parser.add_argument("--query-level", default="STUDY", choices=["PATIENT", "STUDY", "SERIES", "IMAGE"])
    find_parser.add_argument("--patient-id", default="*", help="Patient ID.") # Changed default to *
    find_parser.add_argument("--study-uid", default="", help="Study UID.")
    find_parser.add_argument("--series-uid", default="", help="Series UID.")
    find_parser.add_argument("--sop-instance-uid", default="", help="SOP Instance UID (for IMAGE level).")
    find_parser.add_argument("--modality", default="", help="Modality.")
    find_parser.set_defaults(func=_handle_find_scu)

    move_parser = subparsers.add_parser("move", help="C-MOVE.", parents=[common_parser])
    move_parser.add_argument("--move-dest-aet", required=True, help="Move Destination AET.")
    move_parser.add_argument("--query-level", default="STUDY", choices=["PATIENT", "STUDY", "SERIES", "IMAGE"]) # Added IMAGE
    move_parser.add_argument("--patient-id", help="Patient ID for move.")
    move_parser.add_argument("--study-uid", help="Study UID for move.")
    move_parser.add_argument("--series-uid", help="Series UID for move.")
    move_parser.add_argument("--sop-instance-uid", help="SOP UID for IMAGE level move.") # Added for IMAGE level move
    move_parser.set_defaults(func=_handle_move_scu)

    store_parser = subparsers.add_parser("store", help="C-STORE.", parents=[common_parser])
    store_parser.add_argument("--filepath", required=True, help="Path to DICOM file/directory.")
    store_parser.set_defaults(func=_handle_store_scu)

    get_parser = subparsers.add_parser("get", help="C-GET.", parents=[common_parser])
    get_parser.add_argument("--patient-id", default="", help="Patient ID for C-GET.")
    get_parser.add_argument("--study-uid", default="", help="Study UID for C-GET.")
    get_parser.add_argument("--series-uid", default="", help="Series UID for C-GET.")
    get_parser.add_argument("--sop-instance-uid", default="", help="SOP UID for C-GET.")
    get_parser.add_argument("--out-dir", required=True, help="Output directory for C-GET.")
    get_parser.set_defaults(func=_handle_get_scu)
    return parser

def main_cli(): # Renamed from main to main_cli to avoid confusion if imported
    """Main function for CLI argument parsing and dispatching."""
    parser = _setup_parsers()
    args = parser.parse_args()

    if args.verbose: logger.setLevel(logging.DEBUG); debug_logger()
    else:
        if logger.level == logging.NOTSET or logger.level > logging.INFO: # Ensure INFO if not set lower
            logger.setLevel(logging.INFO)
    
    # Configure root logger if no handlers are present, for CLI basic output
    if not logging.getLogger().hasHandlers():
         logging.basicConfig(level=logger.level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", stream=sys.stdout)

    if hasattr(args, "func"):
        try:
            args.func(args)
        except DicomUtilsError as e:
            print(f"Error: {e}", file=sys.stderr) # Already logged, this is for user feedback
            raise # Re-raise for the __main__ block to handle exit code
        except Exception as e:
            logger.critical(f"Unexpected critical error: {e}", exc_info=True)
            print(f"Unexpected critical error: {e}", file=sys.stderr)
            raise DicomUtilsError(f"Unexpected critical error: {e}") from e
    else:
        parser.print_help(sys.stderr)
        raise InvalidInputError("No command provided.")

if __name__ == "__main__":
    try:
        main_cli()
        sys.exit(0)
    except DicomUtilsError: # Handles errors raised and re-raised by main_cli
        sys.exit(1)
    except Exception: # Catch any truly unexpected errors not wrapped by DicomUtilsError
        sys.exit(2) # Different exit code for truly unexpected
