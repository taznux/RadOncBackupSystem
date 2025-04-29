import unittest
from src.data_sources.mosaiq import Mosaiq

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
        self.rt_record_data = {
            'PatientID': '12345',
            'PatientName': 'John Doe',
            'PatientBirthDate': '19700101',
            'PatientSex': 'M',
            'PhysiciansOfRecord': 'Dr. Smith',
            'StudyDescription': 'Radiation Therapy',
            'TreatmentDate': '20220101',
            'NumberOfFractionsPlanned': 30,
            'CurrentFractionNumber': 5,
            'TreatmentMachineName': 'Machine1',
            'ReferencedSOPInstanceUID': '1.2.3.4.5.6.7.8.9.0',
            'StudyInstanceUID': '1.2.3.4.5.6.7.8.9.1'
        }
        self.store_scp = {
            'AETitle': 'STORE_SCP',
            'IP': '127.0.0.1',
            'Port': 105
        }

    def test_query(self):
        # Test the query method
        rows = self.mosaiq.query(self.sql_query, self.db_config)
        self.assertIsInstance(rows, list)

    def test_transfer(self):
        # Test the transfer method
        self.mosaiq.transfer(self.rt_record_data, self.store_scp)

if __name__ == '__main__':
    unittest.main()
