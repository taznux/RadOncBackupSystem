#!/usr/bin/env python
from pydicom.dataset import Dataset

from pynetdicom import AE, evt, AllStoragePresentationContexts
from pynetdicom.sop_class import PatientRootQueryRetrieveInformationModelFind, PatientRootQueryRetrieveInformationModelGet, StudyRootQueryRetrieveInformationModelFind, StudyRootQueryRetrieveInformationModelGet

import pandas as pd

def find(assoc, ds, query_model=PatientRootQueryRetrieveInformationModelFind):
    if assoc.is_established:
        # Send the C-FIND request
        responses = assoc.send_c_find(ds, query_model)
        identifiers = []
        for (status, identifier) in responses:
            if status:
                print('C-FIND query status: 0x{0:04X}'.format(status.Status))
                if status.Status == 0xFF00:
                    identifiers.append(identifier)
            else:
                print('Connection timed out, was aborted or received invalid response')
        
        return identifiers
    else:
        print('Association rejected, aborted or never connected')

ae = AE()

ae.add_requested_context(PatientRootQueryRetrieveInformationModelFind)
ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)
ae.add_requested_context(PatientRootQueryRetrieveInformationModelGet)
ae.add_requested_context(StudyRootQueryRetrieveInformationModelGet)
#ae.add_requested_context(RTPlanStorage)

# Create our Identifier (query) dataset
ds = Dataset()
ds.PatientID = '400743958'
#ds.QueryRetrieveLevel = 'PATIENT'
ds.QueryRetrieveLevel = 'STUDY'

ds.PatientName= ''
ds.Modality = ''
ds.SOPInstanceUID = ''  # Request to return it
ds.StudyInstanceUID = ''
ds.SeriesInstanceUID = ''

# Associate with the peer AE at IP 127.0.0.1 and port 4242
#assoc = ae.associate("127.0.0.1", 4242)
assoc = ae.associate("10.187.138.252", 4242, ae_title="GPU_SERVER")
#assoc = ae.associate("10.185.129.187", 8177, ae_title="MIMDCMQUERY")

identifiers = find(assoc, ds, query_model=StudyRootQueryRetrieveInformationModelFind)
PID = identifiers[0].PatientID
print("Sucessful Patient ID: "+PID)

print(str(len(identifiers)) + " items by C-FIND")
for i in range(len(identifiers)):
    print("C-FIND Return #"+ str(i+1) + ":")
    print(identifiers[i])



# Release the association
assoc.release()

# comand-line 
# python -m pynetdicom findscu 10.185.129.187 8177 -k QueryRetrieveLevel=STUDY -k PatientID=00495451 -k PatientName -k StudyInstanceUID -aec MIMDCMQUERY

print("------\n")


### C_GET command

# Handler to save received images
def handle_store(event):
    ds = event.dataset
    ds.file_meta = event.file_meta
    print(f"Receiving image: {ds.SOPInstanceUID}")
    os.makedirs("received", exist_ok=True)
    ds.save_as(f"received/{ds.SOPInstanceUID}.dcm", write_like_original=False)
    return 0x0000

handlers = [(evt.EVT_C_STORE, handle_store)]

# Create AE and support storage SCP for receiving images
ae = AE(ae_title="GETSCU")
for context in AllStoragePresentationContexts:
    ae.add_supported_context(context.abstract_syntax)


# Add requested context for C-GET
ae.add_requested_context(StudyRootQueryRetrieveInformationModelGet)

# Create C-GET dataset
ds = Dataset()
ds.QueryRetrieveLevel = 'SERIES'
ds.PatientID = '400743958' 
ds.StudyInstanceUID = '1.3.12.2.1107.5.1.4.95363.30000024041812350281900000010'
ds.SeriesInstanceUID = '2.16.840.1.114362.1.12081536.25694371295.671249245.813.1'

#print(ds)
# Associate with server
#assoc = ae.associate("10.185.129.187", 8177, ae_title="MIMDCMQUERY", evt_handlers=handlers)
assoc = ae.associate("10.187.138.252", 4242, ae_title="GPU_SERVER", evt_handlers=handlers)
identifiers = []
if assoc.is_established:
    print("Association established, sending C-GET request...")
    responses = assoc.send_c_get(ds, StudyRootQueryRetrieveInformationModelGet)
    for status, identifier in responses:
        if status:
            print('C-GET response status: 0x{0:04X}'.format(status.Status))
            print(identifier)
        else:
            print('Connection timed out, was aborted or received invalid response')
else:
    print('Association rejected, aborted or never connected')

assoc.release()





