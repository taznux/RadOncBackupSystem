"""
Treatment Report Generation CLI.

This script provides a command-line interface to generate a treatment summary report
from a Mosaiq data source. It uses environment configurations from `environments.toml` 
to determine the target Mosaiq database details.
"""
import argparse
import tomllib # Python 3.11+
import logging
import sys
import os # Added
from typing import Dict, Any, List, Optional

# Assuming src is in PYTHONPATH or handled by test runner
from src.data_sources.mosaiq import Mosaiq, MosaiqQueryError


# Configure logger for this module
logger = logging.getLogger(__name__)

# Define path to environments.toml
CONFIG_DIR = os.path.join(os.path.dirname(__file__), "..", "config")
ENVIRONMENTS_CONFIG_PATH = os.path.join(CONFIG_DIR, "environments.toml")


class ReportCliError(Exception):
    """Base class for errors specific to this CLI tool."""


class ConfigError(ReportCliError):
    """Raised for configuration-related errors."""


# load_toml_config function removed as it's only used once directly in main.

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
        description="Generate a treatment summary report from a Mosaiq data source configured in environments.toml."
    )
    parser.add_argument(
        "environment_name",
        help="Name of the environment to use (defined in environments.toml). This environment must contain a Mosaiq source.",
    )
    parser.add_argument(
        "mosaiq_source_alias",
        nargs='?',
        default=None,
        help="Alias of the Mosaiq source to use (from environment's [sources]). If not provided, uses default_source or the first available Mosaiq source in the environment."
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
        try:
            with open(ENVIRONMENTS_CONFIG_PATH, "rb") as f_binary:
                loaded_environments = tomllib.load(f_binary)
        except FileNotFoundError:
            raise ConfigError(f"Environments configuration file not found: {ENVIRONMENTS_CONFIG_PATH}") from None
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"Error decoding TOML file {ENVIRONMENTS_CONFIG_PATH}: {e}") from e

        env_block = loaded_environments.get(args.environment_name)
        if not env_block:
            raise ConfigError(f"Environment '{args.environment_name}' not found in {ENVIRONMENTS_CONFIG_PATH}.")

        actual_mosaiq_alias = args.mosaiq_source_alias
        if not actual_mosaiq_alias:
            actual_mosaiq_alias = env_block.get('default_source')
            if actual_mosaiq_alias:
                # Check if the default source is actually Mosaiq
                default_source_config = env_block.get('sources', {}).get(actual_mosaiq_alias, {})
                if default_source_config.get('type') != 'mosaiq':
                    logger.info(f"Default source '{actual_mosaiq_alias}' is not of type 'mosaiq'. Searching for a Mosaiq source.")
                    actual_mosaiq_alias = None # Clear to trigger search
        
        if not actual_mosaiq_alias: # If still no alias (not provided, or default wasn't Mosaiq)
            found_mosaiq = False
            for alias, config in env_block.get('sources', {}).items():
                if config.get('type') == 'mosaiq':
                    actual_mosaiq_alias = alias
                    found_mosaiq = True
                    logger.info(f"Using first available Mosaiq source found: '{actual_mosaiq_alias}'")
                    break
            if not found_mosaiq:
                raise ConfigError(f"No suitable Mosaiq source found in environment '{args.environment_name}'. Please specify one or ensure 'default_source' points to a Mosaiq type source.")

        all_sources_config = env_block.get('sources', {})
        mosaiq_db_config = all_sources_config.get(actual_mosaiq_alias)
        
        if not mosaiq_db_config:
            raise ConfigError(f"Configuration for Mosaiq source alias '{actual_mosaiq_alias}' not found in environment '{args.environment_name}'.")
        
        if mosaiq_db_config.get('type') != 'mosaiq':
            raise ConfigError(f"Selected source '{actual_mosaiq_alias}' is not of type 'mosaiq'. This script only supports Mosaiq sources.")

        logger.info(
            f"Selected environment: {args.environment_name}, Mosaiq Source Alias: {actual_mosaiq_alias}"
        )

        odbc_driver = mosaiq_db_config.get("odbc_driver")
        if not odbc_driver:
            logger.info( 
                f"ODBC driver not specified for Mosaiq source '{actual_mosaiq_alias}'. Using Mosaiq class default."
            )
        
        # The mosaiq_db_config dictionary itself contains db_server, db_database etc.
        mosaiq_source = Mosaiq(odbc_driver=odbc_driver)

        logger.info(f"Generating treatment summary report for MRN: {args.mrn}")
        report_data = mosaiq_source.get_treatment_summary_report(
            patient_mrn=args.mrn,
            db_config=mosaiq_db_config, # Pass the whole config dict for the source
            start_date=args.start_date,
            end_date=args.end_date,
        )

        _print_report_to_console(
            report_data, args.mrn, args.start_date, args.end_date
        )
        logger.info(
            f"Successfully generated and displayed report for MRN: {args.mrn}"
        )
        sys.exit(0)

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
