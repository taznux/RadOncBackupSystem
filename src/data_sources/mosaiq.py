from . import DataSource
import pyodbc
from pydicom.dataset import Dataset
from pynetdicom import AE, evt, StoragePresentationContexts
from pynetdicom.sop_class import RTBeamsTreatmentRecordStorage

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

    def transfer(self, rt_record_data: dict, store_scp: dict):
        ds = Dataset()
        ds.PatientID = rt_record_data['PatientID']
        ds.PatientName = rt_record_data['PatientName']
        ds.PatientBirthDate = rt_record_data['PatientBirthDate']
        ds.PatientSex = rt_record_data['PatientSex']
        ds.PhysiciansOfRecord = rt_record_data['PhysiciansOfRecord']
        ds.StudyDescription = rt_record_data['StudyDescription']
        ds.TreatmentDate = rt_record_data['TreatmentDate']
        ds.NumberOfFractionsPlanned = rt_record_data['NumberOfFractionsPlanned']
        ds.CurrentFractionNumber = rt_record_data['CurrentFractionNumber']
        ds.TreatmentMachineName = rt_record_data['TreatmentMachineName']
        ds.ReferencedSOPInstanceUID = rt_record_data['ReferencedSOPInstanceUID']
        ds.StudyInstanceUID = rt_record_data['StudyInstanceUID']
        ds.SOPClassUID = RTBeamsTreatmentRecordStorage

        ae = AE()
        ae.requested_contexts = StoragePresentationContexts
        assoc = ae.associate(store_scp['IP'], store_scp['Port'], ae_title=store_scp['AETitle'])
        if assoc.is_established:
            status = assoc.send_c_store(ds)
            if status:
                print('C-STORE request status: 0x{0:04x}'.format(status.Status))
            else:
                print('Connection timed out, was aborted or received invalid response')
            assoc.release()
        else:
            print('Association rejected, aborted or never connected')
