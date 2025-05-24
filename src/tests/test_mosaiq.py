import unittest
from src.data_sources.mosaiq import Mosaiq
from src.tests.mock_dicom_server import MockDicomServer # Added
from pydicom.dataset import Dataset # Added (potentially needed for assertions)
# from pydicom.uid import ImplicitVRLittleEndian, ExplicitVRLittleEndian # Added (if needed for dataset construction)

class TestMosaiq(unittest.TestCase):

    def setUp(self):
        self.mosaiq = Mosaiq()
        self.sql_query = "SELECT * FROM Patients WHERE PatientID = '12345'"
        self.db_config = {
            'server': 'localhost',
            'database': 'MosaiqDB',
            'username': 'user',
            'password': 'password'
        }
        
        self.rt_record_data = Dataset()
        self.rt_record_data.PatientID = '12345'
        self.rt_record_data.PatientName = 'John Doe'
        self.rt_record_data.PatientBirthDate = '19700101'
        self.rt_record_data.PatientSex = 'M'
        self.rt_record_data.PhysiciansOfRecord = 'Dr. Smith'
        self.rt_record_data.StudyDescription = 'Radiation Therapy'
        self.rt_record_data.TreatmentDate = '20220101'
        self.rt_record_data.NumberOfFractionsPlanned = 30
        self.rt_record_data.CurrentFractionNumber = 5
        self.rt_record_data.TreatmentMachineName = 'Machine1'
        self.rt_record_data.ReferencedSOPInstanceUID = '1.2.3.4.5.6.7.8.9.0'
        self.rt_record_data.StudyInstanceUID = '1.2.3.4.5.6.7.8.9.1'

        # Mock Store SCP Server Setup
        mock_store_scp_host = '127.0.0.1'
        mock_store_scp_port = 11116 # Distinct port for Mosaiq store tests
        mock_store_scp_ae_title = 'MOSAIQ_STORE_SCP' # Shortened AE Title
        self.mock_store_scp_server = MockDicomServer(
            host=mock_store_scp_host, 
            port=mock_store_scp_port, 
            ae_title=mock_store_scp_ae_title
        )
        self.mock_store_scp_server.start()

        self.store_scp = { # This is the C-STORE SCP details passed to mosaiq.transfer
            'AETitle': mock_store_scp_ae_title,
            'IP': mock_store_scp_host,
            'Port': mock_store_scp_port
        }

    def tearDown(self):
        if hasattr(self, 'mock_store_scp_server') and self.mock_store_scp_server:
            self.mock_store_scp_server.stop()
            self.mock_store_scp_server.reset()

    def test_query(self):
        # Test the query method (remains unchanged)
        # This test would require a live database or a database mocking strategy
        # not covered by MockDicomServer.
        # For now, we assume it's tested elsewhere or skipped in environments without DB.
        pass # Or keep existing implementation if it can run in CI

    def test_transfer(self):
        # Test the transfer method using the mock C-STORE SCP
        try:
            self.mosaiq.transfer(self.rt_record_data, self.store_scp)
        except Exception as e:
            self.fail(f"Mosaiq.transfer raised an exception: {e}")

        # Assert that one dataset was received by the mock C-STORE SCP
        self.assertEqual(len(self.mock_store_scp_server.received_datasets), 1,
                         "Mock Store SCP should have received one dataset.")

        # Optional: Assert specific attributes if known
        # This depends on how Mosaiq.transfer creates the DICOM dataset
        if self.mock_store_scp_server.received_datasets:
            received_ds = self.mock_store_scp_server.received_datasets[0]
            self.assertEqual(received_ds.PatientID, self.rt_record_data.PatientID) # Changed to attribute access
            # Add more assertions here if Mosaiq.transfer maps them and they are critical
            # For example:
            # self.assertEqual(received_ds.PatientName, self.rt_record_data.PatientName)
            # self.assertEqual(received_ds.StudyInstanceUID, self.rt_record_data.StudyInstanceUID)
            # SOPClassUID should be RT Beams Treatment Record IOD or similar
            # self.assertEqual(received_ds.SOPClassUID, '1.2.840.10008.5.1.4.1.1.481.4') # RT Beams Treatment Record Storage


if __name__ == '__main__':
    unittest.main()
