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
import tomllib
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
DICOM_CONFIG_PATH = os.path.join(CONFIG_DIR, "dicom.toml")


# --- Custom Exceptions ---
class BackupError(Exception):
    """Base class for errors specific to this backup script."""


class BackupConfigError(BackupError):
    """Raised for configuration-related errors during backup."""


# --- Core Functions ---
# Removed handle_store function

def _load_configurations(
    environment_name: str, environments_path: str, dicom_path: str
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]]:
    """Loads and validates environment, DICOM, local AE, and staging SCP configurations."""
    try:
        with open(environments_path, "rb") as f:
            environments_cfg = tomllib.load(f)
        with open(dicom_path, "rb") as f:
            dicom_cfg = tomllib.load(f)
    except FileNotFoundError as e:
        msg = f"Configuration file error: {e.filename} not found."
        logger.error(msg, exc_info=True)
        raise BackupConfigError(msg) from e
    except tomllib.TOMLDecodeError as e:
        msg = f"TOML decoding error in configuration file: {e}"
        logger.error(msg, exc_info=True)
        raise BackupConfigError(msg) from e

    if environment_name not in environments_cfg:
        msg = f"Environment '{environment_name}' not found in {environments_path}"
        logger.error(msg)
        raise BackupConfigError(msg)
    env_config = environments_cfg[environment_name]

    source_name = env_config.get("source") or env_config.get("source1")
    if not source_name:
        msg = f"No 'source' or 'source1' defined for environment '{environment_name}'."
        logger.error(msg)
        raise BackupConfigError(msg)

    source_ae_details = dicom_cfg.get(source_name)
    if not source_ae_details:
        msg = f"AE details for source '{source_name}' not found in {dicom_path}."
        logger.error(msg)
        raise BackupConfigError(msg)

    local_ae_config_name = "backup_script_ae" # Mandatory name for this script's AE config
    local_ae_config = dicom_cfg.get(local_ae_config_name)
    if not local_ae_config or not local_ae_config.get("AETitle"):
        msg = (f"Local AE configuration '{local_ae_config_name}' with an 'AETitle' "
               f"not found or incomplete in {dicom_path}.")
        logger.error(msg)
        raise BackupConfigError(msg)
    
    # Staging SCP config is optional at load time, checked by Mosaiq handler
    staging_scp_config_name = "staging_scp_for_mosaiq"
    staging_scp_config = dicom_cfg.get(staging_scp_config_name)
    if staging_scp_config:
        logger.info(f"Loaded staging SCP configuration: {staging_scp_config_name}")
    else:
        logger.info(f"Staging SCP configuration '{staging_scp_config_name}' not found. This is only required for Mosaiq backups.")

    return env_config, dicom_cfg, source_ae_details, local_ae_config, staging_scp_config


def _initialize_source_system(
    source_name: str, env_config: Dict[str, Any], source_ae_details: Dict[str, Any]
) -> ARIA | MIM | Mosaiq:
    """Initializes and returns the data source system instance."""
    logger.info(f"Initializing data source system: {source_name}")
    if source_name == "ARIA":
        return ARIA()
    elif source_name == "MIM":
        return MIM()
    elif source_name == "Mosaiq":
        odbc_driver = env_config.get("mosaiq_odbc_driver")
        logger.debug(f"Mosaiq ODBC driver from env_config: {odbc_driver}")
        return Mosaiq(odbc_driver=odbc_driver)
    else:
        msg = f"Invalid source system specified: {source_name}"
        logger.error(msg)
        raise BackupConfigError(msg)


def _initialize_orthanc_uploader(
    env_config: Dict[str, Any], dicom_cfg: Dict[str, Any], local_aet_title: str
) -> Optional[Orthanc]:
    """Initializes and returns an Orthanc uploader instance if configured."""
    backup_target_config_name = env_config.get("backup")
    if not backup_target_config_name:
        logger.warning(
            f"No 'backup' destination defined for environment. Orthanc uploader (DICOM mode) cannot be initialized."
        )
        return None

    orthanc_ae_config = dicom_cfg.get(backup_target_config_name)
    if not orthanc_ae_config or not all(k in orthanc_ae_config for k in ["AETitle", "IP", "Port"]):
        logger.warning(
            f"DICOM AE configuration for backup target '{backup_target_config_name}' "
            f"(requiring AETitle, IP, Port) not found or incomplete in DICOM config. "
            "Orthanc uploader (DICOM mode) cannot be initialized."
        )
        return None
    
    try:
        uploader = Orthanc(
            calling_aet=local_aet_title,
            peer_aet=orthanc_ae_config['AETitle'],
            peer_host=orthanc_ae_config['IP'],
            peer_port=int(orthanc_ae_config['Port'])
        )
        logger.info(
            f"Orthanc uploader (DICOM mode) initialized for target '{backup_target_config_name}' "
            f"(AET: {orthanc_ae_config['AETitle']}, Host: {orthanc_ae_config['IP']}:{orthanc_ae_config['Port']}) "
            f"using calling AET: {local_aet_title}"
        )
        return uploader
    except Exception as e:
        logger.error(f"Failed to initialize Orthanc uploader (DICOM mode): {e}", exc_info=True)
        return None


