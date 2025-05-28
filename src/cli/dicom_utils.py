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

from pynetdicom import AE, debug_logger, evt
from pynetdicom.sop_class import (
    VerificationSOPClass,
    PatientRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelFind,
    PatientStudyOnlyQueryRetrieveInformationModelFind, # Common Q/R model
    PatientRootQueryRetrieveInformationModelMove,
    StudyRootQueryRetrieveInformationModelMove,
    PatientStudyOnlyQueryRetrieveInformationModelMove, # Common Q/R model
    ModalityWorklistInformationModelFind, # Example, not primary for this tool
    RTPlanStorage, # Example storage SOP class, will need a list
    CTImageStorage, # Example storage SOP class
    MRImageStorage, # Example storage SOP class
    RTStructureSetStorage,
    RTDoseStorage,
    RTBeamsTreatmentRecordStorage,
    # Add more storage SOP classes as needed for C-STORE
)
from pynetdicom.presentation import PresentationContext
from pydicom import dcmread
from pydicom.dataset import Dataset

# Setup basic logging
logger = logging.getLogger('dicom_utils')
logger.addHandler(logging.StreamHandler(sys.stdout))
# Set level via command line for pynetdicom internal logs
# debug_logger() # Uncomment for pynetdicom verbose logs

# --- C-ECHO SCU ---
def handle_echo(args):
    logger.info(f"Performing C-ECHO to {args.aec} at {args.host}:{args.port} from AET {args.aet}")
    ae = AE(ae_title=args.aet)
    ae.add_requested_context(VerificationSOPClass)
    
    assoc = ae.associate(args.host, args.port, ae_title=args.aec)
    if assoc.is_established:
        logger.info("Association established.")
        status = assoc.send_c_echo()
        if status:
            logger.info(f"C-ECHO status: 0x{status.Status:04X} ({status.StatusDescription})")
        else:
            logger.error("C-ECHO failed: No response from SCP.")
        assoc.release()
        logger.info("Association released.")
    else:
        logger.error(f"Association failed for C-ECHO: {assoc.acceptor.primitive.result_str}")

# --- C-FIND SCU ---
def handle_find_response(status, identifier, ae_title):
    """Handler for C-FIND responses."""
    if status.Status in (0xFF00, 0xFF01):  # Pending
        if identifier:
            logger.info(f"C-FIND RSP from {ae_title}: Pending - Found identifier:")
            # Print the identifier dataset content
            for elem in identifier:
                 logger.info(f"  {elem.name}: {elem.value}")
        else:
            logger.info(f"C-FIND RSP from {ae_title}: Pending - No identifier data in this response.")
        return True # Continue C-FIND
    elif status.Status == 0x0000: # Success
        logger.info(f"C-FIND RSP from {ae_title}: Success - Final result.")
        if identifier:
            logger.info("Final identifier data (if any):")
            for elem in identifier:
                 logger.info(f"  {elem.name}: {elem.value}")
        return False # Stop C-FIND
    else: # Failure, Cancel, etc.
        logger.error(f"C-FIND RSP from {ae_title}: Error - Status 0x{status.Status:04X} ({status.StatusDescription})")
        if identifier and hasattr(identifier, 'ErrorComment'):
            logger.error(f"  Error Comment: {identifier.ErrorComment}")
        return False # Stop C-FIND

def handle_find(args):
    logger.info(f"Performing C-FIND to {args.aec} at {args.host}:{args.port} from AET {args.aet}")
    ae = AE(ae_title=args.aet)

    # Determine Query/Retrieve model based on query level or specific args
    # For simplicity, using PatientRoot as default, can be expanded
    # Or select based on --query-level
    if args.query_level == "PATIENT":
        model = PatientRootQueryRetrieveInformationModelFind
    elif args.query_level == "STUDY":
        model = StudyRootQueryRetrieveInformationModelFind # Or PatientStudyOnly if preferred
    elif args.query_level == "SERIES":
        model = StudyRootQueryRetrieveInformationModelFind # Series level implies starting from Study usually
    elif args.query_level == "IMAGE":
        model = StudyRootQueryRetrieveInformationModelFind # Image level implies starting from Study usually
    else: # Default
        model = PatientRootQueryRetrieveInformationModelFind

    ae.add_requested_context(model)
    
    ds = Dataset()
    ds.QueryRetrieveLevel = args.query_level
    
    # Populate dataset based on provided arguments
    if args.patient_id:
        ds.PatientID = args.patient_id
    else:
        ds.PatientID = '*' # Wildcard if not specified for broader levels
        
    if args.study_uid:
        ds.StudyInstanceUID = args.study_uid
    else:
        ds.StudyInstanceUID = '' # Request this field
        
    if args.series_uid:
        ds.SeriesInstanceUID = args.series_uid
    else:
        ds.SeriesInstanceUID = '' # Request this field

    if args.modality:
        ds.Modality = args.modality
    else:
        # Don't set Modality if not specified, to allow wildcard search for it
        pass

    # Request specific return keys (can be more comprehensive)
    ds.PatientName = '*'
    ds.StudyDate = ''
    ds.StudyTime = ''
    ds.AccessionNumber = ''
    ds.SOPInstanceUID = '' # For IMAGE level
    ds.SeriesNumber = ''
    ds.InstanceNumber = ''


    assoc = ae.associate(args.host, args.port, ae_title=args.aec)
    if assoc.is_established:
        logger.info("Association established for C-FIND.")
        # responses is a generator
        responses = assoc.send_c_find(ds, model)
        for status, identifier in responses:
            if not handle_find_response(status, identifier, args.aec):
                break # Stop if handler returns False
        assoc.release()
        logger.info("Association released.")
    else:
        logger.error(f"Association failed for C-FIND: {assoc.acceptor.primitive.result_str}")


