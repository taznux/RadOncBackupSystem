import argparse
import logging
import tomllib
from pydicom.dataset import Dataset
from pynetdicom import AE, evt, StoragePresentationContexts
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind, StudyRootQueryRetrieveInformationModelMove

def handle_store(event):
    """Handle a C-STORE service request"""
    ds = event.dataset
    ds.file_meta = event.file_meta
    return 0x0000

def validate_data(config_file, environment):
    with open(config_file, 'rb') as f:
        config = tomllib.load(f)
    
    env_config = config[environment]
    source_config = config[env_config['source']]
    backup_config = config[env_config['backup']]

    ae = AE()
    ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)
    ae.add_requested_context(StudyRootQueryRetrieveInformationModelMove)
    ae.supported_contexts = StoragePresentationContexts

    query_ds = Dataset()
    query_ds.QueryRetrieveLevel = 'STUDY'
    query_ds.PatientID = '*'

    assoc = ae.associate(source_config['IP'], source_config['Port'], ae_title=source_config['AETitle'])
    if assoc.is_established:
        responses = assoc.send_c_find(query_ds, StudyRootQueryRetrieveInformationModelFind)
        for (status, identifier) in responses:
            if status and status.Status == 0x0000:
                print(f"Data validation successful for {identifier.PatientID}")
            else:
                print(f"Data validation failed for {identifier.PatientID}")
        assoc.release()
    else:
        print('Association rejected, aborted or never connected')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Validate DICOM data')
    parser.add_argument('config_file', type=str, help='Path to the configuration file')
    parser.add_argument('environment', type=str, help='Environment to use for validation (e.g., UCLA, TJU)')
    args = parser.parse_args()

    validate_data(args.config_file, args.environment)