def _build_aria_mim_cfind_dataset(env_config: Dict[str, Any], environment_name: str) -> Dataset:
    """Builds the C-FIND query dataset for ARIA/MIM sources."""
    query_dataset = Dataset()
    query_dataset.QueryRetrieveLevel = env_config.get("dicom_query_level", "SERIES")

    dicom_query_keys_config = env_config.get("dicom_query_keys")
    if not dicom_query_keys_config or not isinstance(dicom_query_keys_config, dict):
        logger.warning(
            f"'dicom_query_keys' is missing or not a table in environment '{environment_name}'. "
            "Using minimal defaults for C-FIND (PatientID='*', Modality='')."
        )
        query_dataset.PatientID = "*"
        query_dataset.Modality = ""
    else:
        logger.info(
            f"Using 'dicom_query_keys' from environment config for C-FIND: {dicom_query_keys_config}"
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
    env_config: Dict[str, Any],
    source_ae_details: Dict[str, Any],
    dicom_cfg: Dict[str, Any],
    local_aet_title: str, # Added
    orthanc_uploader: Optional[Orthanc]
):
    """Handles the backup workflow for ARIA or MIM sources using direct C-MOVE."""
    query_dataset = _build_aria_mim_cfind_dataset(env_config, environment_name)
    source_name = env_config.get("source") 
    backup_target_name = env_config.get("backup")

    if not backup_target_name or backup_target_name not in dicom_cfg:
        logger.error(f"Backup target AE '{backup_target_name}' not configured in DICOM config for ARIA/MIM workflow. Skipping transfers.")
        return
    backup_target_ae_config = dicom_cfg[backup_target_name]

    logger.info(f"Querying {source_name} for data with C-FIND dataset: \n{query_dataset}")
    instance_uids_found = source_instance.query(query_dataset, source_ae_details)

    logger.info(f"Found {len(instance_uids_found)} instance UID(s) from {source_name}.")

    if not instance_uids_found:
        logger.info(f"No instances found from {source_name} matching query criteria.")
        return

    processed_uids_count = 0
    max_uids = env_config.get("max_uids_per_run", 10 if os.environ.get("CI") else float('inf'))

    for uid in list(instance_uids_found)[:max_uids]:
        logger.info(f"Processing instance UID: {uid} for C-MOVE from {source_name} to {backup_target_name}")
        dataset_to_retrieve = Dataset()
        dataset_to_retrieve.QueryRetrieveLevel = "IMAGE" # C-MOVE for a single instance requires IMAGE level
        # PatientID, StudyInstanceUID, SeriesInstanceUID might be needed by some SCPs for IMAGE level C-MOVE
        # For simplicity, we assume SOPInstanceUID is enough, but this might need adjustment
        dataset_to_retrieve.PatientID = query_dataset.PatientID # Carry over from C-FIND if available
        dataset_to_retrieve.StudyInstanceUID = query_dataset.StudyInstanceUID
        dataset_to_retrieve.SeriesInstanceUID = query_dataset.SeriesInstanceUID
        dataset_to_retrieve.SOPInstanceUID = uid

        try:
            logger.info(
                f"Initiating C-MOVE for UID {uid} from {source_name} to destination AET: {backup_target_ae_config['AETitle']}."
            )
            transfer_success = source_instance.transfer( # type: ignore
                dataset_to_retrieve, 
                source_ae_details, 
                backup_destination_aet=backup_target_ae_config['AETitle'],
                calling_aet=local_aet_title
            )

            if transfer_success:
                logger.info(f"C-MOVE successful for UID {uid} to {backup_target_name}.")
                if orthanc_uploader:
                    logger.info(f"Verifying storage of UID {uid} in Orthanc backup via C-FIND.")
                    store_verified = orthanc_uploader.store(sop_instance_uid=uid)
                    if store_verified:
                        logger.info(f"UID {uid} successfully verified in Orthanc backup.")
                    else:
                        logger.warning(f"UID {uid} NOT verified in Orthanc backup after C-MOVE.")
                else:
                    logger.warning("Orthanc uploader not available, skipping storage verification.")
            else:
                logger.error(f"C-MOVE failed for UID {uid} from {source_name} to {backup_target_name}.")
            processed_uids_count += 1
        except Exception as e:
            logger.error(
                f"Error during C-MOVE transfer for UID {uid} from {source_name}: {e}",
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

    # Ensure essential UIDs are present for C-MOVE
    ds.PatientID = getattr(ds, "PatientID", f"MOSAIQ_PAT_{row_index + 1}")
    ds.StudyInstanceUID = getattr(ds, "StudyInstanceUID", None) or generate_uid()
    ds.SeriesInstanceUID = getattr(ds, "SeriesInstanceUID", None) or generate_uid()
    ds.SOPInstanceUID = getattr(ds, "SOPInstanceUID", None) or generate_uid()
    ds.SOPClassUID = getattr(ds, "SOPClassUID", None) or "1.2.840.10008.5.1.4.1.1.481.2" # RT Beams Treatment Record Default

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
    env_config: Dict[str, Any],
    source_ae_details: Dict[str, Any],
    dicom_cfg: Dict[str, Any],
    local_aet_title: str, # Added
    orthanc_uploader: Optional[Orthanc], # Added
    staging_scp_config: Optional[Dict[str, Any]] # Added
):
    """Handles the backup workflow for Mosaiq sources using C-STORE to staging then C-MOVE."""
    if not staging_scp_config:
        raise BackupConfigError(
            f"Staging SCP configuration ('staging_scp_for_mosaiq') is required for Mosaiq backup "
            f"in environment '{environment_name}' but not found in DICOM config."
        )

    db_config = source_ae_details.get("db_config")
    if not db_config:
        msg = f"Database configuration ('db_config') for Mosaiq source '{env_config['source']}' not found in DICOM config."
        logger.error(msg)
        raise BackupConfigError(msg)

    db_column_to_dicom_tag = source_ae_details.get("db_column_to_dicom_tag", {})
    dicom_defaults = source_ae_details.get("dicom_defaults", {})
    if not db_column_to_dicom_tag:
        logger.warning(f"Mosaiq 'db_column_to_dicom_tag' mapping not found for source '{env_config['source']}'. Dataset creation will be minimal.")

    sql_query = env_config.get("mosaiq_backup_sql_query")
    if not sql_query:
        msg = f"'mosaiq_backup_sql_query' not found in environment configuration for '{environment_name}'."
        logger.error(msg)
        raise BackupConfigError(msg)
    
    logger.info(f"Querying Mosaiq with SQL from configuration (first 100 chars): {sql_query[:100]}...")
    rt_records_data = source_instance.query(sql_query, db_config)
    logger.info(f"Found {len(rt_records_data)} records from Mosaiq to process.")

    backup_target_aet_name = env_config.get("backup")
    if not backup_target_aet_name or backup_target_aet_name not in dicom_cfg:
        msg = (f"Backup target AE '{backup_target_aet_name}' not configured in DICOM config. "
               "Mosaiq records cannot be fully backed up.")
        logger.error(msg)
        if rt_records_data: # Only critical if there's data to backup
            raise BackupConfigError(f"Missing backup target AE configuration for Mosaiq environment '{environment_name}'.")
        return
    backup_target_ae_config = dicom_cfg[backup_target_aet_name]

    for i, record_data_row in enumerate(rt_records_data):
        try:
            ds = _build_mosaiq_dataset_from_row(
                record_data_row, db_column_to_dicom_tag, dicom_defaults, i
            )
            patient_id_for_log = getattr(ds, 'PatientID', 'UnknownPatientID')
            sop_uid_for_log = ds.SOPInstanceUID
            logger.info(
                f"Attempting C-STORE of Mosaiq record (PatientID: {patient_id_for_log}, "
                f"SOPInstanceUID: {sop_uid_for_log}) to staging SCP: {staging_scp_config['AETitle']}"
            )
            store_to_staging_success = source_instance.transfer(ds, staging_scp_config)

            if store_to_staging_success:
                logger.info(f"Successfully C-STORED UID {sop_uid_for_log} to staging SCP {staging_scp_config['AETitle']}.")
                
                move_args = Namespace(
                    aet=local_aet_title,
                    aec=staging_scp_config['AETitle'],
                    host=staging_scp_config['IP'],
                    port=int(staging_scp_config['Port']),
                    move_dest_aet=backup_target_ae_config['AETitle'],
                    query_level="IMAGE", 
                    patient_id=ds.PatientID, 
                    study_uid=ds.StudyInstanceUID, 
                    series_uid=ds.SeriesInstanceUID, 
                    sop_instance_uid=ds.SOPInstanceUID,
                    verbose=False # Consider making this configurable
                )
                logger.info(f"Attempting C-MOVE for UID {sop_uid_for_log} from staging {staging_scp_config['AETitle']} to {backup_target_ae_config['AETitle']}")
                try:
                    dicom_utils._handle_move_scu(move_args) # Assumes this raises on failure
                    logger.info(f"C-MOVE successful for UID {sop_uid_for_log} from staging to {backup_target_ae_config['AETitle']}.")
                    if orthanc_uploader:
                        logger.info(f"Verifying storage of UID {sop_uid_for_log} in Orthanc backup via C-FIND.")
                        store_verified = orthanc_uploader.store(sop_instance_uid=sop_uid_for_log)
                        if store_verified:
                            logger.info(f"UID {sop_uid_for_log} successfully verified in Orthanc backup.")
                        else:
                            logger.warning(f"UID {sop_uid_for_log} NOT verified in Orthanc backup after C-MOVE from staging.")
                    else:
                        logger.warning("Orthanc uploader not available, skipping storage verification after C-MOVE from staging.")
                except (DicomOperationError, DicomConnectionError, InvalidInputError) as e:
                    logger.error(f"C-MOVE from staging failed for UID {sop_uid_for_log}: {e}", exc_info=True)
                except Exception as e: # Catch any other unexpected errors from dicom_utils
                    logger.error(f"Unexpected error during C-MOVE from staging for UID {sop_uid_for_log}: {e}", exc_info=True)

            else:
                logger.error(f"C-STORE to staging SCP {staging_scp_config['AETitle']} failed for UID {sop_uid_for_log}.")
        except Exception as e:
            logger.error(f"Failed to process or C-STORE dataset from Mosaiq row {i} to staging: {e}", exc_info=True)


