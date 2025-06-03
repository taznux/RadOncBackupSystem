"""
DICOM Validation CLI.

This script provides a command-line interface to validate DICOM data consistency
between a source system and a backup system (Orthanc). It performs the following steps:

1.  Loads configurations for environments and DICOM Application Entities (AEs).
2.  Establishes a local C-STORE SCP to receive DICOM instances via C-MOVE.
3.  Performs a C-ECHO to the source AE to verify basic connectivity.
4.  Queries the source AE using C-FIND (at SERIES level) to get a list of series.
5.  For a limited number of found series (configurable by `MAX_SERIES_TO_VALIDATE`):
    a.  Initiates a C-MOVE request to the source AE, instructing it to send
        instances of the series to the local C-STORE SCP.
    b.  The `_handle_move_store` callback processes each received instance,
        converting it to bytes and storing it in `GLOBAL_RECEIVED_DATASETS`.
    c.  After C-MOVE completes for a series, each received instance (as bytes)
        is verified against the Orthanc backup using `Orthanc.verify()`.
        This involves checking for existence and byte-for-byte content matching.
6.  Prints a summary of validation successes and failures.

Configuration:
- Uses `config_loader.py` to load `environments.toml`, `logging.toml`, and `dicom.toml`.
- `environments.toml` provides environment-specific settings.
- `dicom.toml` (if used by Orthanc class or other components) for AE details.
- Secrets (like DB passwords) are loaded from `.env` if present.

Usage:
    python -m src.cli.validate <environment_name> [source_alias] [backup_alias] [--log_level <LEVEL>]

Example:
    python -m src.cli.validate UCLA ARIA ORTHANC_MAIN_UCLA --log_level DEBUG
"""
import argparse
import logging 
# tomllib is used by config_loader
from pydicom.dataset import Dataset
from pynetdicom import AE, evt, StoragePresentationContexts, ALL_TRANSFER_SYNTAXES
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind, StudyRootQueryRetrieveInformationModelMove, VerificationSOPClass
from src.backup_systems.orthanc import Orthanc
from src.config.config_loader import load_config, ConfigLoaderError # Import the new loader
import io
import pydicom 
import os
import sys # For sys.exit
from typing import Optional

logger = logging.getLogger(__name__)

# Global list to store received datasets' bytes during C-MOVE
GLOBAL_RECEIVED_DATASETS = []

# Define paths to configuration files relative to this script's location (src/cli)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ENVIRONMENTS_CONFIG_PATH = os.path.join(PROJECT_ROOT, "src", "config", "environments.toml")
LOGGING_CONFIG_PATH = os.path.join(PROJECT_ROOT, "src", "config", "logging.toml")
DICOM_CONFIG_PATH = os.path.join(PROJECT_ROOT, "src", "config", "dicom.toml")


def _handle_move_store(event: evt.Event) -> int:
    """
    Handles DICOM C-STORE events triggered by a C-MOVE operation.

    This callback function is invoked by the pynetdicom AE when a DICOM instance
    is received. It converts the received pydicom Dataset into bytes and appends
    it to the `GLOBAL_RECEIVED_DATASETS` list for later verification.

    :param event: The pynetdicom event object, containing the dataset and association details.
    :type event: pynetdicom.events.Event
    :return: DICOM status code 0x0000 (Success).
    :rtype: int
    """
    ds = event.dataset
    if not hasattr(ds, 'file_meta'):
        logger.debug("Dataset received via C-STORE has no file_meta, creating one.")
        ds.file_meta = pydicom.Dataset()
        ds.file_meta.TransferSyntaxUID = event.context.transfer_syntax[0] 
        ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
        ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    
    logger.info(f"Received SOPInstanceUID: {ds.SOPInstanceUID} via C-STORE for C-MOVE operation.")
    try:
        with io.BytesIO() as bio:
            pydicom.dcmwrite(bio, ds, write_like_original=False) 
            GLOBAL_RECEIVED_DATASETS.append(bio.getvalue())
        logger.debug(f"Successfully processed and stored bytes for SOPInstanceUID: {ds.SOPInstanceUID}")
    except Exception as e:
        logger.error(f"Error processing dataset {ds.SOPInstanceUID} in _handle_move_store: {e}", exc_info=True)
        return 0xA700 
    
    return 0x0000 

