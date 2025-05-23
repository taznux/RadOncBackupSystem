from . import DataSource
from pydicom.dataset import Dataset
from pynetdicom import AE, evt, StoragePresentationContexts, ALL_TRANSFER_SYNTAXES
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind, StudyRootQueryRetrieveInformationModelGet
import logging

logger = logging.getLogger(__name__)

class MIM(DataSource):
    """
    Represents the MIM data source system.

    This class provides methods to query and transfer data from a MIM DICOM node
    using C-FIND and C-GET operations.
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
                        if identifier and hasattr(identifier, 'SOPInstanceUID'):
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

    def transfer(self, get_dataset: Dataset, qr_scp: dict, local_store_config: dict, c_store_handler: callable):
        """
        Performs a C-GET operation to retrieve data from MIM and store it locally.

        This method initiates a C-GET request to the MIM QR SCP. The MIM SCP will then
        initiate C-STORE sub-operations to this application. This method starts a temporary
        local C-STORE SCP to receive these instances.

        :param get_dataset: A pydicom Dataset object specifying what to retrieve.
                            Must include QueryRetrieveLevel and unique keys for that level
                            (e.g., StudyInstanceUID, SeriesInstanceUID, SOPInstanceUID).
        :type get_dataset: pydicom.dataset.Dataset
        :param qr_scp: A dictionary containing the MIM Query/Retrieve SCP details:
                       {'IP': 'host_ip', 'Port': port_number, 'AETitle': 'AE_TITLE'}.
        :type qr_scp: dict
        :param local_store_config: A dictionary for the local C-STORE SCP configuration:
                                   {'IP': 'local_ip', 'Port': local_port_number, 'AETitle': 'local_aet'}.
                                   The 'AETitle' is used for the SCP AE.
        :type local_store_config: dict
        :param c_store_handler: The event handler function (e.g., for `evt.EVT_C_STORE`)
                                to be used by the local C-STORE SCP. This handler will
                                process each DICOM instance received.
        :type c_store_handler: callable
        :raises Exception: Can raise various exceptions related to network issues or DICOM protocol errors.
        """
        # The AE for this operation acts as both C-GET SCU and C-STORE SCP.
        # The AETitle in local_store_config is for our SCP part.
        ae = AE(ae_title=local_store_config['AETitle'])
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelGet)
        
        # Configure SCP part: contexts it supports for incoming C-STOREs
        for context in StoragePresentationContexts: # Support all standard storage SOP classes
            ae.add_supported_context(context.abstract_syntax, ALL_TRANSFER_SYNTAXES) # And all transfer syntaxes
        
        handlers = [(evt.EVT_C_STORE, c_store_handler)]
        
        logger.info(f"Starting temporary C-STORE SCP on {local_store_config['IP']}:{local_store_config['Port']} "
                    f"with AET {ae.ae_title} for C-GET operation.")
        scp_server = ae.start_server((local_store_config['IP'], local_store_config['Port']),
                                     block=False, evt_handlers=handlers)

        logger.info(f"Attempting C-GET association to MIM QR SCP: {qr_scp['AETitle']} at {qr_scp['IP']}:{qr_scp['Port']}")
        assoc = ae.associate(qr_scp['IP'], qr_scp['Port'], ae_title=qr_scp['AETitle'])
        
        if assoc.is_established:
            logger.info(f"C-GET Association established with MIM. Sending C-GET request.")
            try:
                # C-GET SCU tells the SCP to send instances back to us (our AE).
                # The SCP will use the AE Title from the C-GET association request.
                responses = assoc.send_c_get(get_dataset, StudyRootQueryRetrieveInformationModelGet)
                for (status, _) in responses: # Identifier is typically None for C-GET responses from SCU perspective
                    if status is None:
                        logger.error("C-GET failed: Connection timed out, aborted or received invalid response from MIM.")
                        break
                    if status.Status == 0xFF00 or status.Status == 0xFF01: # Pending
                        logger.info(f"C-GET pending: {status.NumberOfRemainingSuboperations} remaining, "
                                    f"{status.NumberOfCompletedSuboperations} completed, "
                                    f"{status.NumberOfWarningSuboperations} warnings, "
                                    f"{status.NumberOfFailedSuboperations} failures.")
                    elif status.Status == 0x0000: # Success
                        logger.info("C-GET operation completed successfully.")
                        logger.info(f"C-GET final status: {status.NumberOfCompletedSuboperations} completed, "
                                    f"{status.NumberOfWarningSuboperations} warnings, "
                                    f"{status.NumberOfFailedSuboperations} failures.")
                        break
                    else: # Failure
                        logger.error(f"C-GET operation failed. Status: 0x{status.Status:04X}")
                        if hasattr(status, 'ErrorComment') and status.ErrorComment:
                            logger.error(f"Error Comment: {status.ErrorComment}")
                        break
            except Exception as e:
                logger.error(f"Exception during C-GET operation: {e}", exc_info=True)
            finally:
                logger.debug("Releasing C-GET association with MIM.")
                assoc.release()
        else:
            logger.error(f"C-GET Association rejected, aborted or never connected to MIM SCP: {qr_scp['AETitle']}.")
        
        logger.debug("Shutting down temporary C-STORE SCP for C-GET.")
        scp_server.shutdown()
        logger.info("Temporary C-STORE SCP for C-GET shut down.")