def backup_data(environment_name: str):
    """
    Main function to perform data backup for a specified environment.
    Orchestrates configuration loading, source/backup system initialization, and workflow execution.
    """
    logger.info(f"Starting backup for environment: {environment_name}")
    
    current_orthanc_uploader: Optional[Orthanc] = None # Initialize to None

    try:
        env_config, dicom_cfg, source_ae_details, local_ae_config, staging_scp_config = _load_configurations(
            environment_name, ENVIRONMENTS_CONFIG_PATH, DICOM_CONFIG_PATH
        )
        source_name = env_config["source"] 
        source_instance = _initialize_source_system(
            source_name, env_config, source_ae_details
        )
        
        # Initialize Orthanc uploader once, using the local_ae_config's AETitle
        current_orthanc_uploader = _initialize_orthanc_uploader(env_config, dicom_cfg, local_ae_config['AETitle'])

        if source_name in ["ARIA", "MIM"]:
            _handle_aria_mim_backup(
                source_instance, # type: ignore 
                environment_name, 
                env_config, 
                source_ae_details, 
                dicom_cfg, 
                local_ae_config['AETitle'], # Pass local AET
                current_orthanc_uploader
            )
        elif source_name == "Mosaiq":
            _handle_mosaiq_backup(
                source_instance, # type: ignore
                environment_name, 
                env_config, 
                source_ae_details, 
                dicom_cfg,
                local_ae_config['AETitle'], # Pass local AET
                current_orthanc_uploader,    # Pass uploader
                staging_scp_config         # Pass staging config
            )
        # Else case for invalid source_name is handled in _initialize_source_system

    except (BackupConfigError, ValueError) as e: 
        logger.error(f"Backup configuration or setup error for environment '{environment_name}': {e}", exc_info=True)
        raise 
    except Exception as e:
        logger.error(f"An unexpected error occurred during backup for environment '{environment_name}': {e}", exc_info=True)
        raise BackupError(f"Unexpected backup failure for {environment_name}: {e}") from e

    logger.info(f"Backup process for environment '{environment_name}' completed.")


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
        "environment",
        type=str,
        help="Name of the environment to backup (must be defined in environments.toml).",
    )
    args = parser.parse_args()

    try:
        backup_data(args.environment)
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
