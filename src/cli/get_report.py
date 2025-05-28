"""
Treatment Report Generation CLI.

This script provides a command-line interface to generate a treatment summary report
from a Mosaiq data source. It uses environment configurations to determine
the target Mosaiq database details.

The main function `main` handles argument parsing, configuration loading,
and orchestrates the report generation via the Mosaiq data source.

Key functionalities:
- Load environment settings from `environments.toml`.
- Load Mosaiq database connection details from `dicom.toml` (using the Mosaiq entry).
- Construct parameters for the report (MRN, dates).
- Instantiate the Mosaiq data source class.
- Execute the report generation and print the results.

Usage:
    python -m src.cli.get_report --environments_config <path_to_environments.toml> \
                                 --dicom_config <path_to_dicom.toml> \
                                 --environment <env_name_for_mosaiq> \
                                 --mrn <mrn> \
                                 [--start_date <YYYY-MM-DD>] \
                                 [--end_date <YYYY-MM-DD>]

Example:
    python -m src.cli.get_report --environments_config src/config/environments.toml \
                                 --dicom_config src/config/dicom.toml \
                                 --environment TJU_MOSAIQ --mrn "PAT001" \
                                 --start_date "2023-01-01" --end_date "2023-12-31"
"""
import argparse
import tomllib
import logging
from src.data_sources.mosaiq import Mosaiq
import sys # For sys.exit

logger = logging.getLogger(__name__)

def load_config(config_file: str) -> dict:
    """
    Loads a TOML configuration file.

    :param config_file: Path to the TOML configuration file.
    :type config_file: str
    :return: A dictionary representing the loaded TOML configuration.
    :rtype: dict
    :raises FileNotFoundError: If the config file is not found.
    :raises tomllib.TOMLDecodeError: If the config file is not valid TOML.
    """
    logger.info(f"Loading configuration from: {config_file}")
    try:
        with open(config_file, 'rb') as f:
            return tomllib.load(f)
    except FileNotFoundError:
        logger.error(f"Configuration file not found: {config_file}")
        raise
    except tomllib.TOMLDecodeError as e:
        logger.error(f"Error decoding TOML file {config_file}: {e}")
        raise

def generate_report(mosaiq_source: Mosaiq, mrn: str, db_config: dict, start_date: str = None, end_date: str = None):
    """
    Generates and prints a treatment summary report using the Mosaiq data source.

    :param mosaiq_source: An initialized Mosaiq data source object.
    :type mosaiq_source: src.data_sources.mosaiq.Mosaiq
    :param mrn: The Medical Record Number of the patient.
    :type mrn: str
    :param db_config: A dictionary containing database connection parameters for Mosaiq.
    :type db_config: dict
    :param start_date: Optional start date for the report (YYYY-MM-DD).
    :type start_date: str, optional
    :param end_date: Optional end date for the report (YYYY-MM-DD).
    :type end_date: str, optional
    """
    logger.info(f"Generating treatment summary report for MRN: {mrn}")
    try:
        report_data = mosaiq_source.get_treatment_summary_report(
            patient_mrn=mrn,
            db_config=db_config,
            start_date=start_date,
            end_date=end_date
        )

        if not report_data:
            print(f"No treatment summary data found for MRN: {mrn} with the given date range.")
            return

        print(f"\nTreatment Summary Report for MRN: {mrn}")
        if start_date or end_date:
            print(f"Date Range: {start_date if start_date else 'N/A'} to {end_date if end_date else 'N/A'}")
        print("=" * 80)
        
        # Assuming report_data is a list of dictionaries
        # Print header based on keys of the first record, if data exists
        if report_data:
            headers = report_data[0].keys()
            header_string = " | ".join(f"{str(header):<15}" for header in headers) # Adjust width as needed
            print(header_string)
            print("-" * len(header_string))
            for record in report_data:
                row_string = " | ".join(f"{str(record.get(header, 'N/A')):<15}" for header in headers) # Adjust width
                print(row_string)
        
        print("=" * 80)
        logger.info(f"Successfully generated and printed report for MRN: {mrn}")

    except Exception as e:
        logger.error(f"Failed to generate treatment summary report for MRN {mrn}: {e}", exc_info=True)
        print(f"Error generating report: {e}")


