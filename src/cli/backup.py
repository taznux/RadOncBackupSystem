"""
DICOM Backup CLI.

This script provides a command-line interface to initiate the backup process
for DICOM data from configured source systems to a backup destination (e.g., Orthanc).
It uses environment configurations defined in TOML files to determine source
details, backup parameters, and data source types (ARIA, MIM, Mosaiq).
"""
import argparse
# import functools # For functools.partial - No longer needed
# import io - No longer needed for handle_store
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
from typing import Optional, Dict, Any, Tuple, List 
from argparse import Namespace # Added

# from pydicom import dcmwrite - No longer needed for handle_store
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import generate_uid, ExplicitVRLittleEndian

from src.data_sources.aria import ARIA
from src.data_sources.mim import MIM
from src.data_sources.mosaiq import Mosaiq
from src.backup_systems.orthanc import Orthanc
from ..cli import dicom_utils # Added
from ..cli.dicom_utils import DicomOperationError, DicomConnectionError, InvalidInputError # Added

import logging
import os
import sys

logger = logging.getLogger(__name__)

# Default configuration file paths
CONFIG_DIR = os.path.join(os.path.dirname(__file__), "..", "config")
ENVIRONMENTS_CONFIG_PATH = os.path.join(CONFIG_DIR, "environments.toml")
# DICOM_CONFIG_PATH is no longer directly used by _load_configurations or backup_data top-level
# It might be implicitly used if environments.toml points to AE details that were originally in dicom.toml,
# but the primary config entry point is environments.toml.


# --- Custom Exceptions ---
class BackupError(Exception):
    """Base class for errors specific to this backup script."""


class BackupConfigError(BackupError):
    """Raised for configuration-related errors during backup."""


# --- Core Functions ---
# Removed handle_store function

def _load_configurations(
    environment_name: str, environments_path: str
) -> Dict[str, Any]:
    """Loads the specified environment block from environments.toml."""
    try:
        with open(environments_path, "rb") as f:
            environments_cfg = tomllib.load(f)
    except FileNotFoundError as e:
        msg = f"Environments configuration file error: {e.filename} not found."
        logger.error(msg, exc_info=True)
        raise BackupConfigError(msg) from e
    except tomllib.TOMLDecodeError as e:
        msg = f"TOML decoding error in environments configuration file: {e}"
        logger.error(msg, exc_info=True)
        raise BackupConfigError(msg) from e

    if environment_name not in environments_cfg:
        msg = f"Environment '{environment_name}' not found in {environments_path}"
        logger.error(msg)
        raise BackupConfigError(msg)
    
    logger.info(f"Successfully loaded configuration for environment: {environment_name}")
    return environments_cfg[environment_name]


def _initialize_source_system(
    source_type: str, source_config: Dict[str, Any]
) -> ARIA | MIM | Mosaiq:
    """Initializes and returns the data source system instance."""
    logger.info(f"Initializing data source system of type: {source_type} with config: {source_config.get('aet', source_config.get('db_server'))}") # Log AET or DB server
    if source_type == "aria": # Match type from environments.toml
        return ARIA()
    elif source_type == "mim": # Match type from environments.toml
        return MIM()
    elif source_type == "mosaiq": # Match type from environments.toml
        odbc_driver = source_config.get("odbc_driver")
        logger.debug(f"Mosaiq ODBC driver from source_config: {odbc_driver}")
        return Mosaiq(odbc_driver=odbc_driver)
    else:
        msg = f"Invalid source system type specified: {source_type}"
        logger.error(msg)
        raise BackupConfigError(msg)


