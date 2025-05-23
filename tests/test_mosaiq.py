import unittest
from src.data_sources.mosaiq import Mosaiq

class TestMosaiq(unittest.TestCase):

    def setUp(self):
        self.mosaiq = Mosaiq()

    def test_query(self):
        # Test the query method
        self.assertIsNone(self.mosaiq.query())

    def test_transfer(self):
        # Test the transfer method
        self.assertIsNone(self.mosaiq.transfer())

if __name__ == '__main__':
    unittest.main()
