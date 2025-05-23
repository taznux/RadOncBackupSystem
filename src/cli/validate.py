import argparse
import logging
import tomllib
from pydicom.dataset import Dataset
from pynetdicom import AE, evt, StoragePresentationContexts, ALL_TRANSFER_SYNTAXES
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind, StudyRootQueryRetrieveInformationModelMove, VerificationSOPClass
from src.backup_systems.orthanc import Orthanc # Import Orthanc
import io # For BytesIO
import pydicom # For dcmwrite
import os # For path operations
import tempfile # For temporary directory
import shutil # For removing directory

# Global list to store received datasets during C-MOVE
GLOBAL_RECEIVED_DATASETS = []

def _handle_move_store(event):
    """Handle a C-STORE event from C-MOVE."""
    # This is the handler for instances received during C-MOVE
    # ds is a pydicom Dataset
    ds = event.dataset
    # Ensure it has file_meta
    if not hasattr(ds, 'file_meta'):
        ds.file_meta = pydicom.Dataset()
        ds.file_meta.TransferSyntaxUID = pydicom.uid.ImplicitVRLittleEndian # Default if not specified
        ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
        ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    
    # We need to convert dataset to bytes to be used by Orthanc.verify()
    # pynetdicom's event.dataset is already a pydicom.Dataset object.
    # We need to save it to a BytesIO object to get the byte stream.
    with io.BytesIO() as bio:
        pydicom.dcmwrite(bio, ds, write_like_original=False) # write_like_original=False to ensure our meta is used
        GLOBAL_RECEIVED_DATASETS.append(bio.getvalue())
    
    print(f"Received and processed SOPInstanceUID: {ds.SOPInstanceUID} for verification.")
    return 0x0000 # Success

