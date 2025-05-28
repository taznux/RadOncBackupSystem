import unittest
from unittest.mock import patch, MagicMock, call
import sys
from io import StringIO
import pyodbc # Required for Mosaiq class and error types

# Assuming src is in PYTHONPATH or handled by test runner
from src.data_sources.mosaiq import Mosaiq
# For CLI testing, we might need to import the main function of get_report.py
# from src.cli import get_report # This might need adjustment based on how get_report.py is structured

class TestMosaiqGetTreatmentSummaryReport(unittest.TestCase):
    """
    Tests for the get_treatment_summary_report method of the Mosaiq class.
    """

    def setUp(self):
        self.db_config = {
            'server': 'test_server',
            'database': 'test_db',
            'username': 'test_user',
            'password': 'test_password'
        }
        # Minimal Mosaiq instance, odbc_driver might not be strictly needed if self.query is mocked
        self.mosaiq = Mosaiq(odbc_driver="Test Driver")

    @patch('src.data_sources.mosaiq.Mosaiq.query')
    def test_get_report_success_with_data(self, mock_query):
        """Test successful retrieval of treatment summary report with data."""
        sample_rows = [
            ('Doe, John', 'MRN001', '2023-01-15', '2023-02-28', 50.0, 25, 'PTV_LUNG'),
            ('Doe, Jane', 'MRN002', '2023-03-10', '2023-04-20', 60.0, 30, 'PTV_BRAIN')
        ]
        mock_query.return_value = sample_rows
        
        expected_report = [
            {"PatientName": "Doe, John", "PatientMRN": "MRN001", "StartDate": "2023-01-15", "EndDate": "2023-02-28", "TotalDose": 50.0, "NumberOfFractions": 25, "TargetVolume": "PTV_LUNG"},
            {"PatientName": "Doe, Jane", "PatientMRN": "MRN002", "StartDate": "2023-03-10", "EndDate": "2023-04-20", "TotalDose": 60.0, "NumberOfFractions": 30, "TargetVolume": "PTV_BRAIN"}
        ]

        report = self.mosaiq.get_treatment_summary_report('MRN001', self.db_config)
        self.assertEqual(report, expected_report)
        mock_query.assert_called_once() 
        # We can also assert the SQL query string if it's stable and important
        args, _ = mock_query.call_args
        self.assertIn("Pat.Pat_ID1 = 'MRN001'", args[0])


    @patch('src.data_sources.mosaiq.Mosaiq.query')
    def test_get_report_success_no_data(self, mock_query):
        """Test successful retrieval when no treatment records are found."""
        mock_query.return_value = [] # Simulate no rows found
        
        report = self.mosaiq.get_treatment_summary_report('MRN003', self.db_config)
        self.assertEqual(report, [])
        mock_query.assert_called_once()
        args, _ = mock_query.call_args
        self.assertIn("Pat.Pat_ID1 = 'MRN003'", args[0])

    @patch('src.data_sources.mosaiq.Mosaiq.query')
    def test_get_report_with_start_and_end_dates(self, mock_query):
        """Test that start and end dates are correctly included in the SQL query."""
        mock_query.return_value = [] # Data itself is not important for this test
        
        self.mosaiq.get_treatment_summary_report('MRN004', self.db_config, start_date='2023-01-01', end_date='2023-12-31')
        
        mock_query.assert_called_once()
        args, _ = mock_query.call_args
        sql_query_arg = args[0]
        self.assertIn("Pat.Pat_ID1 = 'MRN004'", sql_query_arg)
        self.assertIn("AND TxFld.Plan_Start_DtTm >= '2023-01-01'", sql_query_arg)
        self.assertIn("AND TxFld.Plan_End_DtTm <= '2023-12-31'", sql_query_arg)

    @patch('src.data_sources.mosaiq.Mosaiq.query')
    def test_get_report_database_error_pyodbc(self, mock_query):
        """Test handling of pyodbc.Error during query execution."""
        # Simulate a pyodbc.Error (e.g., connection failure, SQL syntax error)
        mock_query.side_effect = pyodbc.Error("Simulated DB Error")
        
        # The method is expected to re-raise pyodbc.Error for critical issues
        with self.assertRaises(pyodbc.Error):
            self.mosaiq.get_treatment_summary_report('MRN005', self.db_config)
        mock_query.assert_called_once()

    @patch('src.data_sources.mosaiq.Mosaiq.query')
    def test_get_report_non_query_error_returns_empty(self, mock_query):
        """Test that a 'No results' pyodbc.Error that isn't critical returns an empty list."""
        # This specific error message is handled to return an empty list
        mock_query.side_effect = pyodbc.Error("HY000", "No results.  Previous SQL was not a query.")
        
        report = self.mosaiq.get_treatment_summary_report('MRN006', self.db_config)
        self.assertEqual(report, [])
        mock_query.assert_called_once()

    @patch('src.data_sources.mosaiq.Mosaiq.query')
    def test_get_report_unexpected_exception(self, mock_query):
        """Test handling of unexpected exceptions during query execution."""
        mock_query.side_effect = Exception("Some other unexpected error")

        with self.assertRaises(Exception) as context:
            self.mosaiq.get_treatment_summary_report('MRN007', self.db_config)
        self.assertTrue("Some other unexpected error" in str(context.exception))
        mock_query.assert_called_once()

