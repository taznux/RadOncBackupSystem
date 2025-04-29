import unittest
from src.data_sources.mim import MIM

class TestMIM(unittest.TestCase):

    def setUp(self):
        self.mim = MIM()

    def test_query(self):
        # Test the query method
        self.assertIsNone(self.mim.query())

    def test_transfer(self):
        # Test the transfer method
        self.assertIsNone(self.mim.transfer())

if __name__ == '__main__':
    unittest.main()