def main():
    """
    Main function to parse arguments, load configuration, and generate a treatment report.
    """
    parser = argparse.ArgumentParser(description="Generate a treatment summary report from Mosaiq.")
    parser.add_argument('--environments_config', required=True, help="Path to the environments configuration file (e.g., environments.toml).")
    parser.add_argument('--dicom_config', required=True, help="Path to the DICOM/Database configuration file (e.g., dicom.toml). This file should contain Mosaiq DB connection details.")
    parser.add_argument('--environment', required=True, help="Name of the Mosaiq environment to use (e.g., TJU_MOSAIQ).")
    parser.add_argument('--mrn', required=True, help="Medical Record Number for the report.")
    parser.add_argument('--start_date', help="Optional start date for the report (YYYY-MM-DD).")
    parser.add_argument('--end_date', help="Optional end date for the report (YYYY-MM-DD).")
    
    args = parser.parse_args()

    # Basic logging setup for CLI
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    try:
        # Load configurations
        environments = load_config(args.environments_config)
        # dicom.toml is used for AE details but also for Mosaiq DB details under a "Mosaiq" key.
        db_configs = load_config(args.dicom_config) 
    except Exception as e:
        logger.critical(f"Failed to load initial configurations: {e}")
        sys.exit(1) # Exit if configs can't be loaded

    # Get specific environment configuration
    if args.environment not in environments:
        logger.error(f"Environment '{args.environment}' not found in environments configuration file.")
        print(f"Error: Environment '{args.environment}' not found. Check your environments.toml.")
        sys.exit(1)
    env_config = environments[args.environment]

    # Determine the data source name from environment config (e.g., 'source = "Mosaiq"')
    data_source_name = env_config.get('source') or env_config.get('source1')
    if not data_source_name or data_source_name.lower() != 'mosaiq':
        logger.error(f"Environment '{args.environment}' does not specify 'Mosaiq' as its source type or source is not defined.")
        print(f"Error: The source for environment '{args.environment}' must be 'Mosaiq'. Found: {data_source_name}")
        sys.exit(1)
    
    logger.info(f"Selected environment: {args.environment}, Data source: {data_source_name}")

    # Get the Database configuration for Mosaiq
    # It's assumed that dicom.toml contains a top-level key (e.g., "Mosaiq") 
    # which holds the DB connection details.
    # This key name "Mosaiq" should match what's in dicom.toml for Mosaiq DB settings.
    mosaiq_db_key = data_source_name # Assuming the source name in env_config matches the key in dicom_config
    if mosaiq_db_key not in db_configs:
        logger.error(f"Mosaiq DB configuration for key '{mosaiq_db_key}' not found in {args.dicom_config}.")
        print(f"Error: Mosaiq DB configuration for '{mosaiq_db_key}' not found. Check your {args.dicom_config}.")
        sys.exit(1)
    
    db_connection_details = db_configs[mosaiq_db_key]

    # Instantiate Mosaiq source
    # The Mosaiq class constructor takes odbc_driver. This might be in env_config or db_connection_details
    odbc_driver = env_config.get('mosaiq_odbc_driver') or db_connection_details.get('odbc_driver') # Prefer env_config
    
    if not odbc_driver:
        # Fallback to default if not found, or could make it mandatory
        logger.warning(f"ODBC driver not specified for Mosaiq in environment '{args.environment}' or in DB config. Using default.")
        # Mosaiq class has a DEFAULT_ODBC_DRIVER, so this is okay.
    
    try:
        mosaiq_source = Mosaiq(odbc_driver=odbc_driver)
        
        # Generate the report
        generate_report(
            mosaiq_source=mosaiq_source,
            mrn=args.mrn,
            db_config=db_connection_details, # Pass the full dict which includes server, database, username, password
            start_date=args.start_date,
            end_date=args.end_date
        )
    except Exception as e:
        logger.critical(f"An unexpected error occurred during report generation: {e}", exc_info=True)
        print(f"An critical error occurred: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
