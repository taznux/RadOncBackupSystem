"""
DICOM Query CLI.

This script provides a command-line interface to perform DICOM C-FIND queries
against configured data sources (ARIA, MIM, Mosaiq). It uses environment
configurations from `environments.toml` to determine the target DICOM AE 
(Application Entity) details and other source-specific parameters.

The main function `main` handles argument parsing, configuration loading,
and orchestrates the query operation via `query_data_source`.

Key functionalities:
- Load environment settings from `environments.toml`.
- Construct a DICOM query dataset based on command-line arguments (MRN, dates).
- Instantiate the appropriate data source class (ARIA, MIM, Mosaiq).
- Execute the query and print the found UIDs.

Note: For Mosaiq sources, this script currently initializes the Mosaiq
data source (including ODBC driver setup) but does not implement the logic
to translate command-line query parameters (like MRN, dates) into a
SQL query. It serves as a placeholder for DICOM Q/R like queries for Mosaiq.
Actual SQL query execution for Mosaiq would require different handling.

Usage:
    python -m src.cli.query <environment_name> [source_alias] [--mrn <mrn>] [--study_date <date>] [--treatment_date <date>]

Example:
    python -m src.cli.query UCLA ARIA --mrn "PAT123"
    python -m src.cli.query TJU --mrn "PAT456" --treatment_date "20230115"
"""
import argparse
import tomllib
import os # Added
from pydicom.dataset import Dataset
from src.data_sources.aria import ARIA
from src.data_sources.mim import MIM
from src.data_sources.mosaiq import Mosaiq
import logging

logger = logging.getLogger(__name__)

# Define path to environments.toml, similar to backup.py
CONFIG_DIR = os.path.join(os.path.dirname(__file__), "..", "config")
ENVIRONMENTS_CONFIG_PATH = os.path.join(CONFIG_DIR, "environments.toml")

def query_data_source(source_type: str, query_dataset: Dataset, source_config: dict):
    """
    Initializes the appropriate data source class and performs a query.

    :param source_type: The type of the data source (e.g., 'aria', 'mim', 'mosaiq').
    :type source_type: str
    :param query_dataset: The pydicom Dataset containing query parameters.
    :type query_dataset: pydicom.dataset.Dataset
    :param source_config: Configuration dictionary for the source, containing AE details
                          (for DICOM sources) or DB details (for Mosaiq).
    :type source_config: dict
    :return: The result of the query operation from the data source (typically a list or set of UIDs).
    :raises ValueError: If the specified `source_type` is unknown.
    """
    source = None
    logger.info(f"Initializing data source of type: {source_type}")
    if source_type == 'aria':
        source = ARIA()
    elif source_type == 'mim':
        source = MIM()
    elif source_type == 'mosaiq':
        odbc_driver = source_config.get('odbc_driver')
        logger.debug(f"Mosaiq ODBC driver from source_config: {odbc_driver}")
        source = Mosaiq(odbc_driver=odbc_driver)
    else:
        logger.error(f"Unknown data_source_type: {source_type}")
        raise ValueError(f"Unknown data_source_type: {source_type}")
    
    if source_type == 'mosaiq':
        logger.warning("Mosaiq source selected in query.py, but this script is designed for DICOM Q/R, not SQL queries.")
        logger.warning("Actual SQL query execution based on CLI args is not implemented for Mosaiq in this script.")
        # To make it work, one would need to construct an SQL query here based on args
        # and pass db_config (which is part of source_config for Mosaiq).
        # For now, returning empty result for Mosaiq to avoid crashing.
        return set() 
        # Example of what might be needed for Mosaiq if it were to actually query:
        # db_config = {
        #     "server": source_config.get("db_server"),
        #     "database": source_config.get("db_database"),
        #     "username": source_config.get("db_username"),
        #     "password": source_config.get("db_password"),
        # }
        # sql_query = f"SELECT SOPInstanceUID FROM SomeTable WHERE PatientID = '{query_dataset.PatientID}'" # Example
        # return source.query(sql_query, db_config)

    logger.info(f"Performing query on {source_type} source: {source_config.get('aet', 'N/A')}")
    return source.query(query_dataset, source_config) # source_config now contains AET, IP, Port

