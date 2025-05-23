from . import DataSource
from pydicom.dataset import Dataset
from pynetdicom import AE, evt, StoragePresentationContexts
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind, StudyRootQueryRetrieveInformationModelGet

class MIM(DataSource):
    def query(self, query_dataset: Dataset, qr_scp: dict):
        ae = AE()
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)
        uids = set()  # Initialize uids to prevent UnboundLocalError
        assoc = ae.associate(qr_scp['IP'], qr_scp['Port'], ae_title=qr_scp['AETitle'])
        if assoc.is_established:
            try:
                responses = assoc.send_c_find(query_dataset, StudyRootQueryRetrieveInformationModelFind)
                for (status, identifier) in responses:
                    if status and status.Status == 0xFF00:  # Pending status, continue
                        if identifier:
                            uids.add(identifier.SOPInstanceUID)
                    elif status and status.Status == 0x0000:  # Success status
                        if identifier:
                            uids.add(identifier.SOPInstanceUID)
                    else:  # Failure or unknown status
                        print('C-FIND query failed or connection issue.')
                        if status:
                            print(f'C-FIND query status: 0x{status.Status:04X}')
                        else:
                            print('No status returned.')
                        # Potentially break or handle error more robustly
            finally:
                assoc.release()
        else:
            print('Association rejected, aborted or never connected')
        return uids

    def transfer(self, get_dataset: Dataset, qr_scp: dict, store_scp: dict, handle_store):
        ae = AE()
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelGet)
        ae.supported_contexts = StoragePresentationContexts
        handlers = [(evt.EVT_C_STORE, handle_store)]
        scp = ae.start_server((store_scp['IP'], store_scp['Port']), block=False, evt_handlers=handlers)
        assoc = ae.associate(qr_scp['IP'], qr_scp['Port'], ae_title=qr_scp['AETitle'])
        if assoc.is_established:
            responses = assoc.send_c_get(get_dataset, StudyRootQueryRetrieveInformationModelGet)
            for _ in responses: pass
            assoc.release()
        scp.shutdown()
