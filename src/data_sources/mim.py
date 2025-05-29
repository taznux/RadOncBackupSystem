from . import DataSource
from pydicom.dataset import Dataset
from pynetdicom import AE, evt # evt might not be needed if local SCP is gone
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind, StudyRootQueryRetrieveInformationModelMove
import logging

logger = logging.getLogger(__name__)

class MIM(DataSource):
    """
    Represents the MIM data source system.

    This class provides methods to query and transfer data from a MIM DICOM node
    using C-FIND and C-MOVE operations.
    """
    def __init__(self):
        """
        Initializes the MIM data source interface.
        """
        super().__init__()
        logger.debug("MIM DataSource initialized.")

    def query(self, query_dataset: Dataset, qr_scp: dict) -> set:
        """
        Performs a C-FIND query against the MIM system.

        :param query_dataset: A pydicom Dataset object containing query parameters.
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
        
        logger.info(f"Attempting C-FIND association to MIM QR SCP: {qr_scp['AETitle']} at {qr_scp['IP']}:{qr_scp['Port']}")
        assoc = ae.associate(qr_scp['IP'], qr_scp['Port'], ae_title=qr_scp['AETitle'])
        
        if assoc.is_established:
            logger.info("C-FIND Association established with MIM.")
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
                logger.debug("Releasing C-FIND association with MIM.")
                assoc.release()
        else:
            logger.error(f"C-FIND Association rejected, aborted or never connected to MIM SCP: {qr_scp['AETitle']}.")
        
        logger.info(f"C-FIND query to MIM found {len(uids)} SOPInstanceUIDs.")
        return uids

    def transfer(self, move_dataset: Dataset, qr_scp: dict, backup_destination_aet: str, calling_aet: str) -> bool:
        """
        Performs a C-MOVE operation to transfer data from MIM directly to a specified backup destination AET.

        :param move_dataset: A pydicom Dataset containing parameters for the C-MOVE request
                             (e.g., PatientID, StudyInstanceUID, SeriesInstanceUID).
                             QueryRetrieveLevel must be set.
        :type move_dataset: pydicom.dataset.Dataset
        :param qr_scp: Dictionary with MIM QR SCP details: {'IP', 'Port', 'AETitle'}.
        :type qr_scp: dict
        :param backup_destination_aet: The AE Title of the final backup destination (e.g., Orthanc).
        :type backup_destination_aet: str
        :param calling_aet: The AE Title of this application initiating the C-MOVE.
        :type calling_aet: str
        :return: True if the C-MOVE operation reported success (0x0000), False otherwise.
        :rtype: bool
        :raises Exception: Can raise exceptions for network or DICOM protocol errors if not handled by pynetdicom.
        """
        
        ae = AE(ae_title=calling_aet)
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelMove)
        
        success_flag = False

        logger.info(f"Attempting C-MOVE association to MIM QR SCP: {qr_scp['AETitle']} "
                    f"at {qr_scp['IP']}:{qr_scp['Port']} from AET {calling_aet}.")
        assoc = ae.associate(qr_scp['IP'], qr_scp['Port'], ae_title=qr_scp['AETitle'])
        
        if assoc.is_established:
            logger.info(f"C-MOVE Association established with MIM. Sending C-MOVE request for destination AET: {backup_destination_aet}.")
            try:
                responses = assoc.send_c_move(move_dataset, backup_destination_aet, StudyRootQueryRetrieveInformationModelMove)
                for (status, identifier) in responses: 
                    if status is None:
                        logger.error("C-MOVE failed: Connection timed out, aborted or received invalid response from MIM.")
                        break 
                    if status.Status == 0xFF00 or status.Status == 0xFF01: # Pending
                        logger.info(f"C-MOVE pending to {backup_destination_aet}: {status.NumberOfRemainingSuboperations} remaining, "
                                    f"{status.NumberOfCompletedSuboperations} completed, "
                                    f"{status.NumberOfWarningSuboperations} warnings, "
                                    f"{status.NumberOfFailedSuboperations} failures.")
                    elif status.Status == 0x0000: # Success
                        logger.info(f"C-MOVE operation to {backup_destination_aet} completed successfully from MIM's perspective.")
                        logger.info(f"C-MOVE final status: {status.NumberOfCompletedSuboperations} completed, "
                                    f"{status.NumberOfWarningSuboperations} warnings, "
                                    f"{status.NumberOfFailedSuboperations} failures.")
                        if status.NumberOfFailedSuboperations > 0 or status.NumberOfWarningSuboperations > 0:
                            logger.warning(f"C-MOVE to {backup_destination_aet} completed with failures/warnings. Check peer logs.")
                        success_flag = True
                        break 
                    else: # Failure or other status
                        error_message = f"C-MOVE operation to {backup_destination_aet} failed. Status: 0x{status.Status:04X}"
                        if hasattr(status, 'ErrorComment') and status.ErrorComment:
                            error_message += f" Error Comment: {status.ErrorComment}"
                        logger.error(error_message)
                        if hasattr(status, 'FailedSOPInstanceUIDList') and status.FailedSOPInstanceUIDList:
                             logger.error(f"Failed SOP Instance UID List: {status.FailedSOPInstanceUIDList}")
                        success_flag = False
                        break 
            except Exception as e:
                logger.error(f"Exception during C-MOVE operation to {backup_destination_aet}: {e}", exc_info=True)
                success_flag = False
            finally:
                logger.debug("Releasing C-MOVE association with MIM.")
                assoc.release()
        else:
            logger.error(f"C-MOVE Association rejected, aborted or never connected to MIM SCP: {qr_scp['AETitle']}.")
            success_flag = False
        
        if success_flag:
            logger.info(f"C-MOVE to {backup_destination_aet} reported overall success.")
        else:
            logger.error(f"C-MOVE to {backup_destination_aet} reported overall failure or was not established.")
        return success_flag