def main():
    """
    Main function to parse arguments, load configuration, and execute a DICOM query.
    """
    parser = argparse.ArgumentParser(description="Query information from data sources using environments.toml. "
                                     "Note: For Mosaiq, this script currently only sets up the data source; "
                                     "actual SQL query execution logic based on args is not implemented.")
    parser.add_argument('environment_name', help="Name of the environment to use (defined in environments.toml).")
    parser.add_argument('source_alias', nargs='?', default=None, 
                        help="Alias of the source to query (defined under the environment's [sources] in environments.toml). Uses the environment's default_source if not provided.")
    parser.add_argument('--mrn', help="Medical Record Number")
    parser.add_argument('--study_date', help="Study date in the format YYYYMMDD or YYYYMMDD-YYYYMMDD. Used if treatment_date is not provided.")
    parser.add_argument('--treatment_date', help="Treatment date (YYYYMMDD or YYYYMMDD-YYYYMMDD) to be used as StudyDate for query.")
    args = parser.parse_args()

    # Load configurations from environments.toml
    try:
        with open(ENVIRONMENTS_CONFIG_PATH, 'rb') as f:
            loaded_environments = tomllib.load(f)
    except FileNotFoundError:
        logger.error(f"Environments configuration file not found at: {ENVIRONMENTS_CONFIG_PATH}")
        return
    except tomllib.TOMLDecodeError as e:
        logger.error(f"Error decoding environments.toml: {e}")
        return

    env_block = loaded_environments.get(args.environment_name)
    if not env_block:
        logger.error(f"Environment '{args.environment_name}' not found in {ENVIRONMENTS_CONFIG_PATH}.")
        return

    actual_source_alias = args.source_alias or env_block.get('default_source')
    if not actual_source_alias:
        logger.error(f"No source alias provided and no 'default_source' defined for environment '{args.environment_name}'.")
        return
    
    all_sources_config = env_block.get('sources', {})
    current_source_config = all_sources_config.get(actual_source_alias)
    if not current_source_config:
        logger.error(f"Configuration for source alias '{actual_source_alias}' not found in environment '{args.environment_name}'.")
        return

    source_type = current_source_config.get('type')
    if not source_type:
        logger.error(f"Missing 'type' for source alias '{actual_source_alias}' in environment '{args.environment_name}'.")
        return
    
    logger.info(f"Selected environment: {args.environment_name}, Source Alias: {actual_source_alias}, Source Type: {source_type}")

    query_dataset = Dataset()
    # Query level and modality can also be part of source_config or env_block['settings']
    # For now, using defaults similar to before, but these could be made more configurable.
    query_dataset.QueryRetrieveLevel = current_source_config.get('dicom_query_level', 
                                                               env_block.get('settings', {}).get('dicom_query_level', 'SERIES'))
    query_dataset.Modality = current_source_config.get('modality_to_query', 
                                                       env_block.get('settings', {}).get('modality_to_query', 'RTRECORD'))
    
    query_dataset.SeriesInstanceUID = '' # Request SeriesInstanceUID by default
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

    query_dataset.StudyInstanceUID = '' # Request StudyInstanceUID by default

    try:
        uids = query_data_source(source_type, query_dataset, current_source_config)
        logger.info(f"Query found {len(uids)} UIDs: {uids if uids else 'None'}")
        print(f"Found UIDs: {uids if uids else 'None'}")
    except ValueError as e:
        logger.error(f"Query failed: {e}")
        print(f"Error: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred during query: {e}", exc_info=True)
        print(f"An unexpected error occurred: {e}")

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    main()
