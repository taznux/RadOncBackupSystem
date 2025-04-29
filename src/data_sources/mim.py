from . import DataSource
from pydicom.dataset import Dataset
from pynetdicom import AE, evt, StoragePresentationContexts
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind, StudyRootQueryRetrieveInformationModelGet

class MIM(DataSource):
    def query(self, query_dataset: Dataset, qr_scp: dict):
        ae = AE()
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)
        assoc = ae.associate(qr_scp['IP'], qr_scp['Port'], ae_title=qr_scp['AETitle'])
        if assoc.is_established:
            responses = assoc.send_c_find(query_dataset, StudyRootQueryRetrieveInformationModelFind)
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
