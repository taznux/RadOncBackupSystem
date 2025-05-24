import unittest
from pydicom.dataset import Dataset
from pydicom.uid import ImplicitVRLittleEndian, ExplicitVRLittleEndian # Added
from src.data_sources.mim import MIM
from src.tests.mock_dicom_server import MockDicomServer # Added

class TestMIM(unittest.TestCase):

    def setUp(self):
        self.mim = MIM()
        self.query_dataset = Dataset()
        self.query_dataset.QueryRetrieveLevel = 'SERIES'
        self.query_dataset.Modality = 'RTRECORD'
        self.query_dataset.SeriesInstanceUID = ''
        self.query_dataset.PatientID = '12345'
        self.query_dataset.StudyDate = '20220101'
        self.query_dataset.StudyInstanceUID = ''
        # self.query_dataset.PatientName = "" # Ensure not set or empty if not part of query key

        self.received_by_internal_scp = [] # Added for test_transfer

        self.get_dataset = Dataset() # This is used for C-MOVE identifier by MIM.transfer
        self.get_dataset.QueryRetrieveLevel = 'IMAGE' # Assuming IMAGE level for MIM transfer
        self.get_dataset.SOPInstanceUID = '1.2.3.4.5.6.7.8.9.0' # Example SOPInstanceUID
        # If QueryRetrieveLevel for C-MOVE was SERIES, then SeriesInstanceUID should be populated in self.get_dataset
        # self.get_dataset.SeriesInstanceUID = "1.2.3.series.uid.for.get"
        # If QueryRetrieveLevel for C-MOVE was STUDY, then StudyInstanceUID should be populated
        # self.get_dataset.StudyInstanceUID = "1.2.3.study.uid.for.get"


        # Mock QR SCP Server Setup (for C-FIND and C-MOVE)
        mock_qr_host = '127.0.0.1'
        mock_qr_port = 11114 # Distinct port for MIM tests
        mock_qr_ae_title = 'MOCK_MIM_QR'
        self.mock_qr_scp_server = MockDicomServer(host=mock_qr_host, port=mock_qr_port, ae_title=mock_qr_ae_title)

        # Define sample response for C-FIND
        self.sample_response_dataset = Dataset()
        self.sample_response_dataset.PatientID = self.query_dataset.PatientID
        self.sample_response_dataset.StudyInstanceUID = '9.8.7.6.5.4.3.2.1.0' # Example UID
        self.sample_response_dataset.SeriesInstanceUID = '9.8.7.6.5.4.3.2.1.1' # Example UID
        self.sample_response_dataset.SOPInstanceUID = '9.8.7.6.5.4.3.2.1.2' # Example UID
        self.sample_response_dataset.Modality = self.query_dataset.Modality
        self.sample_response_dataset.QueryRetrieveLevel = self.query_dataset.QueryRetrieveLevel
        self.sample_response_dataset.file_meta = Dataset()
        self.sample_response_dataset.file_meta.MediaStorageSOPClassUID = '1.2.840.10008.5.1.4.1.1.481.3' # Example
        self.sample_response_dataset.file_meta.MediaStorageSOPInstanceUID = self.sample_response_dataset.SOPInstanceUID
        self.sample_response_dataset.is_little_endian = True
        self.sample_response_dataset.is_implicit_VR = True

        # Configure and start mock server
        self.mock_qr_scp_server.add_c_find_response(self.query_dataset, [self.sample_response_dataset])
        self.mock_qr_scp_server.start()

        self.qr_scp = { # This is the C-FIND and C-MOVE SCP
            'AETitle': mock_qr_ae_title,
            'IP': mock_qr_host,
            'Port': mock_qr_port
        }

        # This is the configuration for the internal C-STORE SCP that MIM.transfer will start.
        # The C-MOVE SCP (mock_qr_scp_server) will be told to send files to this AET.
        self.store_scp = {
            'AETitle': 'MIM_INTERNAL_STORE_SCP', # AE Title for MIM's internal C-STORE SCP
            'IP': '127.0.0.1', # IP where MIM's internal C-STORE SCP will listen
            'Port': 11115 # Port for MIM's internal C-STORE SCP, must be distinct
        }

    def tearDown(self):
        if hasattr(self, 'mock_qr_scp_server') and self.mock_qr_scp_server:
            self.mock_qr_scp_server.stop()
            self.mock_qr_scp_server.reset()

    def test_query(self):
        # Test the query method using the mock server
        uids = self.mim.query(self.query_dataset, self.qr_scp)
        self.assertIsInstance(uids, set)
        self.assertEqual(len(uids), 1)
        self.assertIn(self.sample_response_dataset.SOPInstanceUID, uids)

    def test_transfer(self):
        # Test the transfer method using the mock C-MOVE SCP
        
        # This handle_store is for the internal C-STORE SCP started by MIM.transfer
        def handle_store(event):
            # This SCP receives files if the C-MOVE SCP (mock_qr_scp_server)
            # were to actually send them. Our mock setup does not send.
            if event.dataset:
                self.received_by_internal_scp.append(event.dataset.SOPInstanceUID)
            return 0x0000 # Success status for the C-STORE operation

        # self.qr_scp is the C-MOVE SCP (our mock server).
        # self.store_scp['AETitle'] is the AE Title that MIM.transfer's internal C-STORE SCP will use.
        # MIM.transfer will tell self.qr_scp (the C-MOVE SCP) to send files to self.store_scp['AETitle'].
        # Our mock_qr_scp_server's handle_move will log this destination AET.
        
        # self.get_dataset contains the C-MOVE request identifier (e.g., SOPInstanceUID for IMAGE level)
        try:
            self.mim.transfer(self.get_dataset, self.qr_scp, self.store_scp, handle_store)
        except Exception as e:
            self.fail(f"MIM.transfer raised an exception: {e}")

        # Assert that no data was actually received by the internal SCP,
        # because our mock C-MOVE SCP (mock_qr_scp_server) doesn't send C-STORE sub-operations.
        self.assertEqual(len(self.received_by_internal_scp), 0, 
                         "Internal SCP should not have received any datasets.")

if __name__ == '__main__':
    unittest.main()
