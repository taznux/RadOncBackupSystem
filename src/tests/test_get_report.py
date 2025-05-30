import unittest
from unittest.mock import patch, MagicMock, mock_open # call removed as unused
import sys
from io import StringIO
import os

# Adjust path to import from src
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.data_sources.mosaiq import Mosaiq, MosaiqQueryError
from src.cli.get_report import (
    main as get_report_main,
    ConfigError,
    _print_report_to_console,  # Import helper for direct testing
    # load_toml_config, # Not directly tested here, mocked in CLI tests
)


class TestMosaiqGetTreatmentSummaryReport(unittest.TestCase):
    """
    Tests for the get_treatment_summary_report method of the Mosaiq class.
    """

    def setUp(self):
        self.db_config = {
            "server": "test_server",
            "database": "test_db",
            "username": "test_user",
            "password": "test_password",
        }
        self.mosaiq = Mosaiq(odbc_driver="Test Driver")
        self.expected_columns = Mosaiq._TREATMENT_SUMMARY_COLUMNS
        self.addCleanup(patch.stopall)

    @patch("src.data_sources.mosaiq.Mosaiq.query")
    def test_get_report_success_with_data(self, mock_query):
        """Test successful retrieval of treatment summary report with data."""
        sample_rows = [
            ("Doe, John", "MRN001", "2023-01-15", "2023-02-28", 50.0, 25, "PTV_LUNG"),
            ("Doe, Jane", "MRN002", "2023-03-10", "2023-04-20", 60.0, 30, "PTV_BRAIN"),
        ]
        mock_query.return_value = [tuple(row) for row in sample_rows]

        expected_report = [
            dict(zip(self.expected_columns, sample_rows[0])),
            dict(zip(self.expected_columns, sample_rows[1])),
        ]

        # _build_treatment_summary_sql parameters changed: now takes list of params
        with patch.object(
            self.mosaiq, "_build_treatment_summary_sql", return_value=("DUMMY SQL", ["MRN001"])
        ) as mock_build_sql:
            report = self.mosaiq.get_treatment_summary_report(
                "MRN001", self.db_config
            )

        self.assertEqual(report, expected_report)
        mock_build_sql.assert_called_once_with("MRN001", None, None)
        mock_query.assert_called_once_with("DUMMY SQL", self.db_config, params=["MRN001"])

    @patch("src.data_sources.mosaiq.Mosaiq.query")
    def test_get_report_success_no_data(self, mock_query):
        """Test successful retrieval when no treatment records are found."""
        mock_query.return_value = []

        with patch.object(
            self.mosaiq, "_build_treatment_summary_sql", return_value=("DUMMY SQL", ["MRN003"])
        ):
            report = self.mosaiq.get_treatment_summary_report(
                "MRN003", self.db_config
            )

        self.assertEqual(report, [])
        mock_query.assert_called_once_with("DUMMY SQL", self.db_config, params=["MRN003"])

    @patch("src.data_sources.mosaiq.Mosaiq.query")
    def test_get_report_with_start_and_end_dates_sql_build(self, mock_query):
        """Test that _build_treatment_summary_sql is called with correct dates."""
        mock_query.return_value = []
        expected_params = ["MRN004", "2023-01-01", "2023-12-31"]
        with patch.object(
            self.mosaiq, "_build_treatment_summary_sql", return_value=("DUMMY SQL DATES", expected_params)
        ) as mock_build_sql:
            self.mosaiq.get_treatment_summary_report(
                "MRN004", self.db_config, start_date="2023-01-01", end_date="2023-12-31"
            )

        mock_build_sql.assert_called_once_with("MRN004", "2023-01-01", "2023-12-31")
        mock_query.assert_called_once_with("DUMMY SQL DATES", self.db_config, params=expected_params)

    def test_build_treatment_summary_sql_logic(self):
        """Test the internal _build_treatment_summary_sql method directly."""
        sql_mrn_only, params_mrn_only = self.mosaiq._build_treatment_summary_sql(
            "MRN001", None, None
        )
        self.assertIn("ID.IDA = ?", sql_mrn_only) # Check for placeholder
        self.assertNotIn("AND TxFld.Start_DtTm >=", sql_mrn_only)
        self.assertEqual(params_mrn_only, ["MRN001"])

        sql_with_start, params_with_start = self.mosaiq._build_treatment_summary_sql(
            "MRN002", "2023-01-01", None
        )
        self.assertIn("AND TxFld.Start_DtTm >= ?", sql_with_start) # Check for placeholder
        self.assertEqual(params_with_start, ["MRN002", "2023-01-01"])

        sql_with_end, params_with_end = self.mosaiq._build_treatment_summary_sql(
            "MRN003", None, "2023-12-31"
        )
        self.assertIn("AND TxFld.Last_Tx_DtTm <= ?", sql_with_end) # Check for placeholder
        self.assertEqual(params_with_end, ["MRN003", "2023-12-31"])


    @patch("src.data_sources.mosaiq.Mosaiq.query")
    def test_get_report_raises_mosaiq_query_error(self, mock_query):
        """Test that MosaiqQueryError from self.query is re-raised."""
        mock_query.side_effect = MosaiqQueryError(
            "Simulated DB Error from query method"
        )

        with self.assertRaises(MosaiqQueryError) as context:
            self.mosaiq.get_treatment_summary_report("MRN005", self.db_config)
        self.assertIn("Simulated DB Error from query method", str(context.exception))

    @patch("src.data_sources.mosaiq.Mosaiq.query")
    def test_get_report_column_mismatch_raises_value_error(self, mock_query):
        """Test ValueError is raised if query returns unexpected number of columns."""
        sample_rows_malformed = [("Doe, John", "MRN001", "2023-01-15")]  # 3 cols
        mock_query.return_value = [tuple(row) for row in sample_rows_malformed]

        with self.assertRaises(ValueError) as context:
            self.mosaiq.get_treatment_summary_report("MRN008", self.db_config)
        self.assertIn(
            "Mismatch between expected columns and query result columns",
            str(context.exception),
        )

    @patch("src.data_sources.mosaiq.Mosaiq.query")
    def test_get_report_unexpected_exception_during_processing(self, mock_query):
        """Test handling of unexpected exceptions during data processing."""
        sample_rows = [
            ("Doe, John", "MRN001", "2023-01-15", "2023-02-28", 50.0, 25, "PTV_LUNG")
        ]
        mock_query.return_value = [tuple(row) for row in sample_rows]

        with patch("builtins.zip", side_effect=TypeError("Simulated zipping error")):
            with self.assertRaises(TypeError) as context:
                self.mosaiq.get_treatment_summary_report("MRN007", self.db_config)
            self.assertIn("Simulated zipping error", str(context.exception))


