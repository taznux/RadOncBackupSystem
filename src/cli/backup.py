import argparse
import tomllib
from pydicom.dataset import Dataset
from src.data_sources.aria import ARIA
from src.data_sources.mim import MIM
from src.data_sources.mosaiq import Mosaiq
from src.backup_systems.orthanc import Orthanc

def handle_store(event):
    """Handle a C-STORE service request"""
    ds = event.dataset
    ds.file_meta = event.file_meta
    # global moved_ds # Unused
    # moved_ds = ds # Unused
    return 0x0000

def backup_data(environment):
    with open('src/config/environments.toml', 'rb') as f:
        environments = tomllib.load(f)
    env_config = environments[environment]

    if env_config['source'] == 'ARIA':
        source = ARIA()
    elif env_config['source'] == 'MIM':
        source = MIM()
    elif env_config['source'] == 'Mosaiq':
        source = Mosaiq()
    else:
        raise ValueError("Invalid source system")

    # backup = Orthanc() # Unused variable

    if env_config['source'] == 'Mosaiq':
        sql_query = "SELECT * FROM RTRECORDS WHERE TreatmentDate = CURDATE()"
        db_config = {
            'server': 'your_server',
            'database': 'your_database',
            'username': 'your_username',
            'password': 'your_password'
        }
        rt_records = source.query(sql_query, db_config)
        for rt_record in rt_records:
            source.transfer(rt_record, env_config['backup'])
    else:
        query_dataset = Dataset()
        query_dataset.QueryRetrieveLevel = 'SERIES'
        query_dataset.Modality = 'RTRECORD'
        query_dataset.SeriesInstanceUID = ''
        query_dataset.PatientID = ''
        query_dataset.StudyDate = ''
        query_dataset.StudyInstanceUID = ''

        uids = source.query(query_dataset, env_config['source'])
        for uid in uids:
            move_dataset = Dataset()
            move_dataset.QueryRetrieveLevel = 'IMAGE'
            move_dataset.SOPInstanceUID = uid
            source.transfer(move_dataset, env_config['source'], env_config['backup'], handle_store)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Backup DICOM data')
    parser.add_argument('environment', type=str, help='Environment to use for backup (e.g., UCLA, TJU)')
    args = parser.parse_args()
    backup_data(args.environment)
