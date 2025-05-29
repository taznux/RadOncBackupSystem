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
        # self.store_scp is no longer needed
        # self.received_by_internal_scp is no longer needed

    def tearDown(self):
        if hasattr(self, 'mock_qr_scp_server') and self.mock_qr_scp_server:
            self.mock_qr_scp_server.stop()
            self.mock_qr_scp_server.reset()

    def test_query(self):
        uids = self.mim.query(self.query_dataset, self.qr_scp)
        self.assertIsInstance(uids, set)
        self.assertEqual(len(uids), 1)
        self.assertIn(self.sample_response_dataset.SOPInstanceUID, uids)

    def test_transfer(self):
        mock_backup_destination_aet = "MIM_BACKUP_AET"
        mock_calling_aet = "TEST_MIM_CALLING_AET"

        # self.get_dataset (from setUp) is used as the move_dataset argument.
        # It's configured for IMAGE level C-MOVE with a SOPInstanceUID.
        
        result = False # Initialize to ensure it's set
        try:
            result = self.mim.transfer(
                self.get_dataset, # This is the move_dataset for the C-MOVE operation
                self.qr_scp, 
                mock_backup_destination_aet,
                mock_calling_aet
            )
        except Exception as e:
            self.fail(f"MIM.transfer raised an exception: {e}")

        self.assertTrue(result, "MIM.transfer should return True on C-MOVE success.")
        
        # Verify that the MockDicomServer (acting as C-MOVE SCP) received the correct move_destination_aet
        self.assertIsNotNone(self.mock_qr_scp_server.last_move_destination_aet, 
                             "MockDicomServer should have recorded the move_destination_aet.")
        self.assertEqual(self.mock_qr_scp_server.last_move_destination_aet, 
                         mock_backup_destination_aet,
                         "MIM.transfer did not send the correct move_destination_aet to the C-MOVE SCP.")

if __name__ == '__main__':
    unittest.main()