def validate_data(environment_name: str, source_alias_arg: Optional[str], backup_alias_arg: Optional[str]):
    """
    Validates data consistency between a source DICOM AE and an Orthanc backup.

    Performs C-FIND on the source, then C-MOVEs a sample of data to a local SCP,
    and for each received instance, verifies it against Orthanc using `Orthanc.verify()`.

    :param environment_name: The name of the environment to validate.
    :type environment_name: str
    :param source_alias_arg: Optional alias of the source to validate. Uses environment default if None.
    :type source_alias_arg: Optional[str]
    :param backup_alias_arg: Optional alias of the backup target to validate against. Uses environment default if None.
    :type backup_alias_arg: Optional[str]
    :raises ValueError: If configuration is missing or invalid.
    :raises FileNotFoundError: If environments.toml is not found.
    :raises tomllib.TOMLDecodeError: If environments.toml is not valid TOML.
    """
    global GLOBAL_RECEIVED_DATASETS
    logger.info(f"Starting validation for environment: {environment_name}, Source: {source_alias_arg or 'default'}, Backup: {backup_alias_arg or 'default'}")

    try:
        # Load all configurations using the new central loader
        app_config = load_config(
            config_path_environments=ENVIRONMENTS_CONFIG_PATH,
            config_path_logging=LOGGING_CONFIG_PATH,
            config_path_dicom=DICOM_CONFIG_PATH
        )
    except ConfigLoaderError as e:
        logger.critical(f"Failed to load application configuration for validation: {e}", exc_info=True)
        # For CLI usage, it's better to exit if core config is missing.
        # The print is for user feedback if logging isn't visible yet or is redirected.
        print(f"FATAL: Failed to load application configuration: {e}", file=sys.stderr)
        sys.exit(1) # Exit if config fails

    env_block = app_config.get('environments', {}).get(environment_name)
    if not env_block:
        logger.error(f"Environment '{environment_name}' not found in resolved environments configuration.")
        raise ValueError(f"Environment '{environment_name}' not found.")
    
    # dicom_config = app_config.get('dicom', {}) # If needed for AE details not in environments

    script_ae_config = env_block.get('script_ae')
    if not script_ae_config or not script_ae_config.get('aet'):
        raise ValueError(f"Missing 'script_ae' configuration or 'aet' in environment '{environment_name}'.")
    local_ae_title = script_ae_config['aet']
    local_ae_port = int(script_ae_config.get('port', 11113))

    actual_source_alias = source_alias_arg or env_block.get('default_source')
    if not actual_source_alias:
        raise ValueError(f"No source alias provided and no 'default_source' defined for environment '{environment_name}'.")
    source_ae_config = env_block.get('sources', {}).get(actual_source_alias)
    if not source_ae_config or not all(k in source_ae_config for k in ['aet', 'ip', 'port']):
        raise ValueError(f"Configuration for source alias '{actual_source_alias}' is missing or incomplete (aet, ip, port) in environment '{environment_name}'.")

    actual_backup_alias = backup_alias_arg or env_block.get('default_backup')
    if not actual_backup_alias:
        raise ValueError(f"No backup alias provided and no 'default_backup' defined for environment '{environment_name}'.")
    backup_target_config = env_block.get('backup_targets', {}).get(actual_backup_alias)
    if not backup_target_config or not all(k in backup_target_config for k in ['aet', 'ip', 'port', 'type']):
        raise ValueError(f"Configuration for backup target alias '{actual_backup_alias}' is missing or incomplete (aet, ip, port, type) in environment '{environment_name}'.")
    if backup_target_config.get('type') != 'orthanc':
        raise ValueError(f"Backup target '{actual_backup_alias}' must be of type 'orthanc' for validation.")

    orthanc_verifier = Orthanc(
        calling_aet=local_ae_title,
        peer_aet=backup_target_config['aet'],
        peer_host=backup_target_config['ip'],
        peer_port=int(backup_target_config['port'])
    )
    
    ae = AE(ae_title=local_ae_title)
    for context in StoragePresentationContexts: 
        ae.add_supported_context(context.abstract_syntax, ALL_TRANSFER_SYNTAXES)
    
    ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind) 
    ae.add_requested_context(StudyRootQueryRetrieveInformationModelMove) 
    ae.add_requested_context(VerificationSOPClass) 

    scp_handlers = [(evt.EVT_C_STORE, _handle_move_store)]
    scp_server = ae.start_server(("0.0.0.0", local_ae_port), block=False, evt_handlers=scp_handlers)
    logger.info(f"Local C-STORE SCP server started on port {local_ae_port} with AE Title {local_ae_title} for validation.")

    source_ip = source_ae_config['ip']
    source_port = int(source_ae_config['port'])
    source_aet = source_ae_config['aet']

    logger.info(f"Attempting C-ECHO to source {source_aet} at {source_ip}:{source_port}")
    assoc_echo = ae.associate(source_ip, source_port, ae_title=source_aet)
    if assoc_echo.is_established:
        logger.info(f"C-ECHO association established. Sending C-ECHO request...")
        status_echo = assoc_echo.send_c_echo()
        if not (status_echo and status_echo.Status == 0x0000):
            logger.error(f"C-ECHO to source failed. Status: {status_echo.Status if status_echo else 'Unknown'}. Aborting validation.")
            assoc_echo.release()
            scp_server.shutdown()
            return
        logger.info("C-ECHO successful.")
        assoc_echo.release()
    else:
        logger.error(f"C-ECHO association to source {source_aet} failed. Aborting.")
        scp_server.shutdown()
        return

    # C-FIND for series
    query_ds = Dataset()
    query_ds.QueryRetrieveLevel = 'SERIES'
    query_ds.PatientID = '*' # Broad query for demonstration
    query_ds.SeriesInstanceUID = "" 
    # Add more specific query keys as needed for real validation, e.g., StudyDate
    # query_ds.StudyDate = "YYYYMMDD-YYYYMMDD" 

    datasets_to_verify_details = []
    logger.info(f"Attempting C-FIND to {source_aet} for series list.")
    assoc_find = ae.associate(source_ip, source_port, ae_title=source_aet)
    if assoc_find.is_established:
        logger.info("C-FIND association established. Sending C-FIND request.")
        responses = assoc_find.send_c_find(query_ds, StudyRootQueryRetrieveInformationModelFind)
        for status_dataset, identifier_dataset in responses:
            if status_dataset is None:
                logger.error("C-FIND Error: Connection lost or aborted.")
                break
            if status_dataset.Status in (0xFF00, 0xFF01): # Pending
                if identifier_dataset:
                    study_uid = identifier_dataset.get('StudyInstanceUID')
                    series_uid = identifier_dataset.get('SeriesInstanceUID')
                    if study_uid and series_uid:
                        logger.debug(f"C-FIND Pending: Found SeriesUID {series_uid} in StudyUID {study_uid}")
                        datasets_to_verify_details.append({'StudyInstanceUID': study_uid, 'SeriesInstanceUID': series_uid})
                else:
                    logger.debug("C-FIND Pending status with no identifier dataset.")
            elif status_dataset.Status == 0x0000: # Success
                logger.info("C-FIND operation completed successfully.")
                break
            else: # Failure
                logger.error(f"C-FIND operation failed. Status: 0x{status_dataset.Status:04X}")
                break
        assoc_find.release()
    else:
        logger.error(f"C-FIND association to {source_aet} failed.")
        scp_server.shutdown()
        return
    
    if not datasets_to_verify_details:
        logger.info("No series found from C-FIND. Validation cannot proceed with C-MOVE.")
        scp_server.shutdown()
        return

    logger.info(f"Found {len(datasets_to_verify_details)} series from source. Will attempt to validate up to MAX_SERIES_TO_VALIDATE.")
    MAX_SERIES_TO_VALIDATE = 2 
    
    validation_success_count = 0
    validation_failure_count = 0

    for i, series_details in enumerate(datasets_to_verify_details[:MAX_SERIES_TO_VALIDATE]):
        current_series_uid = series_details['SeriesInstanceUID']
        logger.info(f"Processing Series {i+1}/{len(datasets_to_verify_details[:MAX_SERIES_TO_VALIDATE])}: {current_series_uid}")
        GLOBAL_RECEIVED_DATASETS = [] # Reset for current series

        move_dataset = Dataset()
        move_dataset.QueryRetrieveLevel = 'SERIES'
        move_dataset.StudyInstanceUID = series_details['StudyInstanceUID']
        move_dataset.SeriesInstanceUID = current_series_uid
        
        logger.info(f"Attempting C-MOVE for Series {current_series_uid} to local SCP {local_ae_title}")
        assoc_move = ae.associate(source_ip, source_port, ae_title=source_aet)
        if assoc_move.is_established:
            logger.info("C-MOVE association established. Sending C-MOVE request.")
            responses = assoc_move.send_c_move(move_dataset, local_ae_title, StudyRootQueryRetrieveInformationModelMove)
            for status_dataset_move, _ in responses:
                if status_dataset_move is None:
                    logger.error(f"C-MOVE for series {current_series_uid} failed: Connection lost or aborted.")
                    break
                if status_dataset_move.Status in (0xFF00, 0xFF01): # Pending
                    logger.debug(f"C-MOVE for {current_series_uid} pending: Remaining={status_dataset_move.NumberOfRemainingSuboperations}, "
                                 f"Completed={status_dataset_move.NumberOfCompletedSuboperations}")
                elif status_dataset_move.Status == 0x0000: # Success
                    logger.info(f"C-MOVE for series {current_series_uid} completed successfully by source. "
                                f"Final counts: Completed={status_dataset_move.NumberOfCompletedSuboperations}, "
                                f"Warnings={status_dataset_move.NumberOfWarningSuboperations}, "
                                f"Failures={status_dataset_move.NumberOfFailedSuboperations}.")
                    break
                else: # Failure
                    logger.error(f"C-MOVE for series {current_series_uid} failed at source. Status: 0x{status_dataset_move.Status:04X}")
                    if hasattr(status_dataset_move, 'ErrorComment') and status_dataset_move.ErrorComment:
                         logger.error(f"C-MOVE Error Comment: {status_dataset_move.ErrorComment}")
                    break
            assoc_move.release()
        else:
            logger.error(f"C-MOVE association to {source_aet} failed for series {current_series_uid}.")
            continue 

        if not GLOBAL_RECEIVED_DATASETS:
            logger.warning(f"No instances were received via C-MOVE for series {current_series_uid}. "
                           "This could be an empty series or an issue with C-MOVE/SCP process.")
        
        for instance_bytes in GLOBAL_RECEIVED_DATASETS:
            logger.debug(f"Verifying instance (size: {len(instance_bytes)} bytes) from series {current_series_uid} in Orthanc...")
            try:
                if orthanc_verifier.verify(instance_bytes):
                    logger.info(f"Instance from series {current_series_uid} VERIFIED successfully in Orthanc.")
                    validation_success_count += 1
                else:
                    logger.warning(f"Instance from series {current_series_uid} verification FAILED in Orthanc.")
                    validation_failure_count += 1
            except Exception as e:
                logger.error(f"Error during Orthanc.verify() for an instance from series {current_series_uid}: {e}", exc_info=True)
                validation_failure_count += 1
        
        GLOBAL_RECEIVED_DATASETS = [] # Clear after processing series

    scp_server.shutdown()
    logger.info("Local C-STORE SCP server shut down.")
    summary_message = f"Validation Summary: Successes: {validation_success_count}, Failures: {validation_failure_count}"
    logger.info(summary_message)
    print(summary_message) # Also print to console for easy visibility

