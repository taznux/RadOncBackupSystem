"""
Treatment Report Generation CLI.

This script provides a command-line interface to generate a treatment summary report
from a Mosaiq data source. It uses environment configurations to determine
the target Mosaiq database details.
"""
import argparse
import tomllib # Python 3.11+
import logging
import sys
from typing import Dict, Any, List, Optional

# Assuming src is in PYTHONPATH or handled by test runner
from src.data_sources.mosaiq import Mosaiq, MosaiqQueryError


# Configure logger for this module
logger = logging.getLogger(__name__)


class ReportCliError(Exception):
    """Base class for errors specific to this CLI tool."""


class ConfigError(ReportCliError):
    """Raised for configuration-related errors."""


def load_toml_config(config_file_path: str) -> Dict[str, Any]:
    """
    Loads a TOML configuration file.

    Args:
        config_file_path: Path to the TOML configuration file.

    Returns:
        A dictionary representing the loaded TOML configuration.

    Raises:
        ConfigError: If the file is not found or is not valid TOML.
    """
    logger.info(f"Loading configuration from: {config_file_path}")
    try:
        with open(config_file_path, "rb") as f_binary:
            return tomllib.load(f_binary)
    except FileNotFoundError:
        msg = f"Configuration file not found: {config_file_path}"
        logger.error(msg)
        raise ConfigError(msg) from None
    except tomllib.TOMLDecodeError as e:
        msg = f"Error decoding TOML file {config_file_path}: {e}"
        logger.error(msg)
        raise ConfigError(msg) from e


def _print_report_to_console(
    report_data: List[Dict[str, Any]],
    mrn: str,
    start_date: Optional[str],
    end_date: Optional[str],
):
    """
    Formats and prints the treatment summary report to the console.

    Args:
        report_data: A list of dictionaries, where each dictionary is a record.
        mrn: The patient's MRN.
        start_date: Optional start date used for filtering.
        end_date: Optional end date used for filtering.
    """
    if not report_data:
        print(
            f"No treatment summary data found for MRN: {mrn} with the given date range."
        )
        return

    print(f"\nTreatment Summary Report for MRN: {mrn}")
    if start_date or end_date:
        print(f"Date Range: {start_date or 'N/A'} to {end_date or 'N/A'}")
    
    # Determine headers from the first record, if data exists
    # Assumes all records have the same keys, which is true for Mosaiq._TREATMENT_SUMMARY_COLUMNS
    headers = list(report_data[0].keys()) if report_data else []
    
    # Calculate appropriate column widths
    # Initialize with header lengths or a minimum width
    min_col_width = 10 
    col_widths = {
        header: max(len(header), min_col_width) + 2 for header in headers
    }
    if report_data:
        for record in report_data:
            for header in headers:
                col_widths[header] = max(
                    col_widths[header], len(str(record.get(header, ""))) + 2
                )
    
    # Construct dynamic separator and header string
    if not headers: # No data, no headers
        separator_line = "=" * 100
        header_string = ""
    else:
        header_string = " | ".join(
            f"{header:<{col_widths[header]}}" for header in headers
        )
        separator_line = "-" * len(header_string)

    print("=" * (len(header_string) if header_string else 100)) # Top border
    if header_string:
        print(header_string)
        print(separator_line)

    for record in report_data:
        row_string = " | ".join(
            f"{str(record.get(header, 'N/A')):<{col_widths[header]}}"
            for header in headers
        )
        print(row_string)
    
    if report_data: # Print bottom border only if there was data
        print("=" * len(header_string))


def _create_argument_parser() -> argparse.ArgumentParser:
    """Creates and configures the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        description="Generate a treatment summary report from Mosaiq."
    )
    parser.add_argument(
        "--environments_config",
        required=True,
        help="Path to the environments configuration file (e.g., environments.toml).",
    )
    parser.add_argument(
        "--dicom_config",
        required=True,
        help=(
            "Path to the DICOM/Database configuration file (e.g., dicom.toml). "
            "This file should contain Mosaiq DB connection details."
        ),
    )
    parser.add_argument(
        "--environment",
        required=True,
        help="Name of the Mosaiq environment to use (e.g., TJU_MOSAIQ).",
    )
    parser.add_argument(
        "--mrn", required=True, help="Medical Record Number for the report."
    )
    parser.add_argument(
        "--start_date", help="Optional start date for the report (YYYY-MM-DD)."
    )
    parser.add_argument(
        "--end_date", help="Optional end date for the report (YYYY-MM-DD)."
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging."
    )
    return parser


def main():
    """
    Main function to parse arguments, load configuration, and generate a treatment report.
    """
    parser = _create_argument_parser()
    args = parser.parse_args()

    # Setup logging based on verbosity
    log_level = logging.DEBUG if args.verbose else logging.INFO
    # Configure root logger if no handlers are configured.
    # This is a simple way for CLI apps; more complex apps might have dedicated logging setup.
    if not logging.getLogger().hasHandlers():
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            stream=sys.stdout, # Default to stdout for CLI tools
        )
    else: # If handlers are already configured (e.g. if imported), just set level
        logging.getLogger().setLevel(log_level)
    
    # Ensure our specific logger also adheres to this level
    logger.setLevel(log_level)


    try:
        environments = load_toml_config(args.environments_config)
        db_configs_all = load_toml_config(args.dicom_config)

        if args.environment not in environments:
            raise ConfigError(
                f"Environment '{args.environment}' not found in {args.environments_config}."
            )
        env_config = environments[args.environment]

        data_source_name = env_config.get("source") or env_config.get("source1")
        if not data_source_name or data_source_name.lower() != "mosaiq":
            raise ConfigError(
                f"Environment '{args.environment}' must specify 'Mosaiq' as its source type. "
                f"Found: {data_source_name or 'None'}."
            )

        logger.info(
            f"Selected environment: {args.environment}, Data source: {data_source_name}"
        )

        if data_source_name not in db_configs_all:
            raise ConfigError(
                f"Database configuration for source '{data_source_name}' not found in {args.dicom_config}."
            )
        db_connection_details = db_configs_all[data_source_name]

        odbc_driver = env_config.get(
            "mosaiq_odbc_driver"
        ) or db_connection_details.get("odbc_driver")
        if not odbc_driver: # odbc_driver can be None if relying on Mosaiq class default
            logger.info( # Changed from warning to info as it's an expected fallback
                f"ODBC driver not specified for Mosaiq in environment '{args.environment}' "
                "or in DB config. Using Mosaiq class default."
            )

        mosaiq_source = Mosaiq(odbc_driver=odbc_driver)

        logger.info(f"Generating treatment summary report for MRN: {args.mrn}")
        report_data = mosaiq_source.get_treatment_summary_report(
            patient_mrn=args.mrn,
            db_config=db_connection_details,
            start_date=args.start_date,
            end_date=args.end_date,
        )

        _print_report_to_console(
            report_data, args.mrn, args.start_date, args.end_date
        )
        logger.info(
            f"Successfully generated and displayed report for MRN: {args.mrn}"
        )
        sys.exit(0) # Explicit success exit

    except (ConfigError, MosaiqQueryError, ValueError) as e:
        logger.error(f"Report generation failed: {e}", exc_info=args.verbose)
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        logger.critical(
            f"An unexpected critical error occurred during report generation: {e}",
            exc_info=True,
        )
        print(f"An unexpected critical error occurred: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