def _initialize_orthanc_uploader(
    backup_target_config: Optional[Dict[str, Any]], local_aet_title: str
) -> Optional[Orthanc]:
    """Initializes and returns an Orthanc uploader instance if configured."""
    if not backup_target_config:
        logger.warning(
            "No backup target configuration provided. Orthanc uploader (DICOM mode) cannot be initialized."
        )
        return None

    if not all(k in backup_target_config for k in ["aet", "ip", "port"]):
        logger.warning(
            f"DICOM AE configuration for backup target '{backup_target_config.get('aet', 'UNKNOWN_TARGET')}' "
            f"(requiring aet, ip, port) is incomplete. "
            "Orthanc uploader (DICOM mode) cannot be initialized."
        )
        return None
    
    try:
        uploader = Orthanc(
            calling_aet=local_aet_title,
            peer_aet=backup_target_config['aet'],
            peer_host=backup_target_config['ip'],
            peer_port=int(backup_target_config['port'])
        )
        logger.info(
            f"Orthanc uploader (DICOM mode) initialized for target AET '{backup_target_config['aet']}' "
            f"at {backup_target_config['ip']}:{backup_target_config['port']} "
            f"using calling AET: {local_aet_title}"
        )
        return uploader
    except Exception as e:
        logger.error(f"Failed to initialize Orthanc uploader (DICOM mode): {e}", exc_info=True)
        return None


def _build_aria_mim_cfind_dataset(source_config: Dict[str, Any], env_settings: Dict[str, Any]) -> Dataset:
    """Builds the C-FIND query dataset for ARIA/MIM sources."""
    query_dataset = Dataset()
    
    # Prefer source-specific query level, then environment-level, then default
    query_dataset.QueryRetrieveLevel = source_config.get("dicom_query_level", 
                                                       env_settings.get("dicom_query_level", "SERIES"))

    # Prefer source-specific query keys, then environment-level, then empty (for defaults below)
    dicom_query_keys_config = source_config.get("dicom_query_keys", 
                                                env_settings.get("dicom_query_keys", {}))

    if not dicom_query_keys_config: # If still empty after checking both levels
        logger.warning(
            f"No 'dicom_query_keys' found in source or environment settings. "
            "Using minimal defaults for C-FIND (PatientID='*', Modality='')."
        )
        query_dataset.PatientID = "*"
        query_dataset.Modality = ""
    else:
        logger.info(
            f"Using 'dicom_query_keys' for C-FIND: {dicom_query_keys_config}"
        )
        for key, value in dicom_query_keys_config.items():
            try: 
                Dataset().add_new(key, "LO", "") 
                setattr(query_dataset, key, value)
            except Exception:
                 logger.warning(f"DICOM query key '{key}' from config is not a standard attribute name. Attempting to set as is.")
                 setattr(query_dataset, key, value)

    for essential_key in [
        "PatientID", "StudyDate", "Modality", "SeriesInstanceUID", "StudyInstanceUID", "PatientName"
    ]:
        if not hasattr(query_dataset, essential_key):
            logger.debug(
                f"'{essential_key}' not in query_dataset from config, adding as empty for universal matching."
            )
            setattr(query_dataset, essential_key, "")
    return query_dataset


