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
Configurations are loaded via `config_loader.py`, expecting `environments.toml`,
`logging.toml`, and `dicom.toml`. Secrets are managed via `.env` file.

Usage:
    python -m src.cli.query <environment_name> [source_alias] [--mrn <mrn>] [--study_date <date>] [--treatment_date <date>]

Example:
    python -m src.cli.query UCLA ARIA --mrn "PAT123"
    python -m src.cli.query TJU --mrn "PAT456" --treatment_date "20230115"
"""
import click
from typing import Optional
# tomllib is used by config_loader
import os
import sys # For sys.exit
from pydicom.dataset import Dataset
from src.data_sources.aria import ARIA
from src.data_sources.mim import MIM
from src.data_sources.mosaiq import Mosaiq
from src.config.config_loader import load_config, ConfigLoaderError # Import the new loader
import logging

logger = logging.getLogger(__name__)

# Define paths to configuration files relative to this script's location (src/cli)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ENVIRONMENTS_CONFIG_PATH = os.path.join(PROJECT_ROOT, "src", "config", "environments.toml")
LOGGING_CONFIG_PATH = os.path.join(PROJECT_ROOT, "src", "config", "logging.toml")
DICOM_CONFIG_PATH = os.path.join(PROJECT_ROOT, "src", "config", "dicom.toml") # Needed by load_config

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

@click.command("query", help="Query information from data sources (ARIA, MIM, Mosaiq).")
@click.argument('environment_name', type=str)
@click.argument('source_alias', type=str, required=False, default=None)
@click.option('--mrn', help="Medical Record Number")
@click.option('--study-date', help="Study date (YYYYMMDD or YYYYMMDD-YYYYMMDD).")
@click.option('--treatment-date', help="Treatment date (YYYYMMDD or YYYYMMDD-YYYYMMDD), used as StudyDate.")
@click.pass_context
def query_cmd(ctx, environment_name: str, source_alias: Optional[str], mrn: Optional[str],
              study_date: Optional[str], treatment_date: Optional[str]):
    """
    Command to load configuration and execute a DICOM query.
    """
    if ctx.obj and ctx.obj.get('VERBOSE'):
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    try:
        app_config = load_config(
            config_path_environments=ENVIRONMENTS_CONFIG_PATH,
            config_path_logging=LOGGING_CONFIG_PATH,
            config_path_dicom=DICOM_CONFIG_PATH
        )
    except ConfigLoaderError as e:
        logger.critical(f"Failed to load application configuration for query CLI: {e}", exc_info=True)
        click.echo(f"FATAL: Failed to load application configuration: {e}", err=True)
        sys.exit(1)

    env_block = app_config.get('environments', {}).get(environment_name)
    if not env_block:
        logger.error(f"Environment '{environment_name}' not found in resolved environments configuration.")
        click.echo(f"Error: Environment '{environment_name}' not found.", err=True)
        sys.exit(1)

    actual_source_alias = source_alias or env_block.get('default_source')
    if not actual_source_alias:
        logger.error(f"No source alias provided and no 'default_source' defined for environment '{environment_name}'.")
        click.echo(f"Error: No source alias provided and no default_source defined for '{environment_name}'.", err=True)
        sys.exit(1)

    all_sources_config = env_block.get('sources', {})
    current_source_config = all_sources_config.get(actual_source_alias)
    if not current_source_config:
        logger.error(f"Configuration for source alias '{actual_source_alias}' not found in environment '{environment_name}'.")
        click.echo(f"Error: Config for source '{actual_source_alias}' not found in '{environment_name}'.", err=True)
        sys.exit(1)

    source_type = current_source_config.get('type')
    if not source_type:
        logger.error(f"Missing 'type' for source alias '{actual_source_alias}' in environment '{environment_name}'.")
        click.echo(f"Error: Missing 'type' for source '{actual_source_alias}' in '{environment_name}'.", err=True)
        sys.exit(1)

    logger.info(f"Selected environment: {environment_name}, Source Alias: {actual_source_alias}, Source Type: {source_type}")

    query_dataset = Dataset()
    query_dataset.QueryRetrieveLevel = current_source_config.get('dicom_query_level', 
                                                               env_block.get('settings', {}).get('dicom_query_level', 'SERIES'))
    query_dataset.Modality = current_source_config.get('modality_to_query', env_block.get('settings', {}).get('modality_to_query', 'RTRECORD'))

    query_dataset.SeriesInstanceUID = ''
    query_dataset.PatientID = mrn if mrn else ''

    if treatment_date:
        query_dataset.StudyDate = treatment_date
        logger.debug(f"Using treatment_date for StudyDate: {treatment_date}")
    elif study_date:
        query_dataset.StudyDate = study_date
        logger.debug(f"Using study_date for StudyDate: {study_date}")
    else:
        query_dataset.StudyDate = ''
        logger.debug("No date provided, StudyDate will be wildcard.")

    query_dataset.StudyInstanceUID = ''

    try:
        uids = query_data_source(source_type, query_dataset, current_source_config) # Assumes query_data_source is defined
        logger.info(f"Query found {len(uids)} UIDs: {uids if uids else 'None'}")
        click.echo(f"Found UIDs: {uids if uids else 'None'}")
    except ValueError as e:
        logger.error(f"Query failed: {e}")
        click.echo(f"Error: {e}", err=True)
    except Exception as e:
        logger.error(f"An unexpected error occurred during query: {e}", exc_info=True)
        click.echo(f"An unexpected error occurred: {e}", err=True)

# The main robs CLI in main.py will add this command.