import threading
import time # Will be used for example usage
import logging # Added
from pydicom.dataset import Dataset
from pynetdicom import AE, evt
from pynetdicom.sop_class import (
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelMove,
)
from pynetdicom.presentation import PresentationContext, StoragePresentationContexts
from pydicom.uid import PYDICOM_IMPLEMENTATION_UID, ImplicitVRLittleEndian # Added for fallback


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
        self.c_store_handler_override = None
        self.last_move_destination_aet: Optional[str] = None # Added to store move destination
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
        transfer_syntaxes = ['1.2.840.10008.1.2', '1.2.840.10008.1.2.1', '1.2.840.10008.1.2.2']
        self.ae.add_supported_context(StudyRootQueryRetrieveInformationModelFind, transfer_syntaxes)
        self.ae.add_supported_context(StudyRootQueryRetrieveInformationModelMove, transfer_syntaxes)


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

    def start(self):
        """
        Starts the DICOM server in a separate thread.
        """
        handlers = [
            (evt.EVT_C_FIND, self.handle_find),
            (evt.EVT_C_STORE, self.handle_store),
            (evt.EVT_C_MOVE, self.handle_move), # Added
            # Optional: Add handlers for other events like EVT_C_ECHO if needed
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
        self.last_move_destination_aet = None # Reset on server reset

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
    response_ds1.SOPInstanceUID = "1.2.3.4.5.6.7.8.9.1" # Must be unique
    response_ds1.StudyInstanceUID = "1.2.3.4.5"
    response_ds1.SeriesInstanceUID = "1.2.3.4.5.6"
     # Add media storage SOP class UID if not present, required for C-STORE
    response_ds1.file_meta = Dataset()
    response_ds1.file_meta.MediaStorageSOPClassUID = '1.2.840.10008.5.1.4.1.1.2' # CT Image Storage
    response_ds1.file_meta.MediaStorageSOPInstanceUID = response_ds1.SOPInstanceUID
    response_ds1.is_little_endian = True
    response_ds1.is_implicit_VR = True


    mock_server.add_c_find_response(find_query, [response_ds1])
    print(f"Added C-FIND response for PatientID '12345' and Modality 'CT'")

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

