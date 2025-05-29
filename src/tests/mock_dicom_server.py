import threading
import time # Will be used for example usage
import logging # Added
from pydicom.dataset import Dataset
from pynetdicom import AE, evt
from pynetdicom.sop_class import (
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelMove,
    StudyRootQueryRetrieveInformationModelGet, # Added for C-GET
    CompositeInstanceRootRetrieveGet,        # Added for C-GET
    # PatientRootQueryRetrieveInformationModelGet, # Consider if needed
)
from pynetdicom.presentation import PresentationContext, StoragePresentationContexts
from pydicom.uid import PYDICOM_IMPLEMENTATION_UID, ImplicitVRLittleEndian, ExplicitVRLittleEndian # Added ExplicitVRLittleEndian


# Configure basic logging for the mock server
logger = logging.getLogger('pynetdicom') # Using pynetdicom's logger for consistency or use __name__
# logger.setLevel(logging.INFO) # Or DEBUG for more verbosity
# handler = logging.StreamHandler()
# formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# handler.setFormatter(formatter)
# logger.addHandler(handler)
# logger.propagate = False # Prevent duplicate logging if root logger is also configured

class MockDicomServer:
    """
    A mock DICOM server for testing C-FIND and C-STORE operations.
    """

    def __init__(self, host: str, port: int, ae_title: str):
        """
        Initializes the MockDicomServer.

        Args:
            host: The hostname or IP address to bind to.
            port: The port number to listen on.
            ae_title: The AE title of this mock server.
        """
        self.host = host
        self.port = port
        self.ae_title = ae_title

        self.ae = AE(ae_title=self.ae_title)
        self._configure_presentation_contexts()

        self.c_find_responses: dict[frozenset, list[Dataset]] = {}
        self.received_datasets: list[Dataset] = []
        self.datasets_for_get: list[Dataset] = [] # Added for C-GET
        self.c_store_handler_override = None
        self.last_move_destination_aet: Optional[str] = None 
        self._server_thread = None
        self._server_instance = None

    def _configure_presentation_contexts(self):
        """Sets up the supported presentation contexts for the AE."""
        # For C-FIND
        self.ae.add_supported_context(StudyRootQueryRetrieveInformationModelFind)
        # For C-MOVE
        self.ae.add_supported_context(StudyRootQueryRetrieveInformationModelMove) # Added

        # For C-STORE (all standard storage SOP classes)
        for context in StoragePresentationContexts:
            self.ae.add_supported_context(context.abstract_syntax, context.transfer_syntax)
        
        # Add default transfer syntaxes for find and move - pynetdicom usually handles this well with default list
        # but being explicit can be useful. Default transfer syntaxes for Q/R are:
        # Implicit VR Little Endian, Explicit VR Little Endian, Explicit VR Big Endian (deprecated)
        # For this mock, we'll primarily use ImplicitVRLittleEndian & ExplicitVRLittleEndian
        transfer_syntaxes = [ImplicitVRLittleEndian, ExplicitVRLittleEndian, '1.2.840.10008.1.2.2'] # Added common UIDs
        self.ae.add_supported_context(StudyRootQueryRetrieveInformationModelFind, transfer_syntaxes)
        self.ae.add_supported_context(StudyRootQueryRetrieveInformationModelMove, transfer_syntaxes)
        # Add C-GET contexts
        self.ae.add_supported_context(StudyRootQueryRetrieveInformationModelGet, transfer_syntaxes)
        self.ae.add_supported_context(CompositeInstanceRootRetrieveGet, transfer_syntaxes)
        # self.ae.add_supported_context(PatientRootQueryRetrieveInformationModelGet, transfer_syntaxes)


    def handle_find(self, event):
        """
        Handles C-FIND requests.
        This method is registered as a callback for the evt.EVT_C_FIND event.
        """
        query_dataset = event.identifier
        query_key_items = []

        # Extract relevant query attributes for the key
        # This needs to be consistent with how keys are created in add_c_find_response
        for elem in query_dataset:
            if elem.value: # Only consider elements with values
                query_key_items.append((elem.tag, elem.value))
        query_key = frozenset(query_key_items)

        # Attempt to find a direct match for the full query key first
        if query_key in self.c_find_responses:
            response_datasets = self.c_find_responses[query_key]
            for ds in response_datasets:
                yield (0xFF00, ds) # Pending status
            yield (0x0000, None) # Success status
            return

        # If no direct match, iterate and check for subset matches (flexible matching)
        # This part can be adjusted based on desired matching strictness.
        # For simplicity, we'll stick to exact matches based on the add_c_find_response logic for now.
        # A more sophisticated approach might involve checking if the query_dataset's items
        # are a superset of any stored key's items.

        # If no responses are found for the specific query_key
        # Check for broader rule based on (QueryRetrieveLevel, PatientID, StudyInstanceUID, SeriesInstanceUID)
        # This is an example, can be more specific or general
        
        # Fallback: No specific rule matched
        yield (0x0000, None) # Success status, no results
        return

    def handle_move(self, event):
        """
        Handles C-MOVE requests.
        This method is registered as a callback for the evt.EVT_C_MOVE event.
        """
        logger.info(f"C-MOVE request received for AET: {self.ae_title}. Move Destination AET: {event.move_destination_aet}")
        
        # The event.identifier contains the C-MOVE request identifier dataset
        # For example, it will have PatientID, StudyInstanceUID, SeriesInstanceUID, SOPInstanceUID
        # that the SCU (ARIA) wants to be moved.
        # We can log it or store it if needed for assertions later.
        # logger.info(f"C-MOVE Identifier: {event.identifier}")

        # In a real C-MOVE SCP, this is where you would:
        # 1. Interpret event.identifier to know which SOP instances to send.
        # 2. Establish an association with event.move_destination_aet.
        # 3. Send the SOP instances using C-STORE sub-operations over that association.
        # 4. Yield status updates (Pending) for each sub-operation.

        # For this mock server, we are not actually performing the C-STORE sub-operations.
        # We just simulate the C-MOVE handshake.

        # Number of C-STORE sub-operations. We'll simulate a few.
        # The standard requires these to be accurate if provided.
        # For simplicity, we'll yield a generic "Pending" then "Success".
        # If event.identifier is available and has Number of Completed/Failed/Warning Sub-operations,
        # those would typically be for the SCU to fill, not the SCP initially.
        # The SCP reports these as it performs sub-operations.
        
        # Yield pending status (simulating work being done)
        # The dataset in the status can optionally contain:
        # (0000,0800) Number of Remaining Sub-operations
        # (0000,0850) Number of Completed Sub-operations
        # (0000,0860) Number of Failed Sub-operations
        # (0000,0870) Number of Warning Sub-operations
        
        # For this mock, we'll keep it simple.
        # No specific number of sub-operations are being reported back.
        # Yield a few "Pending" statuses
        self.last_move_destination_aet = event.move_destination_aet # Store the move destination
        yield (0xFF00, None) # Pending
        yield (0xFF00, None) # Pending

        # Finally, yield a "Success" status
        # This indicates that all C-STORE sub-operations (if any were to be performed) are complete.
        yield (0x0000, None) # Success
        return

    def handle_store(self, event):
        """
        Handles C-STORE requests.
        This method is registered as a callback for the evt.EVT_C_STORE event.
        """
        if self.c_store_handler_override:
            return self.c_store_handler_override(event)

        # event.dataset contains the DICOM dataset received
        # It's already a pydicom.Dataset object
        ds = event.dataset
        # Ensure it has necessary attributes for storage, like file_meta
        if not hasattr(ds, 'file_meta'):
            ds.file_meta = Dataset()
            # Populate with common UIDs if missing, or derive from context
            ds.file_meta.MediaStorageSOPClassUID = event.context.abstract_syntax # Corrected
            ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID if hasattr(ds, 'SOPInstanceUID') else 'UnknownUID_FileMetaCreation' # Ensure this is present in ds
            
            # Use the mock server's own implementation class UID
            if hasattr(self.ae, 'implementation_class_uid'):
                 ds.file_meta.ImplementationClassUID = self.ae.implementation_class_uid
            else: # Fallback if not set on AE (should be)
                # from pydicom.uid import PYDICOM_IMPLEMENTATION_UID # pydicom's default - import moved to top
                ds.file_meta.ImplementationClassUID = PYDICOM_IMPLEMENTATION_UID

            if event.context.transfer_syntax: # Should be present from accepted context
                ds.file_meta.TransferSyntaxUID = event.context.transfer_syntax[0]
            else:
                # Fallback, though ideally context should always have a negotiated transfer syntax
                # from pydicom.uid import ImplicitVRLittleEndian # A common default - import moved to top
                ds.file_meta.TransferSyntaxUID = ImplicitVRLittleEndian
            
            # Add a log to indicate file_meta was created
            logger.debug(f"MockDicomServer: Created missing file_meta for SOPInstanceUID: {ds.SOPInstanceUID if hasattr(ds, 'SOPInstanceUID') else 'Unknown'}")

        # Add a more general log for any stored dataset
        logger.info(f"MockDicomServer: Dataset received for SOPInstanceUID: {ds.SOPInstanceUID if hasattr(ds, 'SOPInstanceUID') else 'Unknown'}. Adding to received_datasets list.")
        self.received_datasets.append(ds)
        return 0x0000  # Success status

    def handle_get(self, event):
        """
        Handles C-GET requests.
        This method is registered as a callback for the evt.EVT_C_GET event.
        """
        logger.info(f"C-GET request received for AET: {self.ae_title}.")
        identifier = event.identifier
        logger.debug(f"C-GET Identifier: {identifier}")

        # Simplistic implementation: find matching SOPInstanceUID in self.datasets_for_get
        # A more complete implementation would handle QueryRetrieveLevel (PATIENT, STUDY, SERIES, IMAGE)
        # and match accordingly. For now, we focus on IMAGE level (SOPInstanceUID).
        
        sop_instance_uid_to_get = identifier.get("SOPInstanceUID")
        if not sop_instance_uid_to_get:
            logger.error("C-GET request identifier does not contain SOPInstanceUID. Cannot process.")
            yield (0xA900, None) # Unable to process
            return

        datasets_to_send = [
            ds for ds in self.datasets_for_get if ds.SOPInstanceUID == sop_instance_uid_to_get
        ]

        if not datasets_to_send:
            logger.warning(f"No datasets found for SOPInstanceUID {sop_instance_uid_to_get} in datasets_for_get.")
            # A700: Refused: Out of Resources (Unable to calculate number of matches)
            # A900: Error: Unable to process (Identifier does not match SOP Class)
            # C0xx: Error: Unable to process (Cannot support query retrieve level)
            yield (0xA700, None) 
            return

        number_of_matches = len(datasets_to_send)
        completed_sub_ops = 0
        failed_sub_ops = 0
        warning_sub_ops = 0 # Not typically used for C-STORE sub-ops by SCP

        # Send C-STORE sub-operations for each matching dataset
        for i, ds_to_send in enumerate(datasets_to_send):
            remaining_sub_ops = number_of_matches - (i + 1)
            
            # Yield Pending status before sending each C-STORE
            status_ds = Dataset()
            status_ds.NumberOfRemainingSuboperations = remaining_sub_ops
            status_ds.NumberOfCompletedSuboperations = completed_sub_ops
            status_ds.NumberOfFailedSuboperations = failed_sub_ops
            status_ds.NumberOfWarningSuboperations = warning_sub_ops
            yield (0xFF00, status_ds) # Pending

            logger.info(f"Attempting C-STORE sub-operation for SOPInstanceUID: {ds_to_send.SOPInstanceUID}")
            try:
                # The C-GET SCU acts as a C-STORE SCP for these sub-operations.
                # The association is event.assoc.
                c_store_status = event.assoc.send_c_store(ds_to_send)
                if c_store_status and c_store_status.Status == 0x0000:
                    logger.info(f"C-STORE sub-operation for {ds_to_send.SOPInstanceUID} successful.")
                    completed_sub_ops += 1
                else:
                    logger.error(f"C-STORE sub-operation for {ds_to_send.SOPInstanceUID} failed. Status: {c_store_status.Status if c_store_status else 'Unknown'}")
                    failed_sub_ops += 1
            except Exception as e:
                logger.error(f"Exception during C-STORE sub-operation for {ds_to_send.SOPInstanceUID}: {e}", exc_info=True)
                failed_sub_ops += 1
        
        # Final C-GET status
        final_status_ds = Dataset()
        final_status_ds.NumberOfRemainingSuboperations = 0
        final_status_ds.NumberOfCompletedSuboperations = completed_sub_ops
        final_status_ds.NumberOfFailedSuboperations = failed_sub_ops
        final_status_ds.NumberOfWarningSuboperations = warning_sub_ops

        if failed_sub_ops > 0 and completed_sub_ops > 0:
            logger.info(f"C-GET completed with some failures. Completed: {completed_sub_ops}, Failed: {failed_sub_ops}")
            yield (0xB000, final_status_ds) # Warning: Sub-operations Complete - One or more Failures
        elif failed_sub_ops > 0:
            logger.error(f"C-GET completed with all sub-operations failed. Failed: {failed_sub_ops}")
            yield (0xC000, final_status_ds) # Error: Unable to process (or a more specific Cxxx code)
        elif completed_sub_ops == number_of_matches:
            logger.info(f"C-GET completed successfully. Completed: {completed_sub_ops}")
            yield (0x0000, final_status_ds) # Success
        else: # Should not happen if logic is correct, but as a fallback
            logger.error("C-GET completed in an indeterminate state.")
            yield (0xA900, final_status_ds) # Error: Unable to process
        return


    def start(self):
        """
        Starts the DICOM server in a separate thread.
        """
        handlers = [
            (evt.EVT_C_FIND, self.handle_find),
            (evt.EVT_C_STORE, self.handle_store),
            (evt.EVT_C_MOVE, self.handle_move), 
            (evt.EVT_C_GET, self.handle_get), # Added C-GET handler
            # (evt.EVT_C_ECHO, self.handle_echo),
        ]
        
        # Configure pynetdicom logging for more insights if needed
        from pynetdicom import _config
        _config.LOG_HANDLER_LEVEL = 'DEBUG' # or 'INFO'
        # _config.LOG_HANDLER_BYTES_LIMIT = 1024 * 10 # Limit log output size for byte strings

        self._server_instance = self.ae.start_server(
            (self.host, self.port),
            block=False,  # Important: non-blocking
            evt_handlers=handlers
        )
        # Keep the main thread alive if it's just for the server, or manage thread separately
        # For this mock server, we assume it might be run and then interacted with.
        # If running in a script that then exits, the server thread would also exit.
        # A common pattern is to start and then join the thread if it's the primary focus.
        # However, for a mock object used in tests, starting and stopping is usually controlled by the test.

    def stop(self):
        """
        Stops the DICOM server.
        """
        if self._server_instance:
            self._server_instance.shutdown()
            self._server_instance = None # Clear the instance

    def reset(self):
        """
        Resets the server's state (clears responses and received datasets).
        """
        self.c_find_responses.clear()
        self.received_datasets.clear()
        self.datasets_for_get.clear() # Reset for C-GET
        self.last_move_destination_aet = None 

    def add_c_find_response(self, query_criteria_dataset: Dataset, response_datasets: list[Dataset]):
        """
        Adds a response rule for C-FIND queries.

        Args:
            query_criteria_dataset: A pydicom.Dataset containing attributes to match.
                                   Only these attributes will be used to create the key.
            response_datasets: A list of pydicom.Dataset to be returned if the query matches.
        """
        key_items = []
        # Create a key based on the elements present in the query_criteria_dataset
        # This ensures that the lookup in handle_find is consistent.
        for elem in query_criteria_dataset:
            if elem.value: # Only consider elements with values for the key
                key_items.append((elem.tag, elem.value))
        
        # Using frozenset of items makes the key hashable and order-independent
        query_key = frozenset(key_items)
        self.c_find_responses[query_key] = response_datasets

    def add_dataset_for_get(self, dataset: Dataset):
        """
        Adds a dataset that this mock server can "provide" via C-GET.
        Ensure the dataset has SOPInstanceUID.
        """
        if not hasattr(dataset, "SOPInstanceUID") or not dataset.SOPInstanceUID:
            logger.error("Dataset added for C-GET must have a SOPInstanceUID.")
            return
        # Ensure file_meta is present, as it's needed for C-STORE sub-operations
        if not hasattr(dataset, 'file_meta'):
            dataset.file_meta = Dataset()
        if not dataset.file_meta.get('TransferSyntaxUID'): # Set a default if not present
            dataset.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian 
        if not dataset.file_meta.get('MediaStorageSOPClassUID'):
            dataset.file_meta.MediaStorageSOPClassUID = dataset.SOPClassUID # Assume it's on the dataset
        if not dataset.file_meta.get('MediaStorageSOPInstanceUID'):
            dataset.file_meta.MediaStorageSOPInstanceUID = dataset.SOPInstanceUID
            
        self.datasets_for_get.append(dataset)
        logger.info(f"Added dataset {dataset.SOPInstanceUID} to datasets_for_get list.")