class TestGetReportCLI(unittest.TestCase):
    """
    Tests for the src/cli/get_report.py command-line interface.
    """

    def setUp(self):
        # Patch open and tomllib.load for environments.toml
        self.mock_open_patcher = patch("builtins.open", new_callable=mock_open)
        self.mock_file_open = self.mock_open_patcher.start()
        
        self.mock_toml_load_patcher = patch("src.cli.get_report.tomllib.load")
        self.mock_toml_load = self.mock_toml_load_patcher.start()

        self.mock_mosaiq_class = patch("src.cli.get_report.Mosaiq").start()

        # Define a more complete mock environments.toml structure
        self.mock_environments_content = {
            "TJU_MOSAIQ_ENV": { 
                "description": "Test TJU Mosaiq Environment",
                "default_source": "MOSAIQ_PRIMARY_TJU",
                "sources": {
                    "MOSAIQ_PRIMARY_TJU": {
                        "type": "mosaiq",
                        "db_server": "tju_db_server_cli",
                        "db_database": "tju_mosaiq_db_cli",
                        "db_username": "tju_user_cli",
                        "db_password": "tju_password_placeholder_cli",
                        "odbc_driver": "TJU Test Driver CLI"
                    },
                    "MOSAIQ_SECONDARY_TJU": { 
                        "type": "mosaiq",
                        "db_server": "tju_db_server2_cli",
                        "odbc_driver": "TJU Test Driver 2 CLI"
                    }
                }
            }
        }
        self.mock_toml_load.return_value = self.mock_environments_content

        self.mock_mosaiq_instance = MagicMock()
        self.mock_mosaiq_class.return_value = self.mock_mosaiq_instance
        self.mock_mosaiq_instance.get_treatment_summary_report.return_value = []

        self.held_stdout = None
        self.captured_output = None
        self.addCleanup(patch.stopall)


    def _capture_stdout(self):
        self.held_stdout = sys.stdout
        self.captured_output = StringIO()
        sys.stdout = self.captured_output

    def _get_captured_stdout(self) -> str:
        return self.captured_output.getvalue() if self.captured_output else ""

    @patch("src.cli.get_report._print_report_to_console")
    def test_cli_success_no_data(self, mock_print_report):
        """Test CLI with valid arguments, resulting in no data found."""
        test_args = [
            "get_report.py", 
            "TJU_MOSAIQ_ENV", # environment_name
            # mosaiq_source_alias will use default_source: MOSAIQ_PRIMARY_TJU
            "--mrn", "CLI_MRN001",
        ]
        with patch.object(sys, "argv", test_args):
            with self.assertRaises(SystemExit) as cm:
                get_report_main()
            self.assertEqual(cm.exception.code, 0) # Expect success exit

        mock_print_report.assert_called_once_with([], "CLI_MRN001", None, None)
        
        # Assert Mosaiq was initialized with the correct ODBC driver
        self.mock_mosaiq_class.assert_called_once_with(odbc_driver="TJU Test Driver CLI")
        
        # Assert get_treatment_summary_report was called with correct db_config
        expected_db_config = self.mock_environments_content["TJU_MOSAIQ_ENV"]["sources"]["MOSAIQ_PRIMARY_TJU"]
        self.mock_mosaiq_instance.get_treatment_summary_report.assert_called_once_with(
            patient_mrn="CLI_MRN001",
            db_config=expected_db_config,
            start_date=None,
            end_date=None,
        )

    @patch("src.cli.get_report._print_report_to_console")
    def test_cli_success_with_data_and_dates(self, mock_print_report):
        """Test CLI with valid arguments, dates, and data returned."""
        sample_report_data = [
            {
                "PatientName": "Cli Test",
                "PatientMRN": "CLI_MRN002",
                "StartDate": "2023-01-15",
            }
        ]
        self.mock_mosaiq_instance.get_treatment_summary_report.return_value = (
            sample_report_data
        )

        test_args = [
            "get_report.py", "TJU_MOSAIQ_ENV", "MOSAIQ_SECONDARY_TJU", # Specify source alias
            "--mrn", "CLI_MRN002",
            "--start_date", "2023-01-01",
            "--end_date", "2023-12-31",
        ]
        with patch.object(sys, "argv", test_args):
            with self.assertRaises(SystemExit) as cm:
                get_report_main()
            self.assertEqual(cm.exception.code, 0)


        mock_print_report.assert_called_once_with(
            sample_report_data, "CLI_MRN002", "2023-01-01", "2023-12-31"
        )
        # Assert Mosaiq was initialized with the correct ODBC driver for the specified alias
        self.mock_mosaiq_class.assert_called_once_with(odbc_driver="TJU Test Driver 2 CLI")
        # Assert get_treatment_summary_report was called with correct db_config for the alias
        expected_db_config_secondary = self.mock_environments_content["TJU_MOSAIQ_ENV"]["sources"]["MOSAIQ_SECONDARY_TJU"]
        self.mock_mosaiq_instance.get_treatment_summary_report.assert_called_once_with(
            patient_mrn="CLI_MRN002",
            db_config=expected_db_config_secondary,
            start_date="2023-01-01",
            end_date="2023-12-31"
        )


    def test_cli_missing_required_mrn_exits(self):
        """Test CLI exits if required --mrn argument is missing."""
        test_args = [
            "get_report.py", "TJU_MOSAIQ_ENV", # Missing --mrn
        ]
        with patch.object(sys, "argv", test_args):
            with self.assertRaises(SystemExit) as cm:
                get_report_main()
        self.assertEqual(cm.exception.code, 2)

    def test_cli_config_file_not_found_exits(self):
        """Test CLI exits if a config file is not found (ConfigError)."""
        # Simulate FileNotFoundError when open is called for environments.toml
        self.mock_file_open.side_effect = FileNotFoundError("environments.toml not found")
        # self.mock_load_toml_config is no longer used for this specific scenario

        test_args = [
            "get_report.py", "ANY_ENV", # Script name for sys.argv
            "--mrn", "M",
        ]
        with patch.object(sys, "argv", test_args):
            with self.assertRaises(SystemExit) as cm:
                get_report_main()
        self.assertEqual(cm.exception.code, 1)
        # Check that the error message printed to stderr (by main's except block) contains the relevant info
        # This requires capturing stderr.
        # For simplicity, we assume the logger.error call in main() or ConfigError itself is sufficient.

    def test_cli_mosaiq_query_error_exits(self):
        """Test CLI exits if Mosaiq query fails (MosaiqQueryError)."""
        self.mock_mosaiq_instance.get_treatment_summary_report.side_effect = (
            MosaiqQueryError("DB connection failed")
        )

        test_args = [
            "get_report.py", "TJU_MOSAIQ_ENV",
            "--mrn", "M",
        ]
        with patch.object(sys, "argv", test_args):
            with self.assertRaises(SystemExit) as cm:
                get_report_main()
        self.assertEqual(cm.exception.code, 1)