# --- C-MOVE SCU ---
# Using a dictionary to store move state per association if needed, though for CLI one assoc at a time.
# For simplicity, we'll rely on the logger for now.
def handle_move_response(event):
    """Handler for C-MOVE interim responses (sub-operations)."""
    # event.status is the C-MOVE response status from the SCP for the overall operation.
    # event.dataset contains details about sub-operations.
    status = event.status
    dataset = event.dataset

    logger.info(f"C-MOVE Response: Status 0x{status.Status:04X} ({status.StatusDescription})")

    if dataset:
        if hasattr(dataset, 'AffectedSOPInstanceUID'):
            logger.info(f"  Affected SOP Instance UID: {dataset.AffectedSOPInstanceUID}")
        
        # These attributes are standard for C-MOVE response identifiers
        if hasattr(dataset, 'NumberOfRemainingSuboperations'):
            logger.info(f"  Remaining Sub-operations: {dataset.NumberOfRemainingSuboperations}")
        if hasattr(dataset, 'NumberOfCompletedSuboperations'):
            logger.info(f"  Completed Sub-operations: {dataset.NumberOfCompletedSuboperations}")
        if hasattr(dataset, 'NumberOfWarningSuboperations'):
            logger.info(f"  Warning Sub-operations: {dataset.NumberOfWarningSuboperations}")
        if hasattr(dataset, 'NumberOfFailedSuboperations'):
            logger.info(f"  Failed Sub-operations: {dataset.NumberOfFailedSuboperations}")
    
    if status.Status == 0x0000: # Success for this response (could be pending if sub-ops remain)
        pass # Continue processing
    elif status.Status in (0xFF00, 0xFF01): # Pending
        logger.info("  Operation is pending, more responses may follow.")
    else: # Failure or Warning for this response
        if hasattr(status, 'ErrorComment') and status.ErrorComment:
            logger.error(f"  Error Comment: {status.ErrorComment}")

def handle_move(args):
    logger.info(f"Performing C-MOVE to {args.aec} at {args.host}:{args.port}, destination AET: {args.move_dest_aet}")
    ae = AE(ae_title=args.aet)

    # Determine Query/Retrieve model
    if args.query_level == "PATIENT":
        model = PatientRootQueryRetrieveInformationModelMove
    elif args.query_level == "STUDY":
        model = StudyRootQueryRetrieveInformationModelMove
    elif args.query_level == "SERIES":
        model = StudyRootQueryRetrieveInformationModelMove # Or specific Series model if available/needed
    else: # IMAGE level move is unusual directly, typically Series or Study
        logger.error("C-MOVE at IMAGE level is not typically supported directly. Move STUDY or SERIES.")
        return

    ae.add_requested_context(model)
    
    ds = Dataset()
    ds.QueryRetrieveLevel = args.query_level
    
    if args.patient_id:
        ds.PatientID = args.patient_id
    if args.study_uid:
        ds.StudyInstanceUID = args.study_uid
    if args.series_uid: # Only relevant for Series level move
        if args.query_level == "SERIES":
            ds.SeriesInstanceUID = args.series_uid
        else:
            logger.warning("Series UID provided but query level is not SERIES. It might be ignored by SCP.")

    # Bind the handler for interim responses (C-STORE sub-operations)
    # Note: This relies on the SCP sending interim C-MOVE-RSP messages, which is optional for SCP.
    handlers = [(evt.EVT_C_MOVE_RSP, handle_move_response)]

    assoc = ae.associate(args.host, args.port, ae_title=args.aec, evt_handlers=handlers)
    if assoc.is_established:
        logger.info(f"Association established for C-MOVE. Destination: {args.move_dest_aet}")
        responses = assoc.send_c_move(ds, args.move_dest_aet, model)
        for status, identifier in responses: # Final C-MOVE response
            if status:
                logger.info(f"C-MOVE final response status: 0x{status.Status:04X} ({status.StatusDescription})")
                if hasattr(status, 'ErrorComment') and status.ErrorComment:
                    logger.error(f"  Error Comment: {status.ErrorComment}")
                if identifier:
                    logger.info("  Identifier data in final C-MOVE response:")
                    for elem in identifier:
                        logger.info(f"    {elem.name}: {elem.value}")
            else:
                logger.error("C-MOVE failed: No final response from SCP.")
        assoc.release()
        logger.info("Association released.")
    else:
        logger.error(f"Association failed for C-MOVE: {assoc.acceptor.primitive.result_str}")

