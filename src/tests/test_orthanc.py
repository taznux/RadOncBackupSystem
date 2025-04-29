import unittest
from src.backup_systems.orthanc import Orthanc

class TestOrthanc(unittest.TestCase):
    def setUp(self):
        self.orthanc = Orthanc()

    def test_store(self):
        data = "test_data"
        result = self.orthanc.store(data)
        self.assertIsNone(result)  # Assuming store method returns None

    def test_verify(self):
        data = "test_data"
        result = self.orthanc.verify(data)
        self.assertIsNone(result)  # Assuming verify method returns None

if __name__ == '__main__':
    unittest.main()
