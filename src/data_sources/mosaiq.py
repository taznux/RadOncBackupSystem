from . import DataSource
import pyodbc
from pydicom.dataset import Dataset, FileMetaDataset
from pynetdicom import AE, evt, StoragePresentationContexts
from pynetdicom.sop_class import RTBeamsTreatmentRecordStorage
from pydicom.uid import generate_uid, ExplicitVRLittleEndian  # Changed from ImplicitVRLittleEndian

class Mosaiq(DataSource):
    def query(self, sql_query: str, db_config: dict):
        conn = pyodbc.connect(
            f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={db_config['server']};DATABASE={db_config['database']};UID={db_config['username']};PWD={db_config['password']}"
        )
        cursor = conn.cursor()
        cursor.execute(sql_query)
        rows = cursor.fetchall()
        conn.close()
        return rows

    def transfer(self, rt_record: Dataset, store_scp: dict): # Changed rt_record_data: dict to rt_record: Dataset
        # Ensure the input is a pydicom Dataset
        if not isinstance(rt_record, Dataset):
            raise TypeError("rt_record must be a pydicom Dataset object")

        # Assume rt_record already contains necessary patient/study/series information
        # and is largely populated. We will prepare it for C-STORE.

        # Set/Overwrite SOP Class and Instance UIDs for this specific record instance
        rt_record.SOPClassUID = RTBeamsTreatmentRecordStorage
        # Generate a new SOPInstanceUID if not present or to ensure uniqueness for this stored instance
        if not hasattr(rt_record, 'SOPInstanceUID') or not rt_record.SOPInstanceUID:
            rt_record.SOPInstanceUID = generate_uid()
        
        # Create file_meta explicitly if it doesn't exist
        if not hasattr(rt_record, 'file_meta') or rt_record.file_meta is None:
            rt_record.file_meta = FileMetaDataset()
        
        # Populate File Meta Information
        rt_record.file_meta.FileMetaInformationVersion = b'\x00\x01'
        rt_record.file_meta.MediaStorageSOPClassUID = rt_record.SOPClassUID
        rt_record.file_meta.MediaStorageSOPInstanceUID = rt_record.SOPInstanceUID
        rt_record.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian # Changed to Explicit VR Little Endian
        # Use a registered PYNETDICOM_IMPLEMENTATION_UID or generate a new one
        rt_record.file_meta.ImplementationClassUID = generate_uid(prefix='1.2.826.0.1.3680043.9.7156.1.') # Example OID prefix
        rt_record.file_meta.ImplementationVersionName = "PYNETDICOM_MOSAIQ_1.0"
        
        # FileMetaInformationGroupLength is typically set by pynetdicom/pydicom before sending/writing
        # So, no need to set rt_record.file_meta.FileMetaInformationGroupLength manually

        # Ensure dataset encoding matches the TransferSyntaxUID for pynetdicom
        rt_record.is_little_endian = True
        rt_record.is_implicit_VR = False # Explicit VR

        ae = AE()
        # Add presentation context for RT Beams Treatment Record Storage - Explicit VR Little Endian
        ae.add_requested_context(RTBeamsTreatmentRecordStorage, transfer_syntax=ExplicitVRLittleEndian)
        
        assoc = ae.associate(store_scp['IP'], store_scp['Port'], ae_title=store_scp['AETitle'])
        if assoc.is_established:
            status = assoc.send_c_store(rt_record) # Use rt_record directly
            if status:
                print('C-STORE request status: 0x{0:04x}'.format(status.Status))
            else:
                print('Connection timed out, was aborted or received invalid response')
            assoc.release()
        else:
            print('Association rejected, aborted or never connected')
