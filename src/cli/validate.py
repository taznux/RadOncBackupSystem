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
- Requires `environments.toml` for environment-specific settings (source, backup target name).
- Requires `dicom.toml` for DICOM AE details (IP, Port, AETitle) for both sources
  and the Orthanc backup (if its DICOM interface were used for verification, though
  current `Orthanc.verify` uses REST API).

Usage:
    python -m src.cli.validate <environments_config_path> <dicom_config_path> <environment_name>

Example:
    python -m src.cli.validate src/config/environments.toml src/config/dicom.toml TJU_Mosaiq
"""
import argparse
import logging # Changed from print to logging
import tomllib
from pydicom.dataset import Dataset
from pynetdicom import AE, evt, StoragePresentationContexts, ALL_TRANSFER_SYNTAXES
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind, StudyRootQueryRetrieveInformationModelMove, VerificationSOPClass
from src.backup_systems.orthanc import Orthanc
import io
import pydicom # For dcmwrite, uid
import os
# import tempfile # Unused currently
# import shutil # Unused currently

logger = logging.getLogger(__name__)

# Global list to store received datasets' bytes during C-MOVE
GLOBAL_RECEIVED_DATASETS = []

# Configuration paths (can be made configurable via args if needed)
CONFIG_DIR = os.path.join(os.path.dirname(__file__), '..', 'config')
DEFAULT_ENV_CONFIG_PATH = os.path.join(CONFIG_DIR, 'environments.toml')
DEFAULT_DICOM_CONFIG_PATH = os.path.join(CONFIG_DIR, 'dicom.toml')


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
    # Ensure file_meta is present, as it might not be if received over network
    if not hasattr(ds, 'file_meta'):
        logger.debug("Dataset received via C-STORE has no file_meta, creating one.")
        ds.file_meta = pydicom.Dataset()
        # Basic file_meta population. TransferSyntaxUID is crucial.
        # If the sender doesn't negotiate well, this might be needed.
        # However, pynetdicom usually handles this based on negotiated context.
        ds.file_meta.TransferSyntaxUID = event.context.transfer_syntax[0] # Use negotiated transfer syntax
        ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
        ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    
    logger.info(f"Received SOPInstanceUID: {ds.SOPInstanceUID} via C-STORE for C-MOVE operation.")
    try:
        with io.BytesIO() as bio:
            # write_like_original=False ensures that file_meta reflects the negotiated Transfer Syntax.
            pydicom.dcmwrite(bio, ds, write_like_original=False) 
            GLOBAL_RECEIVED_DATASETS.append(bio.getvalue())
        logger.debug(f"Successfully processed and stored bytes for SOPInstanceUID: {ds.SOPInstanceUID}")
    except Exception as e:
        logger.error(f"Error processing dataset {ds.SOPInstanceUID} in _handle_move_store: {e}", exc_info=True)
        return 0xA700 # Failure status: Out of resources / Unable to process
    
    return 0x0000 # Success

def validate_data(env_config_path: str, dicom_config_path: str, environment_name: str):
    """
    Validates data consistency between a source DICOM AE and an Orthanc backup.

    Performs C-FIND on the source, then C-MOVEs a sample of data to a local SCP,
    and for each received instance, verifies it against Orthanc using `Orthanc.verify()`.

    :param env_config_path: Path to the environments TOML configuration file.
    :type env_config_path: str
    :param dicom_config_path: Path to the DICOM AEs TOML configuration file.
    :type dicom_config_path: str
    :param environment_name: The name of the environment to validate (e.g., 'UCLA_ARIA').
                             Must be a key in the environments configuration.
    :type environment_name: str
    :raises ValueError: If configuration is missing or invalid.
    :raises FileNotFoundError: If configuration files are not found.
    :raises tomllib.TOMLDecodeError: If configuration files are not valid TOML.
    """
    global GLOBAL_RECEIVED_DATASETS
    logger.info(f"Starting validation for environment: {environment_name}")

    try:
        with open(env_config_path, 'rb') as f:
            environments_cfg = tomllib.load(f)
        with open(dicom_config_path, 'rb') as f:
            dicom_cfg = tomllib.load(f)
    except FileNotFoundError as e:
        logger.error(f"Configuration file not found: {e.filename}", exc_info=True)
        raise
    except tomllib.TOMLDecodeError as e:
        logger.error(f"Error decoding TOML configuration: {e}", exc_info=True)
        raise

    if environment_name not in environments_cfg:
        logger.error(f"Environment '{environment_name}' not found in {env_config_path}")
        raise ValueError(f"Environment '{environment_name}' not found.")
    env_config = environments_cfg[environment_name]
    
    source_name = env_config.get('source') or env_config.get('source1') # Handle TJU legacy
    if not source_name:
        logger.error(f"No 'source' or 'source1' defined for environment '{environment_name}'.")
        raise ValueError(f"No data source defined for environment '{environment_name}'.")

    if source_name not in dicom_cfg:
        logger.error(f"Configuration for source '{source_name}' not found in {dicom_config_path}.")
        raise ValueError(f"Configuration for source '{source_name}' not found.")
    source_ae_config = dicom_cfg[source_name]

    # Backup system (Orthanc)
    # Orthanc URL can be passed to constructor if needed from config, e.g. dicom_cfg[env_config['backup']]['URL']
    # For now, Orthanc class uses its internal ORTHANC_URL or one passed to init.
    orthanc_backup_config_name = env_config.get('backup')
    orthanc_url_from_config = None
    if orthanc_backup_config_name and orthanc_backup_config_name in dicom_cfg:
        orthanc_url_from_config = dicom_cfg[orthanc_backup_config_name].get('URL') # Assuming URL key if specified
    
    orthanc_verifier = Orthanc(orthanc_url=orthanc_url_from_config) # Pass URL if configured, else uses default

    local_ae_port = 11113 # Example port, ensure it's free and firewall allows
    local_ae_title = "VALIDATE_SCP" # AE Title for our local SCP

    ae = AE(ae_title=local_ae_title)
    for context in StoragePresentationContexts: # SCP supports all standard storage
        ae.add_supported_context(context.abstract_syntax, ALL_TRANSFER_SYNTAXES)
    
    ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind) # SCU role for C-FIND
    ae.add_requested_context(StudyRootQueryRetrieveInformationModelMove) # SCU role for C-MOVE
    ae.add_requested_context(VerificationSOPClass) # SCU role for C-ECHO

    scp_handlers = [(evt.EVT_C_STORE, _handle_move_store)]
    # Listen on 0.0.0.0 to accept connections from remote source PACS for C-MOVE
    scp_server = ae.start_server(("0.0.0.0", local_ae_port), block=False, evt_handlers=scp_handlers)
    logger.info(f"Local C-STORE SCP server started on port {local_ae_port} with AE Title {local_ae_title} for validation.")

    # C-ECHO to source
    logger.info(f"Attempting C-ECHO to source {source_ae_config['AETitle']} at {source_ae_config['IP']}:{source_ae_config['Port']}")
    assoc_echo = ae.associate(source_ae_config['IP'], source_ae_config['Port'], ae_title=source_ae_config['AETitle'])
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
        logger.error(f"C-ECHO association to source {source_ae_config['AETitle']} failed. Aborting.")
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
    logger.info(f"Attempting C-FIND to {source_ae_config['AETitle']} for series list.")
    assoc_find = ae.associate(source_ae_config['IP'], source_ae_config['Port'], ae_title=source_ae_config['AETitle'])
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
        logger.error(f"C-FIND association to {source_ae_config['AETitle']} failed.")
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
        assoc_move = ae.associate(source_ae_config['IP'], source_ae_config['Port'], ae_title=source_ae_config['AETitle'])
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
            logger.error(f"C-MOVE association to {source_ae_config['AETitle']} failed for series {current_series_uid}.")
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
        help='Name of the environment to validate (must be defined in the environments config file).'
    )
    parser.add_argument(
        '--env_config', 
        type=str, 
        default=DEFAULT_ENV_CONFIG_PATH,
        help='Path to the environments configuration file (e.g., environments.toml).'
    )
    parser.add_argument(
        '--dicom_config', 
        type=str, 
        default=DEFAULT_DICOM_CONFIG_PATH,
        help='Path to the DICOM AE configuration file (e.g., dicom.toml).'
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
        validate_data(args.env_config, args.dicom_config, args.environment_name)
    except ValueError as ve:
        logger.critical(f"Validation process initialization failed: {ve}", exc_info=True)
    except FileNotFoundError as fnfe:
        logger.critical(f"A required configuration file was not found: {fnfe.filename}", exc_info=True)
    except tomllib.TOMLDecodeError as tde:
        logger.critical(f"Error decoding a TOML configuration file: {tde}", exc_info=True)
    except Exception as ex:
        logger.critical(f"An unexpected critical error occurred during the validation process: {ex}", exc_info=True)
