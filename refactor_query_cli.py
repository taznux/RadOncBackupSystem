import re
import sys
from pydicom.dataset import Dataset # Ensure Dataset is imported
from src.config.config_loader import load_config, ConfigLoaderError # Ensure load_config is imported
import logging # Ensure logging is imported
from typing import Optional # Ensure Optional is imported

# Paths needed by the script (assuming they are defined in the original query.py context)
# These might need to be adjusted if not globally available in the original file
import os
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')) if '__file__' in globals() else os.getcwd()
ENVIRONMENTS_CONFIG_PATH = os.path.join(PROJECT_ROOT, 'src', 'config', 'environments.toml')
LOGGING_CONFIG_PATH = os.path.join(PROJECT_ROOT, 'src', 'config', 'logging.toml')
DICOM_CONFIG_PATH = os.path.join(PROJECT_ROOT, 'src', 'config', 'dicom.toml')

logger = logging.getLogger(__name__)

def refactor_query_file(filepath):
    with open(filepath, 'r') as f:
        original_content = f.read()

    # Extract query_data_source and other necessary parts if they are not top-level
    # For this script, we assume query_data_source is at top level or imported and available.
    # We will basically take everything that is NOT the old main and if __name__ block

    lines = original_content.split('\n')
    kept_lines = []
    skipping_main_block = False
    skipping_if_main_block = False

    for line in lines:
        if line.strip().startswith('def main():'):
            skipping_main_block = True
            continue
        if skipping_main_block and not (line.startswith(' ') or line.strip() == ''):
            # Exiting main block if line is not indented and not empty
            skipping_main_block = False

        if line.strip().startswith("if __name__ == '__main__':"):
            skipping_if_main_block = True
            continue
        if skipping_if_main_block and not (line.startswith(' ') or line.strip() == ''):
            skipping_if_main_block = False

        if skipping_main_block or skipping_if_main_block:
            continue

        # Specific argparse lines to remove if they are outside main (unlikely for this project)
        if 'argparse.ArgumentParser' in line or 'parser.add_argument' in line or 'args = parser.parse_args()' in line:
            continue

        if line.strip() == 'import argparse':
            kept_lines.append('import click')
            # Ensure Optional is imported
            if not any('from typing import Optional' in k_line for k_line in kept_lines) and \
               not any('from typing import Optional' in o_line for o_line in original_content.split('\n')):
                # Try to add it after 'import sys' or 'import click' intelligently
                idx_sys = -1
                idx_click = -1
                try: idx_sys = kept_lines.index('import sys')
                except ValueError: pass
                try: idx_click = kept_lines.index('import click')
                except ValueError: pass
                if idx_sys != -1: kept_lines.insert(idx_sys + 1, 'from typing import Optional')
                elif idx_click != -1: kept_lines.insert(idx_click + 1, 'from typing import Optional')
                else: kept_lines.insert(0, 'from typing import Optional') # Fallback to top
            continue
        kept_lines.append(line)

    content = '\n'.join(kept_lines)
    # Clean up excessive blank lines that might result from removals
    content = re.sub(r'\n\s*\n+', '\n\n', content).strip()

    click_command_code = """

@click.command("query", help="Query information from data sources (ARIA, MIM, Mosaiq).")
@click.argument('environment_name', type=str)
@click.argument('source_alias', type=str, required=False, default=None)
@click.option('--mrn', help="Medical Record Number")
@click.option('--study-date', help="Study date (YYYYMMDD or YYYYMMDD-YYYYMMDD).")
@click.option('--treatment-date', help="Treatment date (YYYYMMDD or YYYYMMDD-YYYYMMDD), used as StudyDate.")
@click.pass_context
def query_cmd(ctx, environment_name: str, source_alias: Optional[str], mrn: Optional[str],
              study_date: Optional[str], treatment_date: Optional[str]):
    \"\"\"
    Command to load configuration and execute a DICOM query.
    \"\"\"
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
"""
    content += '\n\n' + click_command_code.strip()

    with open(filepath, 'w') as f:
        f.write(content)

if __name__ == '__main__':
    if len(sys.argv) > 1:
        filepath_to_refactor = sys.argv[1]
        print(f"Refactoring {filepath_to_refactor}...")
        refactor_query_file(filepath_to_refactor)
        print(f"Finished refactoring {filepath_to_refactor}.")
        print(f"The refactored code has been written back to {filepath_to_refactor}.")
    else:
        print("Please provide the filepath of the Python file to refactor as a command-line argument.")
        print("Example: python refactor_query_cli.py src/cli/query.py")
