import unittest
from pydicom.dataset import Dataset
from pydicom.uid import ImplicitVRLittleEndian, ExplicitVRLittleEndian # Added
from src.data_sources.aria import ARIA
from src.tests.mock_dicom_server import MockDicomServer # Added

class TestARIA(unittest.TestCase):

    def setUp(self):
        self.aria = ARIA()
        self.query_dataset = Dataset()
        self.query_dataset.QueryRetrieveLevel = 'SERIES'
        self.query_dataset.Modality = 'RTRECORD'
        self.query_dataset.SeriesInstanceUID = '' # Keep SeriesInstanceUID empty as per original for query_dataset
        self.query_dataset.PatientID = '12345'
        self.query_dataset.StudyDate = '20220101' # This was in original, keep for now
        self.query_dataset.StudyInstanceUID = '' # Keep StudyInstanceUID empty as per original for query_dataset
        # Ensure PatientName is not set or is empty if not part of the specific query key for C-FIND
        # self.query_dataset.PatientName = ""

        self.received_by_internal_scp = [] # Added for test_transfer

        self.move_dataset = Dataset()
        self.move_dataset.QueryRetrieveLevel = 'IMAGE' # Or SERIES/STUDY depending on what ARIA.transfer expects
        self.move_dataset.SOPInstanceUID = '1.2.3.4.5.6.7.8.9.0' # Must be a valid UID for the C-MOVE identifier
        # If QueryRetrieveLevel is SERIES, then SeriesInstanceUID should be populated
        # self.move_dataset.SeriesInstanceUID = "1.2.3.series.uid" 
        # If QueryRetrieveLevel is STUDY, then StudyInstanceUID should be populated
        # self.move_dataset.StudyInstanceUID = "1.2.3.study.uid"

        # Mock QR SCP Server Setup
        mock_qr_host = '127.0.0.1'
        mock_qr_port = 11112
        mock_qr_ae_title = 'MOCK_ARIA_QR'
        self.mock_qr_scp_server = MockDicomServer(host=mock_qr_host, port=mock_qr_port, ae_title=mock_qr_ae_title)

        # Define sample response for C-FIND
        self.sample_response_dataset = Dataset()
        self.sample_response_dataset.PatientID = self.query_dataset.PatientID
        self.sample_response_dataset.StudyInstanceUID = '1.2.840.113619.2.55.3.2831187366.123.1370177878.940' # Example UID
        self.sample_response_dataset.SeriesInstanceUID = '1.2.840.113619.2.55.3.2831187366.123.1370177878.941' # Example UID
        self.sample_response_dataset.SOPInstanceUID = '1.2.840.113619.2.55.3.2831187366.123.1370177878.942' # Example UID
        self.sample_response_dataset.Modality = self.query_dataset.Modality
        self.sample_response_dataset.QueryRetrieveLevel = self.query_dataset.QueryRetrieveLevel
        # Add file_meta for C-STORE compatibility if these datasets were to be stored
        self.sample_response_dataset.file_meta = Dataset()
        self.sample_response_dataset.file_meta.MediaStorageSOPClassUID = '1.2.840.10008.5.1.4.1.1.481.3' # RT Structure Set Storage, example
        self.sample_response_dataset.file_meta.MediaStorageSOPInstanceUID = self.sample_response_dataset.SOPInstanceUID
        self.sample_response_dataset.is_little_endian = True
        self.sample_response_dataset.is_implicit_VR = True


        # Configure and start mock server
        self.mock_qr_scp_server.add_c_find_response(self.query_dataset, [self.sample_response_dataset])
        self.mock_qr_scp_server.start()

        self.qr_scp = {
            'AETitle': mock_qr_ae_title,
            'IP': mock_qr_host,
            'Port': mock_qr_port
        }

        # Store SCP for transfer test (remains unchanged for now, but could be mocked too)
        self.store_scp = {
            'AETitle': 'STORE_SCP', # This would be a real or another mock server for C-STORE
            'IP': '127.0.0.1',
            'Port': 11113 # Different port if also mocked locally
        }

    def tearDown(self):
        if hasattr(self, 'mock_qr_scp_server') and self.mock_qr_scp_server:
            self.mock_qr_scp_server.stop()
            self.mock_qr_scp_server.reset() # Good practice

    def test_query(self):
        # Test the query method using the mock server
        uids = self.aria.query(self.query_dataset, self.qr_scp)
        self.assertIsInstance(uids, set)
        self.assertEqual(len(uids), 1)
        self.assertIn(self.sample_response_dataset.SOPInstanceUID, uids)


    def test_transfer(self):
        # Test the transfer method
        # This test might need its own mock C-STORE server or adjustments
        # For now, it's left as is, assuming it might connect to a real/different SCP
        # or will be updated in a subsequent step.
        
        # This handle_store is for the internal C-STORE SCP started by ARIA.transfer
        def handle_store(event):
            # This SCP receives files if the C-MOVE SCP (our mock_qr_scp_server)
            # were to actually send them. In our current mock setup, it does not.
            if event.dataset: # event.dataset might be None for other C-STORE related events
                self.received_by_internal_scp.append(event.dataset.SOPInstanceUID)
            return 0x0000 # Success status for the C-STORE operation

        # self.qr_scp is the C-MOVE SCP (our mock server).
        # self.store_scp['AETitle'] is the AE Title that ARIA.transfer's internal C-STORE SCP will use.
        # ARIA.transfer will tell self.qr_scp (the C-MOVE SCP) to send files to self.store_scp['AETitle'].
        # Our mock_qr_scp_server's handle_move will log this destination AET.
        
        # We need to ensure self.move_dataset has the correct attributes for the C-MOVE request.
        # For example, if QueryRetrieveLevel is 'IMAGE', SOPInstanceUID must be present.
        # If 'SERIES', SeriesInstanceUID must be present. If 'STUDY', StudyInstanceUID.
        # The current self.move_dataset is set to 'IMAGE' level with a SOPInstanceUID.

        try:
            self.aria.transfer(self.move_dataset, self.qr_scp, self.store_scp, handle_store)
        except Exception as e:
            self.fail(f"ARIA.transfer raised an exception: {e}")

        # Assert that no data was actually received by the internal SCP,
        # because our mock C-MOVE SCP (mock_qr_scp_server) doesn't send C-STORE sub-operations.
        self.assertEqual(len(self.received_by_internal_scp), 0, 
                         "Internal SCP should not have received any datasets.")

        # The primary check is that the C-MOVE operation completes successfully at the protocol level,
        # meaning aria.transfer doesn't hang or error out when communicating with mock_qr_scp_server.
        # The mock_qr_scp_server's handle_move should have logged the move_destination_aet.


if __name__ == '__main__':
    unittest.main()