def _handle_aria_mim_backup(
    source_instance: ARIA | MIM,
    environment_name: str, 
    source_config: Dict[str, Any], # Contains AET, IP, Port directly
    backup_target_config: Dict[str, Any], # Contains AET, IP, Port of final backup
    local_aet_title: str, 
    orthanc_uploader: Optional[Orthanc],
    env_settings: Dict[str, Any] # Contains general settings like max_uids
):
    """Handles the backup workflow for ARIA or MIM sources using direct C-MOVE."""
    query_dataset = _build_aria_mim_cfind_dataset(source_config, env_settings)
    
    # source_config now directly contains AE details for the query
    # e.g., source_config = {'aet': 'ARIA_UCLA_AE', 'ip': '192.168.1.100', 'port': 104, 'type': 'aria'}
    source_name_for_log = source_config.get('aet', environment_name + "_source") # Use AET for logging if available

    logger.info(f"Querying {source_name_for_log} for data with C-FIND dataset: \n{query_dataset}")
    instance_uids_found = source_instance.query(query_dataset, source_config) # Pass full source_config

    logger.info(f"Found {len(instance_uids_found)} instance UID(s) from {source_name_for_log}.")

    if not instance_uids_found:
        logger.info(f"No instances found from {source_name_for_log} matching query criteria.")
        return

    processed_uids_count = 0
    max_uids = env_settings.get("max_uids_per_run", 10 if os.environ.get("CI") else float('inf'))
    
    backup_destination_aet = backup_target_config['aet'] # From [env.backup_targets.ALIAS]

    for uid in list(instance_uids_found)[:max_uids]:
        logger.info(f"Processing instance UID: {uid} for C-MOVE from {source_name_for_log} to {backup_destination_aet}")
        dataset_to_retrieve = Dataset()
        dataset_to_retrieve.QueryRetrieveLevel = "IMAGE" 
        dataset_to_retrieve.PatientID = query_dataset.PatientID 
        dataset_to_retrieve.StudyInstanceUID = query_dataset.StudyInstanceUID
        dataset_to_retrieve.SeriesInstanceUID = query_dataset.SeriesInstanceUID
        dataset_to_retrieve.SOPInstanceUID = uid

        try:
            logger.info(
                f"Initiating C-MOVE for UID {uid} from {source_name_for_log} to destination AET: {backup_destination_aet}."
            )
            transfer_success = source_instance.transfer( 
                dataset_to_retrieve, 
                source_config, # Source AE details for C-MOVE SCU
                backup_destination_aet=backup_destination_aet,
                calling_aet=local_aet_title
            )

            if transfer_success:
                logger.info(f"C-MOVE successful for UID {uid} to {backup_destination_aet}.")
                if orthanc_uploader:
                    logger.info(f"Verifying storage of UID {uid} in backup target {backup_destination_aet} via C-FIND.")
                    # Orthanc uploader's store method now uses C-FIND against its configured peer (the backup target)
                    store_verified = orthanc_uploader.store(sop_instance_uid=uid)
                    if store_verified:
                        logger.info(f"UID {uid} successfully verified in backup target {backup_destination_aet}.")
                    else:
                        logger.warning(f"UID {uid} NOT verified in backup target {backup_destination_aet} after C-MOVE.")
                else:
                    logger.warning("Orthanc uploader not available, skipping storage verification.")
            else:
                logger.error(f"C-MOVE failed for UID {uid} from {source_name_for_log} to {backup_destination_aet}.")
            processed_uids_count += 1
        except Exception as e:
            logger.error(
                f"Error during C-MOVE transfer for UID {uid} from {source_name_for_log}: {e}",
                exc_info=True,
            )
    
    if processed_uids_count < len(instance_uids_found) and len(instance_uids_found) > max_uids:
        logger.info(f"Processed only the first {max_uids} UIDs due to max_uids_per_run limit.")


