"""
DICOM Backup CLI.

This script provides a command-line interface to initiate the backup process
for DICOM data from configured source systems to a backup destination (e.g., Orthanc).
It uses environment configurations defined in TOML files to determine source
details, backup parameters, and data source types (ARIA, MIM, Mosaiq).
"""
import argparse
import functools # For functools.partial
import io
import tomllib
from typing import Optional, Dict, Any, Tuple, List # Added Tuple, List

from pydicom import dcmwrite
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import generate_uid, ExplicitVRLittleEndian

from src.data_sources.aria import ARIA
from src.data_sources.mim import MIM
from src.data_sources.mosaiq import Mosaiq
from src.backup_systems.orthanc import Orthanc # For type hinting
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
def handle_store(orthanc_uploader: Optional[Orthanc], event: Any): # Added orthanc_uploader arg
    """
    Handles a DICOM C-STORE service request event.
    If orthanc_uploader is provided, attempts to store the received dataset to Orthanc.

    Args:
        orthanc_uploader: An initialized Orthanc instance, or None.
        event: The pynetdicom event object containing the dataset and other context.
    
    Returns:
        DICOM status code (0x0000 for Success).
    """
    ds = event.dataset

    if not hasattr(ds, "file_meta"):
        ds.file_meta = FileMetaDataset()

    if not getattr(ds.file_meta, "TransferSyntaxUID", None):
        ds.file_meta.TransferSyntaxUID = (
            event.context.transfer_syntax[0]
            if event.context.transfer_syntax
            else ExplicitVRLittleEndian
        )
    if not getattr(ds.file_meta, "MediaStorageSOPClassUID", None):
        ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
    if not getattr(ds.file_meta, "MediaStorageSOPInstanceUID", None):
        ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID

    transfer_syntax = ds.file_meta.TransferSyntaxUID
    ds.is_little_endian = transfer_syntax.is_little_endian
    ds.is_implicit_VR = transfer_syntax.is_implicit_VR

    logger.info(f"Received SOPInstanceUID: {ds.SOPInstanceUID} via C-STORE.")

    if orthanc_uploader:
        try:
            logger.debug(
                f"Attempting to store SOPInstanceUID {ds.SOPInstanceUID} to Orthanc."
            )
            with io.BytesIO() as bio:
                dcmwrite(bio, ds, write_like_original=False)
                dataset_bytes = bio.getvalue()

            orthanc_uploader.store(dataset_bytes)
            logger.info(
                f"Successfully stored SOPInstanceUID {ds.SOPInstanceUID} to Orthanc."
            )
        except Exception as e:
            logger.error(
                f"Failed to store SOPInstanceUID {ds.SOPInstanceUID} to Orthanc: {e}",
                exc_info=True,
            )
    else:
        logger.warning(
            f"Orthanc uploader not configured/provided. SOPInstanceUID {ds.SOPInstanceUID} "
            "received but not backed up to Orthanc."
        )

    return 0x0000  # Success for the C-STORE SCP operation


