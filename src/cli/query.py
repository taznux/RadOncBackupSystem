"""
DICOM Query CLI.

This script provides a command-line interface to perform DICOM C-FIND queries
against configured data sources (ARIA, MIM, Mosaiq). It uses environment
configurations to determine the target DICOM AE (Application Entity) details
and other source-specific parameters like ODBC drivers for Mosaiq.

The main function `main` handles argument parsing, configuration loading,
and orchestrates the query operation via `query_data_source`.

Key functionalities:
- Load environment settings from `environments.toml`.
- Load DICOM AE details from `dicom.toml`.
- Construct a DICOM query dataset based on command-line arguments (MRN, dates).
- Instantiate the appropriate data source class (ARIA, MIM, Mosaiq).
- Execute the query and print the found UIDs.

Note: For Mosaiq sources, this script currently initializes the Mosaiq
data source (including ODBC driver setup) but does not implement the logic
to translate command-line query parameters (like MRN, dates) into a
SQL query. It serves as a placeholder for DICOM Q/R like queries for Mosaiq.
Actual SQL query execution for Mosaiq would require different handling.

Usage:
    python -m src.cli.query --environments_config <path_to_environments.toml> \
                              --dicom_config <path_to_dicom.toml> \
                              --environment <env_name> \
                              [--mrn <mrn>] [--treatment_date <date>]

Example:
    python -m src.cli.query --environments_config src/config/environments.toml \
                              --dicom_config src/config/dicom.toml \
                              --environment UCLA_ARIA --mrn "PAT123"
"""
import argparse
import tomllib
from pydicom.dataset import Dataset
from src.data_sources.aria import ARIA
from src.data_sources.mim import MIM
from src.data_sources.mosaiq import Mosaiq
import logging

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
    with open(config_file, 'rb') as f:
        return tomllib.load(f)

def query_data_source(data_source_name: str, query_dataset: Dataset, source_ae_config: dict, env_config: dict):
    """
    Initializes the appropriate data source class and performs a query.

    :param data_source_name: The name of the data source to query (e.g., 'ARIA', 'MIM', 'Mosaiq').
    :type data_source_name: str
    :param query_dataset: The pydicom Dataset containing query parameters.
    :type query_dataset: pydicom.dataset.Dataset
    :param source_ae_config: Configuration dictionary for the source DICOM Application Entity (AE),
                             containing keys like 'IP', 'Port', 'AETitle'.
    :type source_ae_config: dict
    :param env_config: Configuration dictionary for the environment, potentially containing
                       source-specific settings like 'mosaiq_odbc_driver'.
    :type env_config: dict
    :return: The result of the query operation from the data source (typically a list or set of UIDs).
    :raises ValueError: If the specified `data_source_name` is unknown.
    """
    source = None
    logger.info(f"Initializing data source: {data_source_name}")
    if data_source_name == 'ARIA':
        source = ARIA()
    elif data_source_name == 'MIM':
        source = MIM()
    elif data_source_name == 'Mosaiq':
        odbc_driver = env_config.get('mosaiq_odbc_driver') # Get driver from env_config
        logger.debug(f"Mosaiq ODBC driver from config: {odbc_driver}")
        source = Mosaiq(odbc_driver=odbc_driver) # Pass driver to constructor
    else:
        logger.error(f"Unknown data_source: {data_source_name}")
        raise ValueError(f"Unknown data_source: {data_source_name}")
    
    # The 'qr_scp' parameter for source.query() methods is specific to DICOM Q/R sources.
    # For Mosaiq, it's db_config. This function needs to adapt.
    # Current source.query signatures:
    # ARIA/MIM.query(query_dataset, qr_scp_config)
    # Mosaiq.query(sql_query, db_config) -> This script doesn't build SQL for Mosaiq.
    # This indicates a mismatch. query.py is primarily designed for DICOM Q/R.
    # For now, I'll assume source_ae_config is the qr_scp for ARIA/MIM.
    # If data_source_name is Mosaiq, this query call will fail as it expects sql_query.
    # This script is not set up to perform SQL queries to Mosaiq.
    # This is a pre-existing issue. I will focus on the ODBC driver plumbing.
    if data_source_name == 'Mosaiq':
        logger.warning("Mosaiq source selected in query.py, but this script is designed for DICOM Q/R, not SQL queries.")
        # To make it work, one would need to construct an SQL query here based on args
        # and pass db_config (likely part of source_ae_config or env_config for Mosaiq).
        # For now, returning empty result for Mosaiq to avoid crashing.
        return set() 
        # Example of what might be needed for Mosaiq if it were to actually query:
        # db_config = source_ae_config # Assuming AE config for Mosaiq holds DB details
        # sql_query = f"SELECT SOPInstanceUID FROM SomeTable WHERE PatientID = '{query_dataset.PatientID}'" # Example
        # return source.query(sql_query, db_config)

    logger.info(f"Performing query on {data_source_name} with AET {source_ae_config.get('AETitle')}")
    return source.query(query_dataset, source_ae_config)

