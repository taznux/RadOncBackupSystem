from datetime import datetime, timedelta
import logging.config
import re
import tomllib

import pandas as pd
from pydicom.dataset import Dataset
from pynetdicom import AE, evt, StoragePresentationContexts
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind, StudyRootQueryRetrieveInformationModelMove, RTBeamsTreatmentRecordStorage

RECORDS_REPORT = []

with open ('logging.toml', 'rb') as f:
    logging.config.dictConfig(tomllib.load(f))
    LOGGER = logging.getLogger('scu_move')

with open ('config.toml', 'rb') as f:
    sockets = tomllib.load(f)
    SCU = sockets['local']
    QR_SCP = sockets['mim_server_qr']
    STORE_SCP = sockets['local']

def handle_store(event):
    """Handle a C-STORE service request"""
    global RECORDS_REPORT
    ds = event.dataset
    # ds.file_meta = event.file_meta
    # ds.save_as('data/' + ds.SOPInstanceUID, write_like_original=False)
    keys = [
        'PatientID',
        'PatientName',
        'PatientBirthDate',
        'PatientSex',
        'PhysiciansOfRecord',
        'StudyDescription',
        'TreatmentDate',
        'NumberOfFractionsPlanned',
        'CurrentFractionNumber',
        'TreatmentMachineName',
        'ReferencedSOPInstanceUID',
        'StudyInstanceUID'
    ]
    record = {key: getattr(ds, key, None) for key in keys}
    record['PatientBirthDate'] = datetime.strptime(record['PatientBirthDate'], '%Y%m%d').strftime('%m/%d/%Y')
    record['TreatmentDate'] = datetime.strptime(record['TreatmentDate'], '%Y%m%d').strftime('%m/%d/%Y')
    record['CurrentFractionNumber'] = ds.TreatmentSessionBeamSequence[0].CurrentFractionNumber
    record['TreatmentMachineName'] = ds.TreatmentMachineSequence[0].TreatmentMachineName
    record['ReferencedSOPInstanceUID'] = ds.ReferencedRTPlanSequence[0].ReferencedSOPInstanceUID
    RECORDS_REPORT.append(record)
    return 0x0000

def find_uids(*, mrn='', study_date='', treatment_date=''):
    single_date_format, date_range_format = re.compile(r'\d{8}'), re.compile(r'\d{8}-\d{8}')
    if study_date and not (single_date_format.match(study_date) or date_range_format.match(study_date)):
        raise ValueError('Date must be in the format "YYYYMMDD" or "YYYYMMDD-YYYYMMDD"')
    if treatment_date and not (single_date_format.match(treatment_date) or date_range_format.match(treatment_date)):
        raise ValueError('Date must be in the format "YYYYMMDD" or "YYYYMMDD-YYYYMMDD"')
    if not mrn and not study_date:
        raise ValueError('mrn or study_date must be provided')
    
    series_ds = Dataset()
    series_ds.QueryRetrieveLevel = 'SERIES'
    series_ds.Modality = 'RTRECORD'
    series_ds.SeriesInstanceUID = ''
    series_ds.PatientID = mrn
    series_ds.StudyDate = study_date
    series_ds.StudyInstanceUID = ''

    image_ds = Dataset()
    image_ds.QueryRetrieveLevel = 'IMAGE'
    image_ds.TreatmentDate = treatment_date
    image_ds.SOPInstanceUID = ''


    ae = AE(SCU['AETitle'])
    ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)
    assoc = ae.associate(QR_SCP['IP'], QR_SCP['Port'], ae_title=QR_SCP['AETitle'])
    if assoc.is_established:
        LOGGER.info(f"Association established with {QR_SCP['AETitle']}")
        latest_records_per_study = []

        # Get SeriesInstanceUIDs grouped by Study
        studies = dict()
        responses = assoc.send_c_find(series_ds, StudyRootQueryRetrieveInformationModelFind)
        for _, ds in responses:
            if ds is None: 
                continue
            elif studies.get(ds.StudyInstanceUID):
                studies[ds.StudyInstanceUID].add(ds.SeriesInstanceUID)
            else:
                studies[ds.StudyInstanceUID] = set([ds.SeriesInstanceUID])
        print(f'Number of Studies: {len(studies)}')
        print(f'Number of Series: {sum(len(series) for series in studies.values())}')

        # Get SOPInstanceUIDs (one per series)
        for series_list in studies.values():
            records = []
            for series_uid in series_list:
                image_ds.SeriesInstanceUID = series_uid
                responses = assoc.send_c_find(image_ds, StudyRootQueryRetrieveInformationModelFind)
                record = [(ds.TreatmentDate, getattr(ds,'TreatmentTime','0'), ds.SOPInstanceUID) for _, ds in responses if ds][0]
                records.append(record)

            tx_dates = treatment_date.split('-')
            records = [record for record in records if tx_dates[0] <= record[0] <= tx_dates[-1]]
            if records:
                latest_records_per_study.append(max(records)[-1])

        print(f'Number of Records: {len(latest_records_per_study)}')
        assoc.release()
        LOGGER.info('Association released')
    else:
        LOGGER.error('Association rejected, aborted or never connected')
    return latest_records_per_study

