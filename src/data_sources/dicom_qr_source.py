import logging
from pydicom.dataset import Dataset
from pynetdicom import AE
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind, StudyRootQueryRetrieveInformationModelMove
from . import DataSource

class DicomQrDataSource(DataSource):
    """
    Base class for DICOM Query/Retrieve data sources.

    Provides common C-FIND and C-MOVE functionality.
    """
    def __init__(self, source_name: str):
        """
        Initializes the DicomQrDataSource.

        :param source_name: The name of the DICOM source (e.g., "ARIA", "MIM"), used for logging.
        :type source_name: str
        """
        super().__init__()
        self.source_name = source_name
        self.logger = logging.getLogger(__name__) # This logger will be named after this module: 'src.data_sources.dicom_qr_source'
                                                # Logs will use self.source_name for differentiation in messages.

    def query(self, query_dataset: Dataset, qr_scp: dict) -> set:
        """
        Performs a C-FIND query against the configured DICOM source.

        :param query_dataset: A pydicom Dataset object containing query parameters.
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

        self.logger.info(f"Attempting C-FIND association to {self.source_name} QR SCP: {qr_scp['AETitle']} at {qr_scp['IP']}:{qr_scp['Port']}")
        assoc = ae.associate(qr_scp['IP'], qr_scp['Port'], ae_title=qr_scp['AETitle'])

        if assoc.is_established:
            self.logger.info(f"C-FIND Association established with {self.source_name}.")
            try:
                responses = assoc.send_c_find(query_dataset, StudyRootQueryRetrieveInformationModelFind)
                for (status, identifier) in responses:
                    if status and (status.Status == 0xFF00 or status.Status == 0xFF01): # Pending
                        if identifier and hasattr(identifier, 'SOPInstanceUID'):
                            self.logger.debug(f"C-FIND Pending from {self.source_name}: Found SOPInstanceUID {identifier.SOPInstanceUID}")
                            uids.add(identifier.SOPInstanceUID)
                        else:
                            self.logger.debug(f"C-FIND Pending status from {self.source_name} with no valid identifier.")
                    elif status and status.Status == 0x0000: # Success
                        if identifier and hasattr(identifier, 'SOPInstanceUID'):
                            self.logger.debug(f"C-FIND Success from {self.source_name}: Found SOPInstanceUID {identifier.SOPInstanceUID}")
                            uids.add(identifier.SOPInstanceUID)
                        self.logger.info(f"C-FIND operation with {self.source_name} completed successfully.")
                        break
                    else: # Failure or unknown status
                        error_msg = f"C-FIND query to {self.source_name} failed or connection issue."
                        if status:
                            error_msg += f" Status: 0x{status.Status:04X}."
                        else:
                            error_msg += " No status returned."
                        self.logger.error(error_msg)
                        break
            except Exception as e:
                self.logger.error(f"Exception during C-FIND operation with {self.source_name}: {e}", exc_info=True)
            finally:
                self.logger.debug(f"Releasing C-FIND association with {self.source_name}.")
                assoc.release()
        else:
            self.logger.error(f"C-FIND Association rejected, aborted or never connected to {self.source_name} SCP: {qr_scp['AETitle']}.")

        self.logger.info(f"C-FIND query to {self.source_name} found {len(uids)} SOPInstanceUIDs.")
        return uids

    def transfer(self, move_dataset: Dataset, qr_scp: dict, backup_destination_aet: str, calling_aet: str) -> bool:
        """
        Performs a C-MOVE operation to transfer data from the configured DICOM source
        directly to a specified backup destination AET.

        :param move_dataset: A pydicom Dataset containing parameters for the C-MOVE request.
        :type move_dataset: pydicom.dataset.Dataset
        :param qr_scp: Dictionary with source QR SCP details: {'IP', 'Port', 'AETitle'}.
        :type qr_scp: dict
        :param backup_destination_aet: The AE Title of the final backup destination.
        :type backup_destination_aet: str
        :param calling_aet: The AE Title of this application initiating the C-MOVE.
        :type calling_aet: str
        :return: True if the C-MOVE operation reported success (0x0000), False otherwise.
        :rtype: bool
        """

        ae = AE(ae_title=calling_aet)
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelMove)

        success_flag = False

        self.logger.info(f"Attempting C-MOVE association to {self.source_name} QR SCP: {qr_scp['AETitle']} "
                         f"at {qr_scp['IP']}:{qr_scp['Port']} from AET {calling_aet}.")
        assoc = ae.associate(qr_scp['IP'], qr_scp['Port'], ae_title=qr_scp['AETitle'])

        if assoc.is_established:
            self.logger.info(f"C-MOVE Association established with {self.source_name}. Sending C-MOVE request for destination AET: {backup_destination_aet}.")
            try:
                responses = assoc.send_c_move(move_dataset, backup_destination_aet, StudyRootQueryRetrieveInformationModelMove)
                for (status, identifier) in responses:
                    if status is None:
                        self.logger.error(f"C-MOVE failed with {self.source_name}: Connection timed out, aborted or received invalid response.")
                        break
                    if status.Status == 0xFF00 or status.Status == 0xFF01: # Pending
                        self.logger.info(f"C-MOVE from {self.source_name} pending to {backup_destination_aet}: {status.NumberOfRemainingSuboperations} remaining, "
                                         f"{status.NumberOfCompletedSuboperations} completed, "
                                         f"{status.NumberOfWarningSuboperations} warnings, "
                                         f"{status.NumberOfFailedSuboperations} failures.")
                    elif status.Status == 0x0000: # Success
                        self.logger.info(f"C-MOVE operation from {self.source_name} to {backup_destination_aet} completed successfully from {self.source_name}'s perspective.")
                        self.logger.info(f"C-MOVE final status from {self.source_name}: {status.NumberOfCompletedSuboperations} completed, "
                                         f"{status.NumberOfWarningSuboperations} warnings, "
                                         f"{status.NumberOfFailedSuboperations} failures.")
                        if status.NumberOfFailedSuboperations > 0 or status.NumberOfWarningSuboperations > 0:
                            self.logger.warning(f"C-MOVE from {self.source_name} to {backup_destination_aet} completed with failures/warnings. Check peer logs.")
                        success_flag = True
                        break
                    else: # Failure or other status
                        error_message = f"C-MOVE operation from {self.source_name} to {backup_destination_aet} failed. Status: 0x{status.Status:04X}"
                        if hasattr(status, 'ErrorComment') and status.ErrorComment:
                            error_message += f" Error Comment: {status.ErrorComment}"
                        self.logger.error(error_message)
                        if hasattr(status, 'FailedSOPInstanceUIDList') and status.FailedSOPInstanceUIDList:
                             self.logger.error(f"Failed SOP Instance UID List from {self.source_name}: {status.FailedSOPInstanceUIDList}")
                        success_flag = False
                        break
            except Exception as e:
                self.logger.error(f"Exception during C-MOVE operation from {self.source_name} to {backup_destination_aet}: {e}", exc_info=True)
                success_flag = False
            finally:
                self.logger.debug(f"Releasing C-MOVE association with {self.source_name}.")
                assoc.release()
        else:
            self.logger.error(f"C-MOVE Association rejected, aborted or never connected to {self.source_name} SCP: {qr_scp['AETitle']}.")
            success_flag = False

        if success_flag:
            self.logger.info(f"C-MOVE from {self.source_name} to {backup_destination_aet} reported overall success.")
        else:
            self.logger.error(f"C-MOVE from {self.source_name} to {backup_destination_aet} reported overall failure or was not established.")
        return success_flag