def main():
    """
    Main function to parse arguments, load configuration, and execute a DICOM query.
    """
    parser = argparse.ArgumentParser(description="Query information from data sources. "\
                                     "Note: For Mosaiq, this script currently only sets up the data source; "
                                     "actual SQL query execution logic based on args is not implemented.")
    parser.add_argument('--environments_config', required=True, help="Path to the environments configuration file (e.g., environments.toml).")
    # Changed '--config' to '--dicom_config' for clarity if it's for AE details
    parser.add_argument('--dicom_config', required=True, help="Path to the DICOM AE configuration file (e.g., dicom.toml).")
    parser.add_argument('--environment', required=True, help="Name of the environment to use (e.g., TJU, UCLA).")
    # --source is now determined by the environment config.
    # parser.add_argument('--source', required=True, choices=['ARIA', 'MIM', 'Mosaiq'], help="Data source to query")
    parser.add_argument('--mrn', help="Medical Record Number")
    parser.add_argument('--study_date', help="Study date in the format YYYYMMDD or YYYYMMDD-YYYYMMDD. Used if treatment_date is not provided.")
    parser.add_argument('--treatment_date', help="Treatment date (YYYYMMDD or YYYYMMDD-YYYYMMDD) to be used as StudyDate for query.")
    args = parser.parse_args()

    # Load configurations
    environments = load_config(args.environments_config)
    dicom_aes = load_config(args.dicom_config)

    # Get specific environment configuration
    if args.environment not in environments:
        logger.error(f"Environment '{args.environment}' not found in environments configuration file.")
        return
    env_config = environments[args.environment]

    # Determine the data source name from environment config
    # This simplistic query tool will use 'source' or 'source1' if present.
    data_source_name = env_config.get('source') or env_config.get('source1')
    if not data_source_name:
        logger.error(f"No 'source' or 'source1' defined for environment '{args.environment}'.")
        return
    
    logger.info(f"Selected environment: {args.environment}, Data source: {data_source_name}")

    # Get the AE configuration for the selected data source
    # This assumes data_source_name (e.g., "ARIA", "Mosaiq") is a key in dicom_aes (dicom.toml)
    if data_source_name not in dicom_aes:
        logger.error(f"DICOM AE configuration for source '{data_source_name}' not found in DICOM AE config file.")
        return
    source_ae_config = dicom_aes[data_source_name]

    query_dataset = Dataset()
    query_dataset.QueryRetrieveLevel = 'SERIES' # Default query level
    query_dataset.Modality = 'RTRECORD' # Default modality
    query_dataset.SeriesInstanceUID = '' # Request SeriesInstanceUID
    query_dataset.PatientID = args.mrn if args.mrn else ''

    if args.treatment_date:
        query_dataset.StudyDate = args.treatment_date
        logger.debug(f"Using treatment_date for StudyDate: {args.treatment_date}")
    elif args.study_date:
        query_dataset.StudyDate = args.study_date
        logger.debug(f"Using study_date for StudyDate: {args.study_date}")
    else:
        query_dataset.StudyDate = ''  # Wildcard
        logger.debug("No date provided, StudyDate will be wildcard.")

    query_dataset.StudyInstanceUID = '' # Request StudyInstanceUID

    try:
        # Pass env_config for Mosaiq's ODBC driver and source_ae_config for AE details
        uids = query_data_source(data_source_name, query_dataset, source_ae_config, env_config)
        logger.info(f"Query found {len(uids)} UIDs: {uids if uids else 'None'}")
        print(f"Found UIDs: {uids if uids else 'None'}")
    except ValueError as e:
        logger.error(f"Query failed: {e}")
        print(f"Error: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred during query: {e}", exc_info=True)
        print(f"An unexpected error occurred: {e}")

if __name__ == '__main__':
    # Basic logging setup for CLI
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    main()
