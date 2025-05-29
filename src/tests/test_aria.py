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
        # self.store_scp is no longer needed here
        # self.received_by_internal_scp is no longer needed

    def tearDown(self):
        if hasattr(self, 'mock_qr_scp_server') and self.mock_qr_scp_server:
            self.mock_qr_scp_server.stop()
            self.mock_qr_scp_server.reset() 

    def test_query(self):
        uids = self.aria.query(self.query_dataset, self.qr_scp)
        self.assertIsInstance(uids, set)
        self.assertEqual(len(uids), 1)
        self.assertIn(self.sample_response_dataset.SOPInstanceUID, uids)

    def test_transfer(self):
        mock_backup_destination_aet = "BACKUP_ORTHANC_AET"
        mock_calling_aet = "TEST_CALLING_AET"

        # Ensure self.move_dataset has the correct attributes for the C-MOVE request.
        # Current self.move_dataset is 'IMAGE' level with SOPInstanceUID.

        result = False # Initialize to ensure it's set
        try:
            result = self.aria.transfer(
                self.move_dataset, 
                self.qr_scp, 
                mock_backup_destination_aet,
                mock_calling_aet
            )
        except Exception as e:
            self.fail(f"ARIA.transfer raised an exception: {e}")

        self.assertTrue(result, "ARIA.transfer should return True on C-MOVE success.")
        
        # Verify that the MockDicomServer (acting as C-MOVE SCP) received the correct move_destination_aet
        self.assertIsNotNone(self.mock_qr_scp_server.last_move_destination_aet, 
                             "MockDicomServer should have recorded the move_destination_aet.")
        self.assertEqual(self.mock_qr_scp_server.last_move_destination_aet, 
                         mock_backup_destination_aet,
                         "ARIA.transfer did not send the correct move_destination_aet to the C-MOVE SCP.")


if __name__ == '__main__':
    unittest.main()