def generate_report(uids: list):
    if not uids:
        LOGGER.warning('No records found. Report will not be generated.')
        return None
    
    ae = AE(SCU['AETitle'])
    ae.add_requested_context(StudyRootQueryRetrieveInformationModelMove)
    ae.supported_contexts = StoragePresentationContexts
    handlers = [(evt.EVT_C_STORE, handle_store)]
    scp = ae.start_server((STORE_SCP['IP'], STORE_SCP['Port']), block=False, evt_handlers=handlers)
    LOGGER.info(f"SCP started at {STORE_SCP['IP']}:{STORE_SCP['Port']}")

    assoc = ae.associate(QR_SCP['IP'], QR_SCP['Port'], ae_title=QR_SCP['AETitle'])
    if assoc.is_established:
        LOGGER.info(f"Association established with {QR_SCP['AETitle']}")
        for uid in uids:
            ds = Dataset()
            ds.QueryRetrieveLevel = 'IMAGE'
            ds.SOPInstanceUID = uid
            responses = assoc.send_c_move(ds, STORE_SCP['AETitle'], StudyRootQueryRetrieveInformationModelMove)
            for _ in responses: pass
        assoc.release()
        LOGGER.info('Association released')
    scp.shutdown()
    LOGGER.info('SCP shutdown')
    return None

def get_plan_labels(report: pd.DataFrame):
    plan_uids = report['ReferencedSOPInstanceUID']
    study_uids = report['StudyInstanceUID']
    plan_labels = []


    ae = AE(SCU['AETitle'])
    ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)
    assoc = ae.associate(QR_SCP['IP'], QR_SCP['Port'], ae_title=QR_SCP['AETitle'])
    if assoc.is_established:
        LOGGER.info(f"Association established with {QR_SCP['AETitle']}")

        for plan_uid, study_uid in zip(plan_uids, study_uids):
            series_ds = Dataset()
            series_ds.QueryRetrieveLevel = 'SERIES'
            series_ds.Modality = 'RTPLAN'
            series_ds.StudyInstanceUID = study_uid
            series_ds.SeriesInstanceUID = ''
            responses = assoc.send_c_find(series_ds, StudyRootQueryRetrieveInformationModelFind)
            series_uids = [ds.SeriesInstanceUID for _, ds in responses if ds]

            # Match plan_label to correct series/plan_uid
            for series_uid in series_uids:
                image_ds = Dataset()
                image_ds.QueryRetrieveLevel = 'IMAGE'
                image_ds.SeriesInstanceUID = series_uid
                image_ds.RTPlanLabel = ''
                responses = assoc.send_c_find(image_ds, StudyRootQueryRetrieveInformationModelFind)
                plan_label = [ds.RTPlanLabel for _, ds in responses if ds and ds.SOPInstanceUID == plan_uid]
                # for _, identifier in responses:
                #     if identifier:
                #         print(identifier.SOPInstanceUID, plan_uid)
                #     if identifier and identifier.SOPInstanceUID == plan_uid:
                #         plan_label = identifier.RTPlanLabel
                #         break
                if plan_label:
                    plan_labels.append(plan_label[0])
                    break
            else:
                plan_labels.append('N/A')

        assoc.release()
        LOGGER.info('Association released')
    else:
        LOGGER.error('Association rejected, aborted or never connected')
    return plan_labels
            
if __name__ == '__main__':
    # record_uids = find_uids(study_date='20200101-20240530', treatment_date='20200101-20251231')
    study_start_date = datetime(2024, 3, 1)
    treatment_start_date = datetime(2024, 5, 1)
    end_date = datetime(2024, 5, 7)
    study_range = '-'.join([study_start_date.strftime('%Y%m%d'), end_date.strftime('%Y%m%d')])
    treatment_range = '-'.join([treatment_start_date.strftime('%Y%m%d'), end_date.strftime('%Y%m%d')])

    record_uids = find_uids(study_date=study_range, treatment_date=treatment_range)
    generate_report(record_uids)
    report = pd.DataFrame(RECORDS_REPORT)
    report = report.drop_duplicates(subset='ReferencedSOPInstanceUID')
    report.to_csv(r'', index=False)
    report = report[report['CurrentFractionNumber'] < report['NumberOfFractionsPlanned']]
    report = report.sort_values(by=['PatientName'], key=lambda name: name.str.replace('^', ''))
    plan_labels = get_plan_labels(report)
    report['RTPlanLabel'] = plan_labels
    report.to_csv(r'', index=False)
    print(f'Number of Report Entries: {len(report)}')