def _load_configurations(
    environment_name: str, environments_path: str, dicom_path: str
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Loads and validates environment and DICOM configurations."""
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

    return env_config, dicom_cfg, source_ae_details


def _initialize_source_system(
    source_name: str, env_config: Dict[str, Any], source_ae_details: Dict[str, Any]
) -> ARIA | MIM | Mosaiq: # Using union type for return
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
    env_config: Dict[str, Any], dicom_cfg: Dict[str, Any]
) -> Optional[Orthanc]:
    """Initializes and returns an Orthanc uploader instance if configured."""
    backup_target_config_name = env_config.get("backup")
    if not backup_target_config_name:
        logger.warning(
            f"No 'backup' destination defined for environment. Orthanc uploader cannot be initialized."
        )
        return None

    orthanc_config = dicom_cfg.get(backup_target_config_name)
    if not orthanc_config or "URL" not in orthanc_config:
        logger.warning(
            f"Orthanc URL for backup target '{backup_target_config_name}' not found or incomplete in DICOM config. "
            "Orthanc uploader cannot be initialized."
        )
        return None
    
    try:
        uploader = Orthanc(orthanc_url=orthanc_config["URL"])
        logger.info(
            f"Orthanc uploader initialized for target '{backup_target_config_name}' at {orthanc_config['URL']}"
        )
        return uploader
    except Exception as e:
        logger.error(f"Failed to initialize Orthanc uploader: {e}", exc_info=True)
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
            try: # Check if key is a valid DICOM keyword before setting
                Dataset().add_new(key, "LO", "") # Test if valid keyword, type/value unimportant
                setattr(query_dataset, key, value)
            except Exception:
                 logger.warning(f"DICOM query key '{key}' from config is not a standard attribute name. Attempting to set as is.")
                 setattr(query_dataset, key, value)


    # Ensure essential keys for Q/R are present
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
    environment_name: str, # For logging context
    env_config: Dict[str, Any],
    source_ae_details: Dict[str, Any],
    dicom_cfg: Dict[str, Any],
    local_scp_config: Dict[str, Any],
    orthanc_uploader: Optional[Orthanc]
):
    """Handles the backup workflow for ARIA or MIM sources."""
    query_dataset = _build_aria_mim_cfind_dataset(env_config, environment_name)
    source_name = env_config.get("source") # For logging

    logger.info(f"Querying {source_name} for data with C-FIND dataset: \n{query_dataset}")
    instance_uids_found = source_instance.query(query_dataset, source_ae_details) # type: ignore

    logger.info(f"Found {len(instance_uids_found)} instance UID(s) from {source_name}.")

    if not instance_uids_found:
        logger.info(f"No instances found from {source_name} matching query criteria.")
        return

    if not orthanc_uploader:
        logger.error(
            f"Orthanc uploader not initialized for environment {environment_name}. "
            f"Cannot backup received instances from {source_name}. Skipping transfers."
        )
        return

    logger.info(
        f"Local C-STORE SCP for {source_name} transfer will listen on "
        f"{local_scp_config['Port']} with AET {local_scp_config['AETitle']}"
    )
    
    # Bind the orthanc_uploader to handle_store for this specific backup operation
    bound_handle_store = functools.partial(handle_store, orthanc_uploader)

    processed_uids_count = 0
    max_uids = env_config.get("max_uids_per_run", 10 if os.environ.get("CI") else float('inf'))

    for uid in list(instance_uids_found)[:max_uids]:
        logger.info(f"Processing instance UID: {uid} for transfer from {source_name}")
        dataset_to_retrieve = Dataset()
        dataset_to_retrieve.QueryRetrieveLevel = "IMAGE"
        dataset_to_retrieve.SOPInstanceUID = uid

        try:
            logger.info(
                f"Initiating transfer for UID {uid} from {source_name} to local SCP, then to Orthanc."
            )
            source_instance.transfer( # type: ignore
                dataset_to_retrieve, source_ae_details, local_scp_config, bound_handle_store
            )
            processed_uids_count += 1
        except Exception as e:
            logger.error(
                f"Error during transfer initiation for UID {uid} from {source_name}: {e}",
                exc_info=True,
            )
    
    if processed_uids_count < len(instance_uids_found) and len(instance_uids_found) > max_uids:
        logger.info(f"Processed only the first {max_uids} UIDs due to max_uids_per_run limit.")


def _build_mosaiq_dataset_from_row(
    record_data_row: Dict[str, Any] | Tuple[Any, ...], # Can be dict or tuple
    db_column_to_dicom_tag: Dict[str, str],
    dicom_defaults: Dict[str, Any],
    row_index: int # For logging
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
    else: # Assuming tuple
        logger.warning(
            f"Mosaiq record_data_row (row {row_index}) is a tuple. "
            "Mapping requires db_column_to_dicom_tag to use integer indices "
            "or SQL query to define names for a DictCursor."
        )
        # Basic placeholder if not a dict and no specific tuple mapping logic
        if "PatientID" not in dicom_defaults and not any(v=="PatientID" for v in db_column_to_dicom_tag.values()):
             ds.PatientID = f"MOSAIQ_REC_{row_index + 1}"


    for dcm_tag_name, default_value in dicom_defaults.items():
        if not hasattr(ds, dcm_tag_name):
            setattr(ds, dcm_tag_name, default_value)

    # UID Generation and File Meta
    ds.SOPInstanceUID = getattr(ds, "SOPInstanceUID", None) or generate_uid()
    ds.SeriesInstanceUID = getattr(ds, "SeriesInstanceUID", None) or generate_uid()
    ds.StudyInstanceUID = getattr(ds, "StudyInstanceUID", None) or generate_uid()
    ds.SOPClassUID = getattr(ds, "SOPClassUID", None) or "1.2.840.10008.5.1.4.1.1.7" # Secondary Capture fallback

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
    environment_name: str, # For logging context
    env_config: Dict[str, Any],
    source_ae_details: Dict[str, Any],
    dicom_cfg: Dict[str, Any],
):
    """Handles the backup workflow for Mosaiq sources."""
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
               "Mosaiq records cannot be transferred.")
        logger.error(msg)
        if rt_records_data:
            raise BackupConfigError(f"Missing backup target AE configuration for Mosaiq environment '{environment_name}'.")
        return

    store_scp_details = dicom_cfg[backup_target_aet_name]

    for i, record_data_row in enumerate(rt_records_data):
        try:
            ds = _build_mosaiq_dataset_from_row(
                record_data_row, db_column_to_dicom_tag, dicom_defaults, i
            )
            patient_id_for_log = getattr(ds, 'PatientID', 'UnknownPatientID')
            logger.info(
                f"Attempting to transfer Mosaiq record (PatientID: {patient_id_for_log}, "
                f"SOPInstanceUID: {ds.SOPInstanceUID}) to {backup_target_aet_name}"
            )
            source_instance.transfer(ds, store_scp_details)
        except Exception as e:
            logger.error(f"Failed to process or transfer dataset from Mosaiq row {i}: {e}", exc_info=True)


def backup_data(environment_name: str):
    """
    Main function to perform data backup for a specified environment.
    Orchestrates configuration loading, source/backup system initialization, and workflow execution.
    """
    logger.info(f"Starting backup for environment: {environment_name}")
    
    # This assignment is for the functools.partial approach with handle_store.
    # It will be properly initialized if the source is ARIA/MIM.
    # For Mosaiq, it remains None, and handle_store is not used.
    current_orthanc_uploader: Optional[Orthanc] = None

    try:
        env_config, dicom_cfg, source_ae_details = _load_configurations(
            environment_name, ENVIRONMENTS_CONFIG_PATH, DICOM_CONFIG_PATH
        )
        source_name = env_config["source"] # Safe to access after _load_configurations
        source_instance = _initialize_source_system(
            source_name, env_config, source_ae_details
        )

        if source_name in ["ARIA", "MIM"]:
            current_orthanc_uploader = _initialize_orthanc_uploader(env_config, dicom_cfg)
            # Proceed with ARIA/MIM backup even if Orthanc uploader init failed (it will log warnings)
            
            local_scp_services_config = dicom_cfg.get("local_dicom_services", {}).get("backup_scp", {})
            local_scp_aetitle = local_scp_services_config.get("AETitle")
            local_scp_port = local_scp_services_config.get("Port")

            if not local_scp_aetitle or not local_scp_port:
                msg = (f"'local_dicom_services.backup_scp' with 'AETitle' and 'Port' "
                       f"not fully configured in {DICOM_CONFIG_PATH}. Cannot start local SCP for ARIA/MIM.")
                raise BackupConfigError(msg)

            local_scp_config = {
                "IP": local_scp_services_config.get("IP", "0.0.0.0"),
                "Port": local_scp_port,
                "AETitle": local_scp_aetitle,
            }
            _handle_aria_mim_backup(
                source_instance, environment_name, env_config, source_ae_details, dicom_cfg, local_scp_config, current_orthanc_uploader
            )
        elif source_name == "Mosaiq":
            _handle_mosaiq_backup(
                source_instance, environment_name, env_config, source_ae_details, dicom_cfg # type: ignore
            )
        # Else case for invalid source_name is handled in _initialize_source_system

    except (BackupConfigError, ValueError) as e: # ValueError can be raised by source system init
        logger.error(f"Backup configuration or setup error for environment '{environment_name}': {e}", exc_info=True)
        raise # Re-raise for main to handle exit code
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