def validate_data(config_file, environment):
    global GLOBAL_RECEIVED_DATASETS
    with open(config_file, 'rb') as f:
        config = tomllib.load(f)
    
    env_config = config[environment]
    # Ensure 'source' and 'backup' keys exist in env_config
    if 'source' not in env_config or 'backup' not in env_config:
        print(f"Error: 'source' or 'backup' not defined for environment '{environment}' in environments.toml")
        return

    source_name = env_config['source']
    backup_name = env_config['backup'] # This should be the name of the Orthanc instance, e.g., "ORTHANC_MAIN"

    # Ensure source_name and backup_name exist as keys in the main config (dicom.toml)
    if source_name not in config:
        print(f"Error: Configuration for source '{source_name}' not found in dicom.toml.")
        return
    # We don't strictly need backup_config for Orthanc class if URL is hardcoded there,
    # but it's good practice to load it if it exists.
    # if backup_name not in config:
    #     print(f"Error: Configuration for backup '{backup_name}' not found in dicom.toml.")
    #     return

    source_config = config[source_name]
    # backup_ae_config = config.get(backup_name, {}) # Get backup AE details if present

    # Instantiate Orthanc backup system
    # The Orthanc class in this project uses a hardcoded URL.
    # If it were to use backup_ae_config, we'd pass it here.
    orthanc_verifier = Orthanc()

    # Our local AE that will receive C-STORE from C-MOVE
    # The port should be available. Port 0 means OS chooses a free port.
    # However, we need to tell the source PACS which port to send to.
    # For simplicity, use a fixed local port for now. Ensure it's free.
    local_ae_port = 11113 # Example port, ensure it's not in use and firewall allows
    local_ae_title = "VALIDATE_SCP"

    ae = AE(ae_title=local_ae_title)
    # Add presentation contexts for the C-STORE SCP part of C-MOVE
    # It should support receiving what the source can send.
    # For maximum compatibility, accept all standard storage SOP classes and transfer syntaxes.
    for context in StoragePresentationContexts:
        ae.add_supported_context(context.abstract_syntax, ALL_TRANSFER_SYNTAXES)
    
    # Add requested contexts for C-FIND and C-MOVE SCU roles
    ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)
    ae.add_requested_context(StudyRootQueryRetrieveInformationModelMove)
    ae.add_requested_context(VerificationSOPClass) # For C-ECHO

    # Start SCP server in a background thread to handle incoming C-STORE from C-MOVE
    scp_handlers = [(evt.EVT_C_STORE, _handle_move_store)]
    scp_server = ae.start_server(("localhost", local_ae_port), block=False, evt_handlers=scp_handlers)
    print(f"Local SCP server started on port {local_ae_port} with AE Title {local_ae_title}")

    # C-ECHO to source to verify connectivity
    assoc_echo = ae.associate(source_config['IP'], source_config['Port'], ae_title=source_config['AETitle'])
    if assoc_echo.is_established:
        print(f"Performing C-ECHO to source {source_config['AETitle']}...")
        status_echo = assoc_echo.send_c_echo()
        if status_echo and status_echo.Status == 0x0000:
            print("C-ECHO successful.")
        else:
            print(f"C-ECHO failed. Status: {status_echo.Status if status_echo else 'Unknown'}. Aborting validation.")
            assoc_echo.release()
            scp_server.shutdown()
            return
        assoc_echo.release()
    else:
        print(f"C-ECHO association to {source_config['AETitle']} failed. Aborting.")
        scp_server.shutdown()
        return

    # C-FIND to get a list of studies/series/instances
    # For validation, let's try to get a few series.
    # Querying for PatientID='*' and Study Level can return a lot.
    # Let's refine the query or limit results if possible.
    # For now, query for SERIES level.
    query_ds = Dataset()
    query_ds.QueryRetrieveLevel = 'SERIES' # Changed from STUDY to SERIES
    query_ds.PatientID = '*' # For demonstration, get all patients. Consider a specific patient for real validation.
    # Add other query keys if needed, e.g., StudyDate, Modality
    # query_ds.StudyDate = "20230101-20231231" # Example: limit by date
    query_ds.SeriesInstanceUID = "" # Return SeriesInstanceUID
    query_ds.SOPInstanceUID = ""    # Return SOPInstanceUID (though for Series level, this might be empty)

    datasets_to_verify_details = []

    print(f"Attempting C-FIND to {source_config['AETitle']} at {source_config['IP']}:{source_config['Port']}")
    assoc_find = ae.associate(source_config['IP'], source_config['Port'], ae_title=source_config['AETitle'])
    if assoc_find.is_established:
        print("C-FIND Association established.")
        # responses is a generator
        responses = assoc_find.send_c_find(query_ds, StudyRootQueryRetrieveInformationModelFind)
        for status_dataset, identifier_dataset in responses:
            if status_dataset is None:
                print("C-FIND Error: Connection timed out or aborted.")
                break 
            
            # Correctly handle C-FIND "Pending" status and check identifier
            if status_dataset.Status == 0xFF00 or status_dataset.Status == 0xFF01: # Pending
                if identifier_dataset:
                    # This identifier_dataset contains attributes of a found Series
                    print(f"Found Series: {identifier_dataset.get('SeriesInstanceUID', 'N/A')}, Patient: {identifier_dataset.get('PatientID', 'N/A')}")
                    # We need StudyInstanceUID and SeriesInstanceUID to request C-MOVE
                    study_uid = identifier_dataset.get('StudyInstanceUID')
                    series_uid = identifier_dataset.get('SeriesInstanceUID')
                    if study_uid and series_uid:
                         datasets_to_verify_details.append({'StudyInstanceUID': study_uid, 'SeriesInstanceUID': series_uid})
                else:
                    print("Pending status with no identifier.")
            elif status_dataset.Status == 0x0000: # Success (end of responses)
                print("C-FIND completed successfully.")
                if identifier_dataset: # Should be None or empty for success status
                     print(f"Success status with identifier: {identifier_dataset}")
                break # End of C-FIND
            else: # Failure
                print(f"C-FIND failed. Status: 0x{status_dataset.Status:04X}")
                break
        assoc_find.release()
    else:
        print(f"C-FIND association to {source_config['AETitle']} failed.")
        scp_server.shutdown()
        return
    
    print(f"Found {len(datasets_to_verify_details)} series to potentially retrieve and verify.")
    # Limit the number of series to process for this example to avoid excessive operations
    # In a real scenario, you might want to process all or a significant random sample.
    MAX_SERIES_TO_VALIDATE = 2 # Example limit
    
    validation_success_count = 0
    validation_failure_count = 0

    for i, series_details in enumerate(datasets_to_verify_details[:MAX_SERIES_TO_VALIDATE]):
        print(f"\nProcessing Series {i+1}/{len(datasets_to_verify_details[:MAX_SERIES_TO_VALIDATE])}: {series_details['SeriesInstanceUID']}")
        GLOBAL_RECEIVED_DATASETS = [] # Clear for each C-MOVE

        move_dataset = Dataset()
        move_dataset.QueryRetrieveLevel = 'SERIES'
        move_dataset.StudyInstanceUID = series_details['StudyInstanceUID']
        move_dataset.SeriesInstanceUID = series_details['SeriesInstanceUID']
        
        assoc_move = ae.associate(source_config['IP'], source_config['Port'], ae_title=source_config['AETitle'])
        if assoc_move.is_established:
            print(f"Attempting C-MOVE for Series {series_details['SeriesInstanceUID']} to {local_ae_title}")
            # The destination AETitle for C-MOVE is our local AE title
            responses = assoc_move.send_c_move(move_dataset, local_ae_title, StudyRootQueryRetrieveInformationModelMove)
            
            # Iterate through C-MOVE responses to ensure command completion
            for status_dataset_move, _ in responses: # Second element is usually None for C-MOVE responses
                if status_dataset_move is None:
                    print("C-MOVE Error: Connection timed out or aborted during C-MOVE.")
                    break
                if status_dataset_move.Status == 0xFF00 or status_dataset_move.Status == 0xFF01: # Pending
                    # Number of Remaining/Completed/Warning/Failed sub-operations
                    # print(f"C-MOVE Pending: {status_dataset_move.NumberOfRemainingSuboperations} remaining")
                    pass # Just wait for completion
                elif status_dataset_move.Status == 0x0000: # Success
                    print(f"C-MOVE for series {series_details['SeriesInstanceUID']} completed successfully.")
                    break
                else: # Failure
                    print(f"C-MOVE for series {series_details['SeriesInstanceUID']} failed. Status: 0x{status_dataset_move.Status:04X}")
                    # You might want to log elements from status_dataset_move like ErrorComment or OffendingElement
                    break
            assoc_move.release()
        else:
            print(f"C-MOVE association to {source_config['AETitle']} failed for series {series_details['SeriesInstanceUID']}.")
            continue # Move to next series

        # Now GLOBAL_RECEIVED_DATASETS should contain the byte data of instances from the C-MOVE
        if not GLOBAL_RECEIVED_DATASETS:
            print(f"No instances were received via C-MOVE for series {series_details['SeriesInstanceUID']}.")
            # This could be normal if the series is empty, or an issue with C-MOVE/SCP.
        
        for instance_bytes in GLOBAL_RECEIVED_DATASETS:
            print(f"Verifying instance (size: {len(instance_bytes)} bytes) in Orthanc...")
            if orthanc_verifier.verify(instance_bytes):
                print("Instance verified successfully in Orthanc.")
                validation_success_count += 1
            else:
                print("Instance verification failed in Orthanc.")
                validation_failure_count += 1
        
        GLOBAL_RECEIVED_DATASETS = [] # Clear after processing

    # Shutdown the SCP server
    scp_server.shutdown()
    print("\nLocal SCP server shut down.")
    print(f"Validation Summary: Successes: {validation_success_count}, Failures: {validation_failure_count}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Validate DICOM data')
    parser.add_argument('config_file', type=str, help='Path to the configuration file')
    parser.add_argument('environment', type=str, help='Environment to use for validation (e.g., UCLA, TJU)')
    args = parser.parse_args()

    validate_data(args.config_file, args.environment)
