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
import click # Added click
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

# Removed old default handler setup; click app will handle basicConfig
# if not logger.handlers:
#     logger.addHandler(logging.StreamHandler(sys.stdout))
#     logger.setLevel(logging.INFO)

def setup_logging_for_dicom_utils(verbose: bool):
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.getLogger("pynetdicom").setLevel(log_level) # pynetdicom's own logger
    logger.setLevel(log_level)
    if verbose:
        debug_logger() # pynetdicom's verbose logging


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
@click.command("echo", help="Perform a DICOM C-ECHO.")
@click.option("--aet", default="DICOMUTILS", show_default=True, help="Calling AE Title.")
@click.option("--aec", required=True, help="Called AE Title (SCP).")
@click.option("--host", required=True, help="Hostname/IP of SCP.")
@click.option("--port", required=True, type=int, help="Port of SCP.")
@click.pass_context
def c_echo_cli(ctx, aet: str, aec: str, host: str, port: int) -> None:
    """Performs a DICOM C-ECHO operation."""
    # setup_logging_for_dicom_utils(ctx.obj.get('VERBOSE', False)) # Already called by main robs command
    logger.info(
        f"Performing C-ECHO to {aec} at {host}:{port} from AET {aet}"
    )
    assoc = None
    try:
        assoc = _establish_association(
            aet, aec, host, port, [sop_class.Verification]
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

@click.command("find", help="Perform a DICOM C-FIND.")
@click.option("--aet", default="DICOMUTILS", show_default=True, help="Calling AE Title.")
@click.option("--aec", required=True, help="Called AE Title (SCP).")
@click.option("--host", required=True, help="Hostname/IP of SCP.")
@click.option("--port", required=True, type=int, help="Port of SCP.")
@click.option("--query-level", default="STUDY", type=click.Choice(["PATIENT", "STUDY", "SERIES", "IMAGE"], case_sensitive=False), show_default=True)
@click.option("--patient-id", default="*", show_default=True, help="Patient ID.")
@click.option("--study-uid", default="", help="Study Instance UID.")
@click.option("--series-uid", default="", help="Series Instance UID.")
@click.option("--sop-instance-uid", default="", help="SOP Instance UID (for IMAGE level).")
@click.option("--modality", default="", help="Modality.")
@click.pass_context
def c_find_cli(
    ctx, aet: str, aec: str, host: str, port: int, query_level: str,
    patient_id: str = "*", study_uid: str = "", series_uid: str = "",
    sop_instance_uid: str = "", modality: str = ""
) -> List[Dataset]:
    """Performs a DICOM C-FIND operation."""
    # setup_logging_for_dicom_utils(ctx.obj.get('VERBOSE', False)) # Called by main robs group
    logger.info(
        f"Performing C-FIND to {aec} at {host}:{port} (AET {aet}) "
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
        assoc = _establish_association(aet, aec, host, port, [model])
        responses = assoc.send_c_find(query_dataset, model)
        last_status = None
        for status_rsp, identifier_rsp in responses:
            last_status = status_rsp.Status
            if status_rsp.Status in (0xFF00, 0xFF01): # Pending
                if identifier_rsp:
                    found_identifiers.append(identifier_rsp)
            elif status_rsp.Status == 0x0000: # Success
                if identifier_rsp: found_identifiers.append(identifier_rsp)
                logger.info(f"C-FIND operation with {aec} completed successfully.")
                break
            else: # Failure
                error_msg = f"C-FIND failed with status 0x{status_rsp.Status:04X}"
                comment = getattr(identifier_rsp, "ErrorComment", getattr(status_rsp, "ErrorComment", None))
                if comment: error_msg += f" - Error Comment: {comment}"
                raise DicomOperationError(error_msg, status=status_rsp.Status)

        if not found_identifiers and last_status == 0x0000 : # Success but no results
            logger.info("C-FIND successful, but no matching instances found.")
        elif found_identifiers:
            logger.info(f"C-FIND found {len(found_identifiers)} matching instance(s).")
            for i, ds in enumerate(found_identifiers):
                logger.info(f"  Result {i+1}: PatientID={ds.get('PatientID', 'N/A')}, "
                            f"StudyUID={ds.get('StudyInstanceUID', 'N/A')}, "
                            f"SeriesUID={ds.get('SeriesInstanceUID', 'N/A')}, "
                            f"SOP_UID={ds.get('SOPInstanceUID', 'N/A')}")
        return found_identifiers
    finally:
        if assoc and assoc.is_established: assoc.release(); logger.info("Association released after C-FIND.")


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

@click.command("move", help="Perform a DICOM C-MOVE.")
@click.option("--aet", default="DICOMUTILS", show_default=True, help="Calling AE Title.")
@click.option("--aec", required=True, help="Called AE Title (SCP).")
@click.option("--host", required=True, help="Hostname/IP of SCP.")
@click.option("--port", required=True, type=int, help="Port of SCP.")
@click.option("--move-dest-aet", required=True, help="Move Destination AE Title.")
@click.option("--query-level", default="STUDY", type=click.Choice(["PATIENT", "STUDY", "SERIES", "IMAGE"], case_sensitive=False), show_default=True)
@click.option("--patient-id", default="*", help="Patient ID for move.") # Defaulting to * might be broad, consider removing default or making it more specific.
@click.option("--study-uid", default="", help="Study Instance UID for move.")
@click.option("--series-uid", default="", help="Series Instance UID for move.")
@click.option("--sop-instance-uid", default="", help="SOP Instance UID (for IMAGE level move).")
@click.pass_context
def c_move_cli(
    ctx, aet: str, aec: str, host: str, port: int, move_dest_aet: str,
    query_level: str, patient_id: str, study_uid: str, series_uid: str, sop_instance_uid: str
):
    """Handles the C-MOVE SCU operation for CLI."""
    # setup_logging_for_dicom_utils(ctx.obj.get('VERBOSE', False)) # Called by main robs group
    logger.info(f"CLI: C-MOVE to {aec}@{host}:{port}, dest AET: {move_dest_aet}")
    identifier_dataset = _build_query_dataset_from_params(
        query_level, patient_id, study_uid, series_uid,
        sop_instance_uid if query_level == "IMAGE" else ""
    )
    if query_level == "IMAGE" and not identifier_dataset.SOPInstanceUID:
        raise InvalidInputError("SOPInstanceUID is required for IMAGE level C-MOVE.")

    model = _get_query_model(identifier_dataset.QueryRetrieveLevel, "MOVE")
    assoc = None
    try:
        assoc = _establish_association(aet, aec, host, port, [model],
                                       event_handlers=[(evt.EVT_C_MOVE_RSP, _on_move_response)])
        final_status = None
        for status_rsp, _ in assoc.send_c_move(identifier_dataset, move_dest_aet, model):
            if status_rsp: final_status = status_rsp.Status
            else: raise DicomOperationError("C-MOVE failed: No/invalid intermediate status from SCP.")

        if final_status != 0x0000:
            raise DicomOperationError(f"C-MOVE failed with final status 0x{final_status:04X}", status=final_status)
        logger.info("CLI C-MOVE operation reported success by SCP.")
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

@click.command("get", help="Perform a DICOM C-GET.")
@click.option("--aet", default="DICOMUTILS", show_default=True, help="Calling AE Title.")
@click.option("--aec", required=True, help="Called AE Title (SCP).")
@click.option("--host", required=True, help="Hostname/IP of SCP.")
@click.option("--port", required=True, type=int, help="Port of SCP.")
@click.option("--out-dir", "output_directory", required=True, type=click.Path(file_okay=False, dir_okay=True, writable=True, resolve_path=True), help="Output directory for C-GET.")
@click.option("--patient-id", default="", help="Patient ID for C-GET.")
@click.option("--study-uid", default="", help="Study Instance UID for C-GET.")
@click.option("--series-uid", default="", help="Series Instance UID for C-GET.")
@click.option("--sop-instance-uid", default="", help="SOP Instance UID for C-GET.")
@click.pass_context
def c_get_cli(
    ctx, aet: str, aec: str, host: str, port: int, output_directory: str,
    patient_id: str = "", study_uid: str = "", series_uid: str = "", sop_instance_uid: str = ""
) -> None:
    """Performs a DICOM C-GET operation."""
    # setup_logging_for_dicom_utils(ctx.obj.get('VERBOSE', False)) # Called by main robs group
    logger.info(f"Performing C-GET from {aec}@{host}:{port} (AET {aet}), output: {output_directory}")
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
    contexts = [model] + [ctx_item for ctx_item in StoragePresentationContexts if ctx_item is not None] # Renamed ctx to ctx_item to avoid conflict
    assoc = None
    try:
        assoc = _establish_association(aet, aec, host, port, contexts, event_handlers)
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

@click.command("store", help="Perform DICOM C-STORE for file(s).")
@click.option("--aet", default="DICOMUTILS", show_default=True, help="Calling AE Title.")
@click.option("--aec", required=True, help="Called AE Title (SCP).")
@click.option("--host", required=True, help="Hostname/IP of SCP.")
@click.option("--port", required=True, type=int, help="Port of SCP.")
@click.option("--filepath", required=True, type=click.Path(exists=True, readable=True), help="Path to DICOM file or directory.")
@click.pass_context
def c_store_cli(
    ctx, aet: str, aec: str, host: str, port: int, filepath: str
) -> Tuple[int, int]:
    """Performs DICOM C-STORE for a list of files."""
    setup_logging_for_dicom_utils(ctx.obj.get('VERBOSE', False))
    dicom_files = _get_dicom_files_from_path(filepath) # Handles path validation

    logger.info(f"C-STORE to {aec}@{host}:{port} from {aet} ({len(dicom_files)} files)")

    assoc = None
    successful_stores, failed_stores = 0, 0
    try:
        assoc = _establish_association(aet, aec, host, port,
                                       _COMMON_STORAGE_CONTEXTS,
                                       event_handlers=[(evt.EVT_C_STORE_RSP, _on_store_response)])
        for fpath in dicom_files:
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
        
        if dicom_files and successful_stores == 0 and failed_stores == len(dicom_files):
            logger.error(f"All {len(dicom_files)} C-STOREs failed at DIMSE/local level.")
            # Depending on desired CLI behavior, could raise DicomOperationError here.

        logger.info(f"C-STORE summary: Success/Warning: {successful_stores}, Failed/Error: {failed_stores}")
        if failed_stores > 0:
            logger.warning(f"{failed_stores} files had issues during C-STORE.")

        return successful_stores, failed_stores
    finally:
        if assoc and assoc.is_established: assoc.release(); logger.info("Association released.")

# Click group for dicom utilities
@click.group("dicom", help="DICOM network operations (C-ECHO, C-FIND, C-MOVE, C-STORE, C-GET).")
def dicom_cli_group():
    """Group for DICOM utilities."""
    pass

# The main `robs` group in `src/cli/main.py` will add `dicom_cli_group`.
# Example:
# from . import dicom_utils
# robs.add_command(dicom_utils.dicom_cli_group)

dicom_cli_group.add_command(c_echo_cli)
dicom_cli_group.add_command(c_find_cli)
dicom_cli_group.add_command(c_move_cli)
dicom_cli_group.add_command(c_get_cli)
dicom_cli_group.add_command(c_store_cli)
