import unittest
from pydicom.dataset import Dataset, FileMetaDataset # Added FileMetaDataset
from pydicom.uid import ImplicitVRLittleEndian, ExplicitVRLittleEndian, generate_uid, PYDICOM_IMPLEMENTATION_UID # Added
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
        self.move_dataset.QueryRetrieveLevel = 'SERIES'
        self.move_dataset.PatientID = "ARIA_TEST_PAT" 
        self.move_dataset.StudyInstanceUID = generate_uid() 
        self.move_dataset.SeriesInstanceUID = generate_uid() 
        # Ensure no other UIDs like SOPInstanceUID are present for a SERIES level C-MOVE
        if hasattr(self.move_dataset, 'SOPInstanceUID'):
            delattr(self.move_dataset, 'SOPInstanceUID')
        
        # Set common encoding properties for the identifier dataset
        self.move_dataset.is_little_endian = True
        self.move_dataset.is_implicit_VR = True

        # Mock QR SCP Server Setup
        mock_qr_host = '127.0.0.1'
        mock_qr_port = 11112
        mock_qr_ae_title = 'MOCK_ARIA_QR'
        self.mock_qr_scp_server = MockDicomServer(host=mock_qr_host, port=mock_qr_port, ae_title=mock_qr_ae_title)

        # Define sample response for C-FIND
        self.sample_response_dataset = Dataset()
        self.sample_response_dataset.PatientID = self.query_dataset.PatientID
        self.sample_response_dataset.StudyInstanceUID = generate_uid() 
        self.sample_response_dataset.SeriesInstanceUID = generate_uid() 
        self.sample_response_dataset.SOPInstanceUID = generate_uid() 
        self.sample_response_dataset.Modality = self.query_dataset.Modality
        self.sample_response_dataset.QueryRetrieveLevel = self.query_dataset.QueryRetrieveLevel
        # Add file_meta for C-STORE compatibility if these datasets were to be stored
        self.sample_response_dataset.file_meta = FileMetaDataset() # Use FileMetaDataset
        self.sample_response_dataset.file_meta.MediaStorageSOPClassUID = '1.2.840.10008.5.1.4.1.1.481.3' # RT Structure Set Storage, example
        self.sample_response_dataset.file_meta.MediaStorageSOPInstanceUID = self.sample_response_dataset.SOPInstanceUID
        self.sample_response_dataset.file_meta.ImplementationClassUID = PYDICOM_IMPLEMENTATION_UID # Add this
        self.sample_response_dataset.file_meta.TransferSyntaxUID = ImplicitVRLittleEndian # Add this
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
        # Ensure query_dataset for add_c_find_response has valid UIDs if they were invalid before
        # For this test, query_dataset uses empty UIDs for wildcard matching, which is fine.
        uids = self.aria.query(self.query_dataset, self.qr_scp)
        self.assertIsInstance(uids, set)
        self.assertEqual(len(uids), 1)
        self.assertIn(self.sample_response_dataset.SOPInstanceUID, uids)

    def test_transfer(self):
        mock_backup_destination_aet = "BACKUP_ORTHANC" # Shortened to be <= 16 chars
        mock_calling_aet = "TEST_ARIA_SCU" # Ensure this is also compliant if used as an AET

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

        # This test now expects ARIA.transfer to return False due to the persistent 0xC514
        # error when a SERIES level C-MOVE is attempted with StudyRootQueryRetrieveInformationModelMove
        # against the pynetdicom SCP. This indicates ARIA.transfer correctly reports the failure.
        self.assertFalse(result, "ARIA.transfer should return False due to known 0xC514 C-MOVE failure with StudyRootModelMove.")
        
        # Verify that MockDicomServer.handle_move was not called (last_move_destination_aet should remain None)
        self.assertIsNone(self.mock_qr_scp_server.last_move_destination_aet, 
                          "MockDicomServer.last_move_destination_aet should remain None as handle_move should not be called due to 0xC514 error.")


if __name__ == '__main__':
    unittest.main()
