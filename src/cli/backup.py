"""
DICOM Backup CLI.

This script provides a command-line interface to initiate the backup process
for DICOM data from configured source systems to a backup destination (e.g., Orthanc).
It uses environment configurations defined in TOML files to determine source
details, backup parameters, and data source types (ARIA, MIM, Mosaiq).

The main function `backup_data` orchestrates the loading of configurations,
instantiation of appropriate data source and backup system interfaces,
and the subsequent query and transfer operations.

Workflow for DICOM (ARIA/MIM) sources:
1. Query the source system (C-FIND).
2. For each found instance/series, initiate a transfer (C-MOVE or C-GET).
   - This involves starting a temporary local C-STORE SCP to receive data.
   - The `handle_store` function processes each received instance.
     (Note: Current `handle_store` only logs; actual backup to Orthanc from here is a TODO).

Workflow for Mosaiq (SQL + DICOM C-STORE) sources:
1. Query the Mosaiq database using a SQL query.
2. For each record found:
   - Convert database row to a DICOM dataset (Placeholder logic).
   - Transfer the DICOM dataset to the backup DICOM C-STORE SCP (e.g., Orthanc's DICOM endpoint).

Usage:
    python -m src.cli.backup <environment_name>

Example:
    python -m src.cli.backup TJU_Mosaiq
"""
import argparse
import tomllib
from pydicom.dataset import Dataset
from src.data_sources.aria import ARIA
from src.data_sources.mim import MIM
from src.data_sources.mosaiq import Mosaiq
from src.backup_systems.orthanc import Orthanc
import logging
import os # For potential future config path joining

logger = logging.getLogger(__name__)

# Default configuration file paths (can be overridden or enhanced)
CONFIG_DIR = os.path.join(os.path.dirname(__file__), '..', 'config') # Assuming cli is in src/cli
ENVIRONMENTS_CONFIG_PATH = os.path.join(CONFIG_DIR, 'environments.toml')
DICOM_CONFIG_PATH = os.path.join(CONFIG_DIR, 'dicom.toml') # For AE details and potentially Mosaiq DB details

def handle_store(event):
    """
    Handles a DICOM C-STORE service request event.

    This function is typically used as an event handler for `evt.EVT_C_STORE`
    when running a C-STORE SCP. It processes the incoming dataset.

    :param event: The event object containing the dataset and other context.
    :type event: pynetdicom.events.Event
    :return: Status code 0x0000 (Success).
    :rtype: int
    """
    ds = event.dataset
    # Ensure file_meta is present, as it might not be if received over network
    # and not explicitly set by the sender or pynetdicom.
    if not hasattr(ds, 'file_meta'):
        ds.file_meta = Dataset()
        # Populate required file meta attributes if needed, e.g. TransferSyntaxUID
        # For now, assume it's either present or will be handled by subsequent processing.
    logger.info(f"Received SOPInstanceUID: {ds.SOPInstanceUID} via C-STORE.")
    # In a real scenario, you would save ds to disk or process it further.
    return 0x0000

