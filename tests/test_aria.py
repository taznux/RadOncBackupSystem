import unittest
from src.data_sources.aria import ARIA

class TestARIA(unittest.TestCase):

    def setUp(self):
        self.aria = ARIA()

    def test_query(self):
        # Test the query method
        self.assertIsNone(self.aria.query())

    def test_transfer(self):
        # Test the transfer method
        self.assertIsNone(self.aria.transfer())

if __name__ == '__main__':
    unittest.main()