def _build_mosaiq_dataset_from_row(
    record_data_row: Dict[str, Any] | Tuple[Any, ...], 
    db_column_to_dicom_tag: Dict[str, str],
    dicom_defaults: Dict[str, Any],
    row_index: int 
) -> Dataset:
    """Converts a database row to a pydicom.Dataset."""
    ds = Dataset()
    ds.file_meta = FileMetaDataset()

    if isinstance(record_data_row, dict):
        for db_col, dcm_tag_name in db_column_to_dicom_tag.items():
            if db_col in record_data_row:
                try:
                    setattr(ds, dcm_tag_name, record_data_row[db_col])
                except Exception as e:
                    logger.warning(
                        f"Failed to set DICOM tag '{dcm_tag_name}' from DB column '{db_col}' "
                        f"with value '{record_data_row[db_col]}': {e}"
                    )
            else:
                logger.warning(
                    f"Column '{db_col}' not found in Mosaiq record (row {row_index}) for tag '{dcm_tag_name}'."
                )
    else: 
        logger.warning(
            f"Mosaiq record_data_row (row {row_index}) is a tuple. "
            "Mapping requires db_column_to_dicom_tag to use integer indices "
            "or SQL query to define names for a DictCursor."
        )
        if "PatientID" not in dicom_defaults and not any(v=="PatientID" for v in db_column_to_dicom_tag.values()):
             ds.PatientID = f"MOSAIQ_REC_{row_index + 1}"


    for dcm_tag_name, default_value in dicom_defaults.items():
        if not hasattr(ds, dcm_tag_name):
            setattr(ds, dcm_tag_name, default_value)

    ds.PatientID = getattr(ds, "PatientID", f"MOSAIQ_PAT_{row_index + 1}")
    ds.StudyInstanceUID = getattr(ds, "StudyInstanceUID", None) or generate_uid()
    ds.SeriesInstanceUID = getattr(ds, "SeriesInstanceUID", None) or generate_uid()
    ds.SOPInstanceUID = getattr(ds, "SOPInstanceUID", None) or generate_uid()
    ds.SOPClassUID = getattr(ds, "SOPClassUID", None) or "1.2.840.10008.5.1.4.1.1.481.2" 

    ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
    ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.ImplementationClassUID = generate_uid(prefix="1.2.826.0.1.3680043.9.7156.2.1.")
    ds.file_meta.ImplementationVersionName = "RadOncBackup_Mosaiq_Gen_1.0"
    
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    return ds


