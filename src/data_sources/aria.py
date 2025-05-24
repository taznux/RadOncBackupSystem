from . import DataSource
from pydicom.dataset import Dataset
from pynetdicom import AE, evt, StoragePresentationContexts
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind, StudyRootQueryRetrieveInformationModelMove
import logging

logger = logging.getLogger(__name__)

class ARIA(DataSource):
    """
    Represents the ARIA data source system.

    This class provides methods to query and transfer data (e.g., RTRECORD series)
    from an ARIA DICOM node using C-FIND and C-MOVE operations.
    """
    def __init__(self):
        """
        Initializes the ARIA data source interface.
        """
        super().__init__()
        logger.debug("ARIA DataSource initialized.")

    def query(self, query_dataset: Dataset, qr_scp: dict) -> set:
        """
        Performs a C-FIND query against the ARIA system.

        :param query_dataset: A pydicom Dataset object containing query parameters
                              (e.g., PatientID, StudyDate, Modality).
                              Typically, QueryRetrieveLevel should be set (e.g., 'SERIES').
        :type query_dataset: pydicom.dataset.Dataset
        :param qr_scp: A dictionary containing the Query/Retrieve SCP details:
                       {'IP': 'host_ip', 'Port': port_number, 'AETitle': 'AE_TITLE'}
        :type qr_scp: dict
        :return: A set of SOPInstanceUIDs found matching the query criteria.
                 Returns an empty set if no matches are found or if an error occurs.
        :rtype: set
        """
        ae = AE()
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)
        uids = set()
        
        logger.info(f"Attempting C-FIND association to ARIA QR SCP: {qr_scp['AETitle']} at {qr_scp['IP']}:{qr_scp['Port']}")
        assoc = ae.associate(qr_scp['IP'], qr_scp['Port'], ae_title=qr_scp['AETitle'])
        
        if assoc.is_established:
            logger.info("C-FIND Association established with ARIA.")
            try:
                responses = assoc.send_c_find(query_dataset, StudyRootQueryRetrieveInformationModelFind)
                for (status, identifier) in responses:
                    if status and (status.Status == 0xFF00 or status.Status == 0xFF01): # Pending
                        if identifier and hasattr(identifier, 'SOPInstanceUID'):
                            logger.debug(f"C-FIND Pending: Found SOPInstanceUID {identifier.SOPInstanceUID}")
                            uids.add(identifier.SOPInstanceUID)
                        else:
                            logger.debug("C-FIND Pending status with no valid identifier.")
                    elif status and status.Status == 0x0000: # Success
                        if identifier and hasattr(identifier, 'SOPInstanceUID'): # Should ideally be no identifier for final success
                            logger.debug(f"C-FIND Success: Found SOPInstanceUID {identifier.SOPInstanceUID}")
                            uids.add(identifier.SOPInstanceUID)
                        logger.info("C-FIND operation completed successfully.")
                        break 
                    else: # Failure or unknown status
                        error_msg = "C-FIND query failed or connection issue."
                        if status:
                            error_msg += f" Status: 0x{status.Status:04X}."
                        else:
                            error_msg += " No status returned."
                        logger.error(error_msg)
                        break 
            except Exception as e:
                logger.error(f"Exception during C-FIND operation: {e}", exc_info=True)
            finally:
                logger.debug("Releasing C-FIND association with ARIA.")
                assoc.release()
        else:
            logger.error(f"C-FIND Association rejected, aborted or never connected to ARIA SCP: {qr_scp['AETitle']}.")
        
        logger.info(f"C-FIND query to ARIA found {len(uids)} SOPInstanceUIDs.")
        return uids

    def transfer(self, move_dataset: Dataset, qr_scp: dict, store_scp: dict, handle_store: callable):
        """
        Performs a C-MOVE operation to retrieve data from ARIA.
        This method starts a temporary local C-STORE SCP to receive the data moved from ARIA.

        :param move_dataset: A pydicom Dataset containing parameters for the C-MOVE request
                             (e.g., PatientID, StudyInstanceUID, SeriesInstanceUID).
                             QueryRetrieveLevel must be set.
        :type move_dataset: pydicom.dataset.Dataset
        :param qr_scp: Dictionary with ARIA QR SCP details: {'IP', 'Port', 'AETitle'}.
        :type qr_scp: dict
        :param store_scp: Dictionary for the local C-STORE SCP: {'IP', 'Port'}.
                          The AE Title for this local SCP will be 'ARIA_SCP_TEMP'.
        :type store_scp: dict
        :param handle_store: Event handler (callable) for EVT_C_STORE, e.g., def handle_store(event): ...
        :type handle_store: callable
        :raises Exception: Network or DICOM protocol errors.
        """
        
        # Define a temporary AE Title for our local C-STORE SCP.
        # This SCP will be started to receive the data from the C-MOVE operation.
        local_scp_aet = "ARIA_SCP_TEMP"
        ae = AE(ae_title=local_scp_aet)
        
        # Add requested context for C-MOVE SCU operation
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelMove)
        
        # Configure supported contexts for the local C-STORE SCP part.
        # This allows our local SCP to accept storage of various DICOM objects.
        ae.supported_contexts = StoragePresentationContexts
        
        # Register the event handler for incoming C-STORE requests (data being received)
        handlers = [(evt.EVT_C_STORE, handle_store)]
        
        logger.info(f"Starting temporary C-STORE SCP on {store_scp['IP']}:{store_scp['Port']} with AET {local_scp_aet} for C-MOVE.")
        # Start the local SCP server in non-blocking mode.
        scp_server = ae.start_server((store_scp['IP'], store_scp['Port']), block=False, evt_handlers=handlers)

        logger.info(f"Attempting C-MOVE association to ARIA QR SCP: {qr_scp['AETitle']} at {qr_scp['IP']}:{qr_scp['Port']}")
        # Associate with the remote ARIA C-MOVE SCP.
        assoc = ae.associate(qr_scp['IP'], qr_scp['Port'], ae_title=qr_scp['AETitle'])
        
        if assoc.is_established:
            logger.info(f"C-MOVE Association established with ARIA. Sending C-MOVE request, destination AET: {local_scp_aet}.")
            try:
                # Send the C-MOVE request, telling ARIA to send data to our local_scp_aet.
                responses = assoc.send_c_move(move_dataset, local_scp_aet, StudyRootQueryRetrieveInformationModelMove)
                for (status, identifier) in responses: 
                    if status is None:
                        logger.error("C-MOVE failed: Connection timed out, aborted or received invalid response from ARIA.")
                        # No identifier means no further info from this response
                        break 
                    if status.Status == 0xFF00 or status.Status == 0xFF01: # Pending
                        logger.info(f"C-MOVE pending: {status.NumberOfRemainingSuboperations} remaining, "
                                    f"{status.NumberOfCompletedSuboperations} completed, "
                                    f"{status.NumberOfWarningSuboperations} warnings, "
                                    f"{status.NumberOfFailedSuboperations} failures.")
                    elif status.Status == 0x0000: # Success
                        logger.info("C-MOVE operation completed successfully from ARIA's perspective.")
                        logger.info(f"C-MOVE final status: {status.NumberOfCompletedSuboperations} completed, "
                                    f"{status.NumberOfWarningSuboperations} warnings, "
                                    f"{status.NumberOfFailedSuboperations} failures.")
                        break # Success means the operation is complete
                    else: # Failure or other status
                        error_message = f"C-MOVE operation failed. Status: 0x{status.Status:04X}"
                        if hasattr(status, 'ErrorComment') and status.ErrorComment:
                            error_message += f" Error Comment: {status.ErrorComment}"
                        logger.error(error_message)
                        # Log affected SOP Instance UID if available in the identifier
                        if identifier and hasattr(identifier, 'AffectedSOPInstanceUID'):
                            logger.error(f"Affected SOP Instance UID: {identifier.AffectedSOPInstanceUID}")
                        break # Operation terminated due to failure
            except Exception as e:
                logger.error(f"Exception during C-MOVE operation: {e}", exc_info=True)
            finally:
                logger.debug("Releasing C-MOVE association with ARIA.")
                assoc.release()
        else:
            logger.error(f"C-MOVE Association rejected, aborted or never connected to ARIA SCP: {qr_scp['AETitle']}.")
        
        logger.debug("Shutting down temporary C-STORE SCP.")
        scp_server.shutdown()
        logger.info("Temporary C-STORE SCP shut down.")