# --- C-STORE SCU ---
def handle_store_response(event):
    """Handler for C-STORE responses."""
    # event.status is the C-STORE response status from the SCP
    status = event.status
    if status.Status == 0x0000:
        logger.info(f"C-STORE success for SOP Instance: {event.context.dataset.SOPInstanceUID if event.context and event.context.dataset else 'Unknown SOPInstanceUID'}")
    else:
        error_message = f"C-STORE failed for SOP Instance: {event.context.dataset.SOPInstanceUID if event.context and event.context.dataset else 'Unknown SOPInstanceUID'} with status 0x{status.Status:04X}"
        if hasattr(status, 'ErrorComment') and status.ErrorComment:
            error_message += f" - Error: {status.ErrorComment}"
        logger.error(error_message)

def handle_store(args):
    logger.info(f"Performing C-STORE to {args.aec} at {args.host}:{args.port} from AET {args.aet}")
    
    filepath = args.filepath
    if not os.path.exists(filepath):
        logger.error(f"File or directory not found: {filepath}")
        return

    dicom_files = []
    if os.path.isfile(filepath):
        dicom_files.append(filepath)
    elif os.path.isdir(filepath):
        for root, _, files in os.walk(filepath):
            for file in files:
                # Attempt to read with pydicom to see if it's a DICOM file
                # This is a basic check; could be more sophisticated (e.g., check for DICM prefix)
                try:
                    dcmread(os.path.join(root, file), stop_before_pixels=True)
                    dicom_files.append(os.path.join(root, file))
                except Exception:
                    logger.debug(f"Skipping non-DICOM file: {os.path.join(root, file)}")
        if not dicom_files:
            logger.info(f"No DICOM files found in directory: {filepath}")
            return
    
    logger.info(f"Found {len(dicom_files)} DICOM file(s) to send.")

    ae = AE(ae_title=args.aet)

    # Add all relevant storage SOP classes.
    # For a general tool, it's good to offer a wide range.
    # The SCP will only accept those it supports.
    storage_sop_classes = [
        RTPlanStorage, CTImageStorage, MRImageStorage, RTStructureSetStorage, 
        RTDoseStorage, RTBeamsTreatmentRecordStorage,
        # Add more common SOP classes
        "1.2.840.10008.5.1.4.1.1.2",  # CT Image Storage
        "1.2.840.10008.5.1.4.1.1.4",  # MR Image Storage
        "1.2.840.10008.5.1.4.1.1.481.1", # RT Plan Storage
        "1.2.840.10008.5.1.4.1.1.481.2", # RT Dose Storage
        "1.2.840.10008.5.1.4.1.1.481.3", # RT Structure Set Storage
        "1.2.840.10008.5.1.4.1.1.481.5", # RT Beams Treatment Record Storage
        # etc.
    ]
    for sop_class in storage_sop_classes:
        # Use UID strings directly for add_requested_context for broader compatibility
        # if they are not already imported pynetdicom SOPClass objects
        ae.add_requested_context(sop_class if isinstance(sop_class, str) else sop_class.UID)

    # Bind the handler for C-STORE responses
    handlers = [(evt.EVT_C_STORE_RSP, handle_store_response)]

    assoc = ae.associate(args.host, args.port, ae_title=args.aec, evt_handlers=handlers)
    if assoc.is_established:
        logger.info("Association established for C-STORE.")
        for fpath in dicom_files:
            try:
                logger.info(f"Attempting to send: {fpath}")
                ds = dcmread(fpath)
                
                # Check if the SOP Class of the file is in our requested contexts
                file_sop_class_uid = ds.SOPClassUID
                matching_contexts = [
                    ctx for ctx in ae.requested_contexts 
                    if ctx.abstract_syntax == file_sop_class_uid and ctx.transfer_syntax
                ]
                
                if not matching_contexts:
                    logger.warning(f"SOP Class {file_sop_class_uid} for file {fpath} not in requested contexts or no compatible transfer syntax. Adding it.")
                    # Add context for this specific SOP Class if not already present or if no common transfer syntax proposed
                    # This is a fallback; ideally, all common SOP classes are added beforehand.
                    ae.add_requested_context(file_sop_class_uid) 
                    # Note: This re-association might be too complex here. 
                    # A better approach is to ensure ae.requested_contexts is comprehensive.
                # The SOP Class of the file (ds.SOPClassUID) should be covered by one of the
                # abstract syntaxes in the presentation contexts negotiated by the AE.
                # pynetdicom's send_c_store will select the first supported transfer syntax
                # for the given abstract syntax (SOP Class).

                status = assoc.send_c_store(ds)
                # The actual success/failure of the C-STORE operation is asynchronous
                # and will be handled by `handle_store_response` via EVT_C_STORE_RSP.
                # The status returned here is for the send_c_store *request* itself.
                if status:
                    logger.debug(f"C-STORE request for {fpath} reported DIMSE service status: 0x{status.Status:04X}")
                    if status.Status != 0x0000 and status.Status != 0xFF00 and status.Status != 0xFF01 : # Not success or pending
                        logger.error(f"  DIMSE service error for {fpath}: {status.StatusDescription}")
                        if hasattr(status, 'ErrorComment') and status.ErrorComment:
                             logger.error(f"    Error Comment: {status.ErrorComment}")
                else:
                    # This case (status is None) usually means the association was aborted or never established.
                    logger.error(f"C-STORE request for {fpath} failed: No DIMSE status returned (association issue?).")
                    # If association drops, we should probably break the loop.
                    break 
            except Exception as e:
                logger.error(f"Error processing or sending file {fpath}: {e}", exc_info=True)
        
        assoc.release()
        logger.info("Association released.")
    else:
        logger.error(f"Association failed for C-STORE: {assoc.acceptor.primitive.result_str}")