class TestPrintReportToConsole(unittest.TestCase):
    """Tests for the _print_report_to_console helper function."""

    def setUp(self):
        self.held_stdout = sys.stdout
        self.captured_output = StringIO()
        sys.stdout = self.captured_output
        self.addCleanup(patch.stopall) # Though no patches started in this class's setUp

    def tearDown(self): # Not strictly needed if using addCleanup
        sys.stdout = self.held_stdout

    def test_print_no_data(self):
        _print_report_to_console([], "MRN123", None, None)
        output = self.captured_output.getvalue()
        self.assertIn("No treatment summary data found for MRN: MRN123", output)

    def test_print_with_data_and_dates(self):
        report_data = [
            {"PatientName": "Doe, John", "MRN": "MRN123", "Dose": 50, "Site": "Lung"},
            {"PatientName": "Doe, Jane", "MRN": "MRN456", "Dose": 60, "Site": "Brain"},
        ]
        _print_report_to_console(report_data, "MRN123", "2023-01-01", "2023-12-31")
        output = self.captured_output.getvalue()

        self.assertIn("Treatment Summary Report for MRN: MRN123", output)
        self.assertIn("Date Range: 2023-01-01 to 2023-12-31", output)
        self.assertRegex(output, r"PatientName\s*\|\s*MRN\s*\|\s*Dose\s*\|\s*Site") # Header with flexible spacing
        self.assertIn("Doe, John", output)
        self.assertIn("Lung", output)
        self.assertIn("Doe, Jane", output)
        self.assertIn("Brain", output)


if __name__ == "__main__":
    unittest.main()