if __name__ == '__main__':
    # Example Usage (Optional)
    # This section can be used for basic manual testing of the server.
    # Note: For automated tests, you'd typically use a separate test script.

    server_host = "localhost"
    server_port = 11112
    server_ae_title = "MOCK_SCP"

    mock_server = MockDicomServer(host=server_host, port=server_port, ae_title=server_ae_title)
    mock_server.start()

    print(f"Mock DICOM server '{server_ae_title}' running at {server_host}:{server_port}")

    # Example: Add a C-FIND response
    # Create a query criteria dataset
    find_query = Dataset()
    find_query.PatientID = "12345"
    find_query.QueryRetrieveLevel = "PATIENT"
    find_query.Modality = "CT"

    # Create response datasets
    response_ds1 = Dataset()
    response_ds1.PatientID = "12345"
    response_ds1.PatientName = "Test^Patient"
    response_ds1.Modality = "CT"
    response_ds1.SOPInstanceUID = "1.2.3.4.5.6.7.8.9.1" 
    response_ds1.StudyInstanceUID = "1.2.3.4.5"
    response_ds1.SeriesInstanceUID = "1.2.3.4.5.6"
    response_ds1.SOPClassUID = '1.2.840.10008.5.1.4.1.1.2' # CT Image Storage
    response_ds1.file_meta = Dataset()
    response_ds1.file_meta.MediaStorageSOPClassUID = response_ds1.SOPClassUID
    response_ds1.file_meta.MediaStorageSOPInstanceUID = response_ds1.SOPInstanceUID
    response_ds1.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian # Example
    response_ds1.is_little_endian = True
    response_ds1.is_implicit_VR = False # For ExplicitVRLittleEndian


    mock_server.add_c_find_response(find_query, [response_ds1])
    mock_server.add_dataset_for_get(response_ds1) # Make it available for C-GET
    print(f"Added C-FIND response for PatientID '12345' and Modality 'CT'")
    print(f"Dataset {response_ds1.SOPInstanceUID} available for C-GET.")

    try:
        # Keep the server running for a while (e.g., for manual testing with a DICOM client)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down mock server...")
    finally:
        mock_server.stop()
        print("Mock server stopped.")

    print(f"Received datasets ({len(mock_server.received_datasets)}):")
    for ds in mock_server.received_datasets:
        print(ds.PatientID, ds.SOPInstanceUID)