def main():
    parser = argparse.ArgumentParser(description="DICOM Network Utility Tool.")
    parser.add_argument('--verbose', '-v', action='store_true', help="Enable verbose logging for pynetdicom.")
    
    # Common DICOM parameters group
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument('--aet', default='DICOMUTILS', help="Calling AE Title.")
    common_parser.add_argument('--aec', required=True, help="Called AE Title (SCP).")
    common_parser.add_argument('--host', required=True, help="Hostname or IP address of the SCP.")
    common_parser.add_argument('--port', required=True, type=int, help="Port number of the SCP.")

    subparsers = parser.add_subparsers(title="Commands", dest="command", required=True)

    # Echo sub-command
    echo_parser = subparsers.add_parser('echo', help="Perform C-ECHO.", parents=[common_parser])
    echo_parser.set_defaults(func=handle_echo)

    # Find sub-command
    find_parser = subparsers.add_parser('find', help="Perform C-FIND.", parents=[common_parser])
    find_parser.add_argument('--query-level', default='STUDY', choices=['PATIENT', 'STUDY', 'SERIES', 'IMAGE'], help="Query retrieve level.")
    find_parser.add_argument('--patient-id', help="Patient ID for the query.")
    find_parser.add_argument('--study-uid', help="Study Instance UID for the query.")
    find_parser.add_argument('--series-uid', help="Series Instance UID for the query.")
    find_parser.add_argument('--modality', help="Modality for the query (e.g., CT, MR, RTPLAN).")
    find_parser.set_defaults(func=handle_find)

    # Move sub-command
    move_parser = subparsers.add_parser('move', help="Perform C-MOVE.", parents=[common_parser])
    move_parser.add_argument('--move-dest-aet', required=True, help="Destination AE Title for the C-MOVE operation.")
    move_parser.add_argument('--query-level', default='STUDY', choices=['PATIENT', 'STUDY', 'SERIES'], help="Query retrieve level for selecting what to move.")
    move_parser.add_argument('--patient-id', help="Patient ID for selecting what to move.")
    move_parser.add_argument('--study-uid', help="Study Instance UID for selecting what to move.")
    move_parser.add_argument('--series-uid', help="Series Instance UID for selecting what to move (for SERIES level).")
    move_parser.set_defaults(func=handle_move)

    # Store sub-command
    store_parser = subparsers.add_parser('store', help="Perform C-STORE.", parents=[common_parser])
    store_parser.add_argument('--filepath', required=True, help="Path to a single DICOM file or a directory containing DICOM files to send.")
    store_parser.set_defaults(func=handle_store)

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)
        debug_logger() # Enable pynetdicom's verbose logging
    else:
        logger.setLevel(logging.INFO)

    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