if __name__ == '__main__':
    # Setup basic logging for the CLI
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    parser = argparse.ArgumentParser(
        description='Validate DICOM data consistency between a source and Orthanc backup.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        'environment_name',
        type=str,
        help='Name of the environment to use (defined in environments.toml).'
    )
    parser.add_argument(
        'source_alias',
        type=str,
        nargs='?',
        default=None,
        help="Alias of the source to validate (from environment's [sources]). Uses default_source if not provided."
    )
    parser.add_argument(
        'backup_alias',
        type=str,
        nargs='?',
        default=None,
        help="Alias of the backup target to validate against (from environment's [backup_targets]). Uses default_backup if not provided."
    )
    parser.add_argument(
        '--log_level',
        type=str,
        default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        help='Set the logging level for the script.'
    )
    args = parser.parse_args()

    # Update logging level based on argument
    logging.getLogger().setLevel(args.log_level) # Set root logger level
    logger.info(f"Logging level set to {args.log_level}")

    try:
        validate_data(args.environment_name, args.source_alias, args.backup_alias)
    except ValueError as ve:
        logger.critical(f"Validation process initialization failed: {ve}", exc_info=True)
    except FileNotFoundError as fnfe:
        logger.critical(f"A required configuration file was not found: {fnfe.filename}", exc_info=True)
    except tomllib.TOMLDecodeError as tde:
        logger.critical(f"Error decoding a TOML configuration file: {tde}", exc_info=True)
    except Exception as ex:
        logger.critical(f"An unexpected critical error occurred during the validation process: {ex}", exc_info=True)
