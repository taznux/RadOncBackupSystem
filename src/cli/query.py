import argparse
import tomllib
from pydicom.dataset import Dataset
from src.data_sources.aria import ARIA
from src.data_sources.mim import MIM
from src.data_sources.mosaiq import Mosaiq

def load_config(config_file):
    with open(config_file, 'rb') as f:
        return tomllib.load(f)

def query_data_source(data_source, query_dataset, qr_scp):
    if data_source == 'ARIA':
        source = ARIA()
    elif data_source == 'MIM':
        source = MIM()
    elif data_source == 'Mosaiq':
        source = Mosaiq()
    else:
        raise ValueError(f"Unknown data source: {data_source}")
    
    return source.query(query_dataset, qr_scp)

def main():
    parser = argparse.ArgumentParser(description="Query information from data sources")
    parser.add_argument('--config', required=True, help="Path to the configuration file")
    parser.add_argument('--source', required=True, choices=['ARIA', 'MIM', 'Mosaiq'], help="Data source to query")
    parser.add_argument('--mrn', help="Medical Record Number")
    parser.add_argument('--study_date', help="Study date in the format YYYYMMDD or YYYYMMDD-YYYYMMDD")
    parser.add_argument('--treatment_date', help="Treatment date in the format YYYYMMDD or YYYYMMDD-YYYYMMDD")
    args = parser.parse_args()

    config = load_config(args.config)
    qr_scp = config['qr_scp']

    query_dataset = Dataset()
    query_dataset.QueryRetrieveLevel = 'SERIES'
    query_dataset.Modality = 'RTRECORD'
    query_dataset.SeriesInstanceUID = ''
    query_dataset.PatientID = args.mrn
    # query_dataset.StudyDate = args.study_date # Replaced by logic below

    if args.treatment_date:
        query_dataset.StudyDate = args.treatment_date
    elif args.study_date:
        query_dataset.StudyDate = args.study_date
    else:
        query_dataset.StudyDate = ''  # Wildcard if no date is provided

    query_dataset.StudyInstanceUID = ''

    uids = query_data_source(args.source, query_dataset, qr_scp)
    print(f"Found UIDs: {uids}")

if __name__ == '__main__':
    main()
