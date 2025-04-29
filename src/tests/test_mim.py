import unittest
from pydicom.dataset import Dataset
from src.data_sources.mim import MIM

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

        self.get_dataset = Dataset()
        self.get_dataset.QueryRetrieveLevel = 'IMAGE'
        self.get_dataset.SOPInstanceUID = '1.2.3.4.5.6.7.8.9.0'

        self.qr_scp = {
            'AETitle': 'QR_SCP',
            'IP': '127.0.0.1',
            'Port': 104
        }

        self.store_scp = {
            'AETitle': 'STORE_SCP',
            'IP': '127.0.0.1',
            'Port': 105
        }

    def test_query(self):
        # Test the query method
        uids = self.mim.query(self.query_dataset, self.qr_scp)
        self.assertIsInstance(uids, set)

    def test_transfer(self):
        # Test the transfer method
        def handle_store(event):
            return 0x0000

        self.mim.transfer(self.get_dataset, self.qr_scp, self.store_scp, handle_store)

if __name__ == '__main__':
    unittest.main()