def _handle_mosaiq_backup(
    source_instance: Mosaiq,
    environment_name: str, 
    source_config: Dict[str, Any], # Contains db_config, db_column_to_dicom_tag etc.
    backup_target_config: Dict[str, Any], # Final backup destination
    staging_scp_config: Optional[Dict[str, Any]], # Staging SCP config
    local_aet_title: str, 
    orthanc_uploader: Optional[Orthanc], 
    env_settings: Dict[str, Any] # General settings like SQL query
):
    """Handles the backup workflow for Mosaiq sources using C-STORE to staging then C-MOVE."""
    if not staging_scp_config:
        raise BackupConfigError(
            f"Staging SCP configuration is required for Mosaiq backup "
            f"in environment '{environment_name}' but not found in environment config."
        )
    if not all(k in staging_scp_config for k in ["aet", "ip", "port"]):
        raise BackupConfigError(
            f"Staging SCP configuration for Mosaiq in environment '{environment_name}' is incomplete (missing aet, ip, or port)."
        )


    db_config = {
        "server": source_config.get("db_server"),
        "database": source_config.get("db_database"),
        "username": source_config.get("db_username"),
        "password": source_config.get("db_password"), # Ensure this is handled securely
    }
    if not all(db_config.values()):
        msg = f"Database configuration for Mosaiq source in '{environment_name}' is incomplete."
        logger.error(msg)
        raise BackupConfigError(msg)

    db_column_to_dicom_tag = source_config.get("db_column_to_dicom_tag", {})
    dicom_defaults = source_config.get("dicom_defaults", {})
    
    sql_query = env_settings.get("mosaiq_backup_sql_query")
    if not sql_query:
        msg = f"'mosaiq_backup_sql_query' not found in environment settings for '{environment_name}'."
        logger.error(msg)
        raise BackupConfigError(msg)
    
    logger.info(f"Querying Mosaiq with SQL for {environment_name} (first 100 chars): {sql_query[:100]}...")
    rt_records_data = source_instance.query(sql_query, db_config)
    logger.info(f"Found {len(rt_records_data)} records from Mosaiq for {environment_name} to process.")

    for i, record_data_row in enumerate(rt_records_data):
        try:
            ds = _build_mosaiq_dataset_from_row(
                record_data_row, db_column_to_dicom_tag, dicom_defaults, i
            )
            patient_id_for_log = getattr(ds, 'PatientID', 'UnknownPatientID')
            sop_uid_for_log = ds.SOPInstanceUID
            logger.info(
                f"Attempting C-STORE of Mosaiq record (PatientID: {patient_id_for_log}, "
                f"SOPInstanceUID: {sop_uid_for_log}) to staging SCP: {staging_scp_config['aet']}"
            )
            # For Mosaiq.transfer, staging_scp_config provides the target AET, IP, Port for C-STORE
            store_to_staging_success = source_instance.transfer(ds, staging_scp_config)

            if store_to_staging_success:
                logger.info(f"Successfully C-STORED UID {sop_uid_for_log} to staging SCP {staging_scp_config['aet']}.")
                
                move_args = Namespace(
                    aet=local_aet_title,
                    aec=staging_scp_config['aet'],
                    host=staging_scp_config['ip'],
                    port=int(staging_scp_config['port']),
                    move_dest_aet=backup_target_config['aet'], # Final backup target
                    query_level="IMAGE", 
                    patient_id=ds.PatientID, 
                    study_uid=ds.StudyInstanceUID, 
                    series_uid=ds.SeriesInstanceUID, 
                    sop_instance_uid=ds.SOPInstanceUID,
                    verbose=False 
                )
                logger.info(f"Attempting C-MOVE for UID {sop_uid_for_log} from staging {staging_scp_config['aet']} to {backup_target_config['aet']}")
                try:
                    dicom_utils._handle_move_scu(move_args) 
                    logger.info(f"C-MOVE successful for UID {sop_uid_for_log} from staging to {backup_target_config['aet']}.")
                    if orthanc_uploader:
                        logger.info(f"Verifying storage of UID {sop_uid_for_log} in backup target {backup_target_config['aet']} via C-FIND.")
                        store_verified = orthanc_uploader.store(sop_instance_uid=sop_uid_for_log)
                        if store_verified:
                            logger.info(f"UID {sop_uid_for_log} successfully verified in backup target {backup_target_config['aet']}.")
                        else:
                            logger.warning(f"UID {sop_uid_for_log} NOT verified in backup target {backup_target_config['aet']} after C-MOVE from staging.")
                    else:
                        logger.warning("Orthanc uploader not available, skipping storage verification after C-MOVE from staging.")
                except (DicomOperationError, DicomConnectionError, InvalidInputError) as e:
                    logger.error(f"C-MOVE from staging failed for UID {sop_uid_for_log}: {e}", exc_info=True)
                except Exception as e: 
                    logger.error(f"Unexpected error during C-MOVE from staging for UID {sop_uid_for_log}: {e}", exc_info=True)
            else:
                logger.error(f"C-STORE to staging SCP {staging_scp_config['aet']} failed for UID {sop_uid_for_log}.")
        except Exception as e:
            logger.error(f"Failed to process or C-STORE dataset from Mosaiq row {i} to staging: {e}", exc_info=True)