# Placeholder for CLI tests - will be more complex
# Need to import the main function or a way to invoke the CLI script
# from src.cli import get_report as get_report_cli

class TestGetReportCLI(unittest.TestCase):
    """
    Tests for the src/cli/get_report.py command-line interface.
    """

    def setUp(self):
        # Patch configuration loading and Mosaiq data source interactions
        self.mock_load_config = patch('src.cli.get_report.load_config').start()
        self.mock_mosaiq_class = patch('src.cli.get_report.Mosaiq').start()
        
        # Configure the mock for load_config
        # It's called twice: for environments.toml and dicom.toml
        self.mock_environments = {
            'TJU_MOSAIQ': {
                'source': 'Mosaiq', 
                'mosaiq_odbc_driver': 'Test Driver For CLI'
            }
        }
        self.mock_db_configs = {
            'Mosaiq': { # This key must match 'source' in mock_environments
                'server': 'cli_server', 
                'database': 'cli_db', 
                'username': 'cli_user', 
                'password': 'cli_password'
            }
        }
        self.mock_load_config.side_effect = [
            self.mock_environments, # First call loads environments.toml
            self.mock_db_configs    # Second call loads dicom.toml
        ]

        # Configure the mock for Mosaiq instance and its method
        self.mock_mosaiq_instance = MagicMock()
        self.mock_mosaiq_class.return_value = self.mock_mosaiq_instance
        self.mock_mosaiq_instance.get_treatment_summary_report.return_value = [] # Default empty report

        # Capture stdout
        self.held_stdout = None # To store original stdout
        self.captured_output = None # To store captured output

    def tearDown(self):
        patch.stopall() # Stops all patches started with start()
        if self.held_stdout: # Restore stdout
            sys.stdout = self.held_stdout

    def _capture_stdout(self):
        self.held_stdout = sys.stdout
        self.captured_output = StringIO()
        sys.stdout = self.captured_output

    def _get_captured_stdout(self) -> str:
        if self.captured_output:
            return self.captured_output.getvalue()
        return ""

    def test_cli_valid_arguments_success_no_data(self):
        """Test CLI with valid arguments, resulting in no data found."""
        self._capture_stdout()
        # To test the CLI, we need to simulate command line arguments
        # and then call the main function of the script.
        # This requires importing the main function from src.cli.get_report
        try:
            from src.cli.get_report import main as get_report_main
            test_args = [
                '--environments_config', 'dummy_env.toml',
                '--dicom_config', 'dummy_dicom.toml',
                '--environment', 'TJU_MOSAIQ',
                '--mrn', 'CLI_MRN001'
            ]
            with patch.object(sys, 'argv', ['get_report.py'] + test_args):
                get_report_main()
        except SystemExit as e: # Should not exit for valid args
            self.fail(f"CLI exited unexpectedly for valid arguments: {e}")
        
        output = self._get_captured_stdout()
        self.assertIn("No treatment summary data found for MRN: CLI_MRN001", output)
        self.mock_mosaiq_instance.get_treatment_summary_report.assert_called_once_with(
            patient_mrn='CLI_MRN001',
            db_config=self.mock_db_configs['Mosaiq'], # Ensure the correct db_config is passed
            start_date=None,
            end_date=None
        )

    def test_cli_valid_arguments_with_data_and_dates(self):
        """Test CLI with valid arguments, dates, and data returned."""
        self._capture_stdout()
        sample_report_data = [
            {"PatientName": "Cli Test", "PatientMRN": "CLI_MRN002", "StartDate": "2023-01-15", "EndDate": "2023-02-28", "TotalDose": 50.0, "NumberOfFractions": 25, "TargetVolume": "PTV_CLI"}
        ]
        self.mock_mosaiq_instance.get_treatment_summary_report.return_value = sample_report_data
        
        try:
            from src.cli.get_report import main as get_report_main
            test_args = [
                '--environments_config', 'dummy_env.toml',
                '--dicom_config', 'dummy_dicom.toml',
                '--environment', 'TJU_MOSAIQ',
                '--mrn', 'CLI_MRN002',
                '--start_date', '2023-01-01',
                '--end_date', '2023-12-31'
            ]
            with patch.object(sys, 'argv', ['get_report.py'] + test_args):
                get_report_main()
        except SystemExit as e:
            self.fail(f"CLI exited unexpectedly for valid arguments: {e}")

        output = self._get_captured_stdout()
        self.assertIn("Treatment Summary Report for MRN: CLI_MRN002", output)
        self.assertIn("Date Range: 2023-01-01 to 2023-12-31", output)
        self.assertIn("Cli Test", output) # Check if patient name is in output
        self.assertIn("PTV_CLI", output)  # Check if target volume is in output
        
        self.mock_mosaiq_instance.get_treatment_summary_report.assert_called_once_with(
            patient_mrn='CLI_MRN002',
            db_config=self.mock_db_configs['Mosaiq'],
            start_date='2023-01-01',
            end_date='2023-12-31'
        )

    def test_cli_missing_required_mrn(self):
        """Test CLI exits if required --mrn argument is missing."""
        # argparse in the CLI script should cause a SystemExit
        # We need to import main for each test where it's called, or structure differently
        from src.cli.get_report import main as get_report_main 
        test_args = [
            '--environments_config', 'dummy_env.toml',
            '--dicom_config', 'dummy_dicom.toml',
            '--environment', 'TJU_MOSAIQ'
            # Missing --mrn
        ]
        with patch.object(sys, 'argv', ['get_report.py'] + test_args):
            with self.assertRaises(SystemExit) as cm:
                get_report_main()
        self.assertEqual(cm.exception.code, 2) # argparse exits with 2 for errors

    def test_cli_invalid_environment(self):
        """Test CLI exits if the specified environment is not found."""
        from src.cli.get_report import main as get_report_main
        self.mock_load_config.side_effect = [
            {'OTHER_ENV': {}}, # environments.toml does not contain TJU_MOSAIQ_BAD
            self.mock_db_configs
        ]
        test_args = [
            '--environments_config', 'dummy_env.toml',
            '--dicom_config', 'dummy_dicom.toml',
            '--environment', 'TJU_MOSAIQ_BAD', # This environment is not in mock_environments
            '--mrn', 'CLI_MRN003'
        ]
        with patch.object(sys, 'argv', ['get_report.py'] + test_args):
            with self.assertRaises(SystemExit) as cm:
                get_report_main()
        # Check that the exit code or error message indicates environment not found
        # The script sys.exit(1) in this case
        self.assertEqual(cm.exception.code, 1) 


if __name__ == '__main__':
    unittest.main()