def backup_data(environment_name: str):
    """
    Performs data backup for a specified environment.

    This function loads configurations, determines the data source based on the environment,
    queries the source, and then transfers the data to a backup system.

    :param environment_name: The name of the environment to back up (e.g., 'UCLA', 'TJU').
                             This name should correspond to a section in `environments.toml`.
    :type environment_name: str
    :raises ValueError: If the environment or source system configuration is invalid or missing.
    """
    logger.info(f"Starting backup for environment: {environment_name}")
    
    # Load configurations
    # It's better to load all configs at once if they are needed.
    try:
        with open(ENVIRONMENTS_CONFIG_PATH, 'rb') as f:
            environments_cfg = tomllib.load(f)
        with open(DICOM_CONFIG_PATH, 'rb') as f: # For AE details and Mosaiq DB Config
            dicom_cfg = tomllib.load(f)
    except FileNotFoundError as e:
        logger.error(f"Configuration file not found: {e.filename}", exc_info=True)
        raise
    except tomllib.TOMLDecodeError as e:
        logger.error(f"Error decoding TOML configuration: {e}", exc_info=True)
        raise

    if environment_name not in environments_cfg:
        logger.error(f"Environment '{environment_name}' not found in {ENVIRONMENTS_CONFIG_PATH}")
        raise ValueError(f"Environment '{environment_name}' not found.")
    env_config = environments_cfg[environment_name]

    # Determine primary data source name
    # Handles 'source' for single source environments or 'source1' for multi-source (takes first)
    source_name = env_config.get('source') or env_config.get('source1')
    if not source_name:
        logger.error(f"No 'source' or 'source1' defined for environment '{environment_name}'.")
        raise ValueError(f"No primary data source defined for environment '{environment_name}'.")

    logger.info(f"Primary data source for {environment_name} is '{source_name}'.")

    source_ae_details = dicom_cfg.get(source_name)
    if not source_ae_details:
        logger.error(f"AE details for source '{source_name}' not found in {DICOM_CONFIG_PATH}.")
        raise ValueError(f"Configuration for source '{source_name}' not found.")

    source = None
    if source_name == 'ARIA':
        source = ARIA()
    elif source_name == 'MIM':
        source = MIM()
    elif source_name == 'Mosaiq':
        odbc_driver = env_config.get('mosaiq_odbc_driver')
        logger.debug(f"Mosaiq ODBC driver from env_config: {odbc_driver}")
        source = Mosaiq(odbc_driver=odbc_driver)
    else:
        logger.error(f"Invalid source system specified for {environment_name}: {source_name}")
        raise ValueError(f"Invalid source system: {source_name}")

    # Backup destination (Orthanc) - currently hardcoded URL in Orthanc class
    # backup_system = Orthanc() # This was the unused variable

    # Determine backup destination AE Title from env_config (e.g., env_config['backup'] = "ORTHANC_AET")
    # This would be used if transferring via DICOM to Orthanc, but current Orthanc class uses REST.
    # backup_dest_aet = env_config.get('backup')
    # if not backup_dest_aet:
    #     logger.error(f"No 'backup' destination AE Title defined for environment '{environment_name}'.")
    #     raise ValueError(f"Backup destination not defined for '{environment_name}'.")

    if source_name == 'Mosaiq':
        # Mosaiq-specific query and transfer logic
        # The db_config should come from dicom_cfg (e.g., dicom_cfg['Mosaiq']['db'])
        db_config = source_ae_details.get('db_config') # Assuming db_config is nested under Mosaiq in dicom.toml
        if not db_config:
            logger.error(f"Database configuration ('db_config') for Mosaiq source '{source_name}' not found in {DICOM_CONFIG_PATH}.")
            # Fallback to old hardcoded for now, but this is an error.
            db_config = { 
                'server': 'your_server_TODO', 'database': 'your_database_TODO',
                'username': 'your_username_TODO', 'password': 'your_password_TODO'
            }
            logger.warning("Using fallback hardcoded db_config for Mosaiq. This needs to be configured.")

        # Example SQL query - this should ideally be more configurable
        sql_query = "SELECT * FROM RTRECORDS WHERE TreatmentDate = CONVERT(date, GETDATE())" # SQL Server specific
        logger.info(f"Querying Mosaiq with SQL: {sql_query}")
        rt_records_data = source.query(sql_query, db_config) # This returns list of dicts/rows usually
        
        logger.info(f"Found {len(rt_records_data)} records from Mosaiq to process.")
        for record_data_row in rt_records_data:
            # The Mosaiq.transfer method expects a pydicom Dataset.
            # We need to convert the row (dict or tuple) from DB into a Dataset.
            # This requires knowing the mapping from DB columns to DICOM tags.
            # This part is complex and application-specific.
            # For now, assuming `record_data_row` IS what `source.transfer` expects
            # or that `source.transfer` handles this conversion if `record_data_row` is not a Dataset.
            # Based on current Mosaiq.transfer, it expects a pydicom.Dataset.
            # This part of the code is incomplete for Mosaiq.
            logger.warning(f"Mosaiq data processing: row-to-Dataset conversion and transfer logic is placeholder for record: {record_data_row}")
            # Example: ds = pydicom.Dataset(); ds.PatientID = record_data_row['PatientID'] ...
            # Then: source.transfer(ds, store_scp_details_for_backup_system)
            # The 'env_config['backup']' was used as store_scp for Mosaiq.transfer previously.
            # This implies env_config['backup'] is a DICOM AE (Orthanc's DICOM AET).
            # Let's assume env_config['backup'] is the AET of the Orthanc Store SCP.
            # We need full AE details for store_scp for Mosaiq.transfer.
            backup_target_aet_name = env_config.get('backup')
            if not backup_target_aet_name or backup_target_aet_name not in dicom_cfg:
                logger.error(f"Backup target AE '{backup_target_aet_name}' not configured in {DICOM_CONFIG_PATH}.")
                continue # Skip this record
            
            store_scp_details = dicom_cfg[backup_target_aet_name]
            
            # This is a placeholder for creating the rt_record Dataset from record_data_row
            # For now, creating a dummy dataset to allow the flow to be tested.
            # In a real scenario, this would map DB columns to DICOM tags.
            rt_dataset_placeholder = Dataset()
            rt_dataset_placeholder.PatientID = record_data_row.get('PatientID', 'Unknown') if isinstance(record_data_row, dict) else 'Unknown'
            # Add other essential DICOM tags based on record_data_row
            
            logger.info(f"Attempting to transfer record for PatientID: {rt_dataset_placeholder.PatientID} to {backup_target_aet_name}")
            source.transfer(rt_dataset_placeholder, store_scp_details) # Mosaiq.transfer is C-STORE SCU
            
    else: # ARIA or MIM (DICOM Q/R and C-MOVE/C-GET)
        query_dataset = Dataset()
        query_dataset.QueryRetrieveLevel = 'SERIES' # Example query level
        query_dataset.Modality = 'RTRECORD' # Example modality
        query_dataset.SeriesInstanceUID = '' # Request SeriesInstanceUID
        query_dataset.PatientID = '' # Wildcard PatientID for broad query; refine in production
        query_dataset.StudyDate = '' # Wildcard StudyDate; refine in production
        query_dataset.StudyInstanceUID = '' # Request StudyInstanceUID

        logger.info(f"Querying {source_name} for data...")
        # ARIA/MIM.query(query_dataset, qr_scp_config)
        # source_ae_details contains IP, Port, AETitle for the source Q/R SCP
        instance_uids_found = source.query(query_dataset, source_ae_details)
        
        logger.info(f"Found {len(instance_uids_found)} instance UID(s) from {source_name}.")

        if not instance_uids_found:
            logger.info(f"No instances found from {source_name} matching criteria. Backup for this source might be complete or query too narrow.")
            # return # Exit if nothing to backup

        # For ARIA/MIM, transfer involves C-MOVE or C-GET.
        # The current ARIA/MIM.transfer methods start their own temporary SCP.
        # We need to provide:
        # 1. move_dataset/get_dataset (specifying what to retrieve, per UID)
        # 2. qr_scp (which is source_ae_details)
        # 3. local_store_config (dict with 'IP', 'Port', 'AETitle' for the temp SCP)
        # 4. c_store_handler (our handle_store function)

        # Define local SCP details for ARIA/MIM transfer methods
        # This SCP will be started by ARIA/MIM.transfer() methods.
        # The IP should be the local machine's IP accessible by the source PACS.
        # Port should be a free port. AETitle is for our temp SCP.
        local_scp_config_for_transfer = {
            "IP": "0.0.0.0", # Listen on all interfaces, or specific IP if needed
            "Port": 11112, # Example port, ensure it's free and accessible
            "AETitle": "BACKUP_SCP_TEMP" 
        }
        # Ensure this AETitle is unique and clear.
        # Note: If multiple backups run concurrently, this fixed port/AET could clash.
        # A dynamic port assignment or more robust SCP management might be needed for parallel runs.

        processed_uids_count = 0
        MAX_UIDS_TO_PROCESS_PER_RUN = 10 # Safety break for testing
        for uid in list(instance_uids_found)[:MAX_UIDS_TO_PROCESS_PER_RUN]:
            logger.info(f"Processing instance UID: {uid} for transfer from {source_name}")
            dataset_to_retrieve = Dataset()
            # QueryRetrieveLevel for C-MOVE/C-GET should be specific to what the UID represents.
            # Assuming UIDs from query are SOPInstanceUIDs and we want to retrieve at IMAGE level.
            dataset_to_retrieve.QueryRetrieveLevel = 'IMAGE' 
            dataset_to_retrieve.SOPInstanceUID = uid
            # Potentially add StudyInstanceUID and SeriesInstanceUID if known and required by Q/R SCP for IMAGE level retrieve.

            try:
                # Call transfer method (ARIA or MIM)
                # ARIA/MIM.transfer(dataset_to_retrieve, source_ae_details, local_scp_config_for_transfer, handle_store)
                # The handle_store function will process each received instance (e.g., send to Orthanc).
                # However, the current `handle_store` just logs and returns success.
                # It needs to be enhanced to actually send the data to the Orthanc backup system.
                # This is a significant gap.
                # For now, the call will make it *seem* like data is retrieved.
                logger.warning("The `handle_store` function currently only logs received instances. "
                               "It does NOT transfer them to the Orthanc backup system. This needs implementation.")
                
                source.transfer(dataset_to_retrieve, source_ae_details, local_scp_config_for_transfer, handle_store)
                processed_uids_count +=1
                logger.info(f"Transfer initiated for UID {uid}. Check logs from {source_name}.transfer and handle_store.")

            except Exception as e:
                logger.error(f"Error during transfer initiation for UID {uid} from {source_name}: {e}", exc_info=True)
        
        if processed_uids_count < len(instance_uids_found):
            logger.info(f"Processed only the first {MAX_UIDS_TO_PROCESS_PER_RUN} UIDs due to safety break.")

    logger.info(f"Backup process for environment '{environment_name}' completed.")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    parser = argparse.ArgumentParser(description='Backup DICOM data from configured environments.')
    parser.add_argument('environment', type=str, help='Name of the environment to backup (must be defined in environments.toml).')
    # Consider adding --config_dir if ENVS_CONFIG_PATH and DICOM_CONFIG_PATH are not fixed.
    args = parser.parse_args()
    
    try:
        backup_data(args.environment)
    except ValueError as ve:
        logger.error(f"Backup initialization failed: {ve}")
    except Exception as ex:
        logger.error(f"An unexpected error occurred during backup: {ex}", exc_info=True)