def backup_data(environment_name: str, source_alias: Optional[str] = None):
    """
    Main function to perform data backup for a specified environment and source.
    Orchestrates configuration loading, source/backup system initialization, and workflow execution.
    """
    logger.info(f"Starting backup for environment: {environment_name}, Source Alias: {source_alias or 'Default'}")
    
    try:
        env_block = _load_configurations(environment_name, ENVIRONMENTS_CONFIG_PATH)

        script_ae_config = env_block.get('script_ae')
        if not script_ae_config or not script_ae_config.get('aet'):
            raise BackupConfigError(f"Missing 'script_ae' configuration or 'aet' in environment '{environment_name}'.")
        local_aet_title = script_ae_config['aet']

        actual_source_alias = source_alias or env_block.get('default_source')
        if not actual_source_alias:
            raise BackupConfigError(f"No source alias provided and no 'default_source' defined for environment '{environment_name}'.")
        
        all_sources_config = env_block.get('sources', {})
        current_source_config = all_sources_config.get(actual_source_alias)
        if not current_source_config:
            raise BackupConfigError(f"Configuration for source alias '{actual_source_alias}' not found in environment '{environment_name}'.")

        source_type = current_source_config.get('type')
        if not source_type:
            raise BackupConfigError(f"Missing 'type' for source alias '{actual_source_alias}' in environment '{environment_name}'.")

        actual_backup_alias = env_block.get('default_backup') # Backup destination is not chosen by CLI arg for now
        if not actual_backup_alias:
            raise BackupConfigError(f"No 'default_backup' alias defined for environment '{environment_name}'.")

        all_backup_targets = env_block.get('backup_targets', {})
        current_backup_target_config = all_backup_targets.get(actual_backup_alias)
        if not current_backup_target_config:
            raise BackupConfigError(f"Configuration for backup target alias '{actual_backup_alias}' not found in environment '{environment_name}'.")

        env_settings = env_block.get('settings', {})
        
        source_instance = _initialize_source_system(source_type, current_source_config)
        
        current_orthanc_uploader = _initialize_orthanc_uploader(current_backup_target_config, local_aet_title)

        if source_type in ["aria", "mim"]:
            _handle_aria_mim_backup(
                source_instance, 
                environment_name, 
                current_source_config, 
                current_backup_target_config, 
                local_aet_title, 
                current_orthanc_uploader,
                env_settings
            )
        elif source_type == "mosaiq":
            # Determine staging SCP config. Mosaiq source config might specify an alias for its staging target.
            staging_scp_alias = current_source_config.get('staging_target_alias', 'STAGING_SCP_FOR_MOSAIQ') # Default convention
            staging_scp_config = all_backup_targets.get(staging_scp_alias)
            if not staging_scp_config:
                 logger.warning(f"Staging SCP alias '{staging_scp_alias}' for Mosaiq source '{actual_source_alias}' not found in backup_targets. Proceeding without staging if not strictly required by handler.")
            
            _handle_mosaiq_backup(
                source_instance, 
                environment_name, 
                current_source_config, 
                current_backup_target_config,
                staging_scp_config, # Pass the resolved staging config
                local_aet_title, 
                current_orthanc_uploader,   
                env_settings         
            )
        else:
            raise BackupConfigError(f"Unsupported source type '{source_type}' for alias '{actual_source_alias}'.")

    except (BackupConfigError, ValueError) as e: 
        logger.error(f"Backup configuration or setup error for environment '{environment_name}': {e}", exc_info=True)
        raise 
    except Exception as e:
        logger.error(f"An unexpected error occurred during backup for environment '{environment_name}': {e}", exc_info=True)
        raise BackupError(f"Unexpected backup failure for {environment_name}: {e}") from e

    logger.info(f"Backup process for environment '{environment_name}', source '{actual_source_alias}' completed.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stdout,
    )
    # Ensure the module's logger also uses this basic config if no other handlers are set
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler(sys.stdout))
        logger.setLevel(logging.INFO)


    parser = argparse.ArgumentParser(
        description="Backup DICOM data from configured environments."
    )
    parser.add_argument(
        "environment_name",
        type=str,
        help="Name of the environment to use (defined in environments.toml).",
    )
    parser.add_argument(
        "source_alias",
        type=str,
        nargs='?', # Optional positional argument
        default=None, # Will be None if not provided
        help="Alias of the source to backup (defined under the environment's [sources] in environments.toml). Uses default_source if not provided.",
    )
    args = parser.parse_args()

    try:
        backup_data(args.environment_name, args.source_alias)
        sys.exit(0)
    except (BackupConfigError, ValueError) as e: # ValueError is often from config issues
        # Error message already logged by backup_data or its helpers
        print(f"Error: {e}", file=sys.stderr) # User-facing concise error
        sys.exit(1)
    except BackupError as e: # Other backup specific errors
        print(f"Backup Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e: # Unexpected errors
        # backup_data should have logged this with exc_info=True
        print(f"An unexpected critical error occurred: {e}", file=sys.stderr)
        sys.exit(1)
