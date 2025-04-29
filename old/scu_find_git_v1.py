import tomllib
from pydicom.dataset import Dataset

from pynetdicom import AE, debug_logger
from pynetdicom.sop_class import PatientRootQueryRetrieveInformationModelFind, RTBeamsTreatmentRecordStorage

def find(query_dataset: Dataset, debug_to_console=False):
    with open ('config.toml', 'rb') as f:
        sockets = tomllib.load(f)
    SCU = sockets['local']
    QR_SCP = sockets['rvs']

    if debug_to_console:
        debug_logger()

    ae = AE(SCU['AETitle'])
    ae.add_requested_context(PatientRootQueryRetrieveInformationModelFind)
    assoc = ae.associate(QR_SCP['IP'], QR_SCP['Port'], ae_title=QR_SCP['AETitle'])

    if assoc.is_established:
        responses = assoc.send_c_find(query_dataset, PatientRootQueryRetrieveInformationModelFind)
        uids = set()
        for (status, identifier) in responses:
            if not status:
                print('Connection timed out, was aborted or received invalid response')
                print('C-FIND query status: 0x{0:04X}'.format(status.Status))
            elif identifier:
                uids.add(identifier.SOPInstanceUID)
        assoc.release()
    else:
        print('Association rejected, aborted or never connected')
    return uids


if __name__ == '__main__':
    ds = Dataset()
    ds.QueryRetrieveLevel = 'IMAGE'
    ### PATIENT ###
    ds.PatientID = ''
    ds.PatientName = ''
    ds.TreatmentDate = ''
    ds.TreatmentTime = ''
    ### STUDY ###
    ds.StudyID = ''
    ds.StudyInstanceUID = ''
    # ds.ModalitiesInStudy = 'CT'
    ### SERIES ###
    ds.SeriesInstanceUID = ''
    ds.Modality = 'RTRECORD'
    ### IMAGE ###
    ds.SOPInstanceUID = ''
    ds.SOPClassUID = RTBeamsTreatmentRecordStorage 
    ### RERFERENCED CLASS ###
    ds.ReferencedSOPClassUID = ''
    ds.ReferencedSOPInstanceUID = ''
    ### TEST ###
    ds.PatientBirthDate = ''
    ds.PatientSex = ''
    ds.PhysiciansOfRecord = ''
    ds.StudyDescription = ''
    ds.NumberOfFractionsPlanned = ''
    ds.CurrentFractionNumber = ''
    ds.TreatmentMachineName = ''
    ds.RTPlanLabel = ''

    find(ds, debug_to_console=True)

import tomllib
from pydicom.dataset import Dataset

from pynetdicom import AE, debug_logger
from pynetdicom.sop_class import PatientRootQueryRetrieveInformationModelFind, RTBeamsTreatmentRecordStorage

def find(query_dataset: Dataset, debug_to_console=False):
    with open ('config.toml', 'rb') as f:
        sockets = tomllib.load(f)
    SCU = sockets['local']
    QR_SCP = sockets['rvs']

    if debug_to_console:
        debug_logger()

    ae = AE(SCU['AETitle'])
    ae.add_requested_context(PatientRootQueryRetrieveInformationModelFind)
    assoc = ae.associate(QR_SCP['IP'], QR_SCP['Port'], ae_title=QR_SCP['AETitle'])

    if assoc.is_established:
        responses = assoc.send_c_find(query_dataset, PatientRootQueryRetrieveInformationModelFind)
        uids = set()
        for (status, identifier) in responses:
            if not status:
                print('Connection timed out, was aborted or received invalid response')
                print('C-FIND query status: 0x{0:04X}'.format(status.Status))
            elif identifier:
                uids.add(identifier.SOPInstanceUID)
        assoc.release()
    else:
        print('Association rejected, aborted or never connected')
    return uids


if __name__ == '__main__':
    ds = Dataset()
    ds.QueryRetrieveLevel = 'IMAGE'
    ### PATIENT ###
    ds.PatientID = ''
    ds.PatientName = ''
    ds.TreatmentDate = ''
    ds.TreatmentTime = ''
    ### STUDY ###
    ds.StudyID = ''
    ds.StudyInstanceUID = ''
    # ds.ModalitiesInStudy = 'CT'
    ### SERIES ###
    ds.SeriesInstanceUID = ''
    ds.Modality = 'RTRECORD'
    ### IMAGE ###
    ds.SOPInstanceUID = ''
    ds.SOPClassUID = RTBeamsTreatmentRecordStorage 
    ### RERFERENCED CLASS ###
    ds.ReferencedSOPClassUID = ''
    ds.ReferencedSOPInstanceUID = ''
    ### TEST ###
    ds.PatientBirthDate = ''
    ds.PatientSex = ''
    ds.PhysiciansOfRecord = ''
    ds.StudyDescription = ''
    ds.NumberOfFractionsPlanned = ''
    ds.CurrentFractionNumber = ''
    ds.TreatmentMachineName = ''
    ds.RTPlanLabel = ''

    find(ds, debug_to_console=True)

