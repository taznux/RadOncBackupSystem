import os
import sys
import time
import re
import atexit
import datetime
import random
import pytz
import pandas as pd

from pathlib import Path
from io import StringIO
from urllib.parse import quote

import sqlalchemy
from sqlalchemy import create_engine

import pydicom
from pydicom import dcmread, dcmwrite
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import generate_uid, UID, RTBeamsTreatmentRecordStorage, ImplicitVRLittleEndian

# Database connection crendentials are savced in ./.env
from dotenv import load_dotenv
load_dotenv()

## Set up MosaiqDB and load queries
from app.db.mssql_db import connect_mssql
from app.db.queries import site_statement, record_statement, uid_statement


## Create timezones
utc_timezone = pytz.timezone("UTC")
# Create an Eastern Timezone (EST) object
est_timezone = pytz.timezone("America/New_York")


## Porcess cleanup
def reset_db_connections():
    if hasattr(connect_mssql, "engine"):
        del connect_mssql.engine


## Function to pull schedueld prescription list for each date from Mosaiq
def get_site_data(date=None):
    
    if date is None:
        date = datetime.datetime.now(tz=est_timezone).date()
        date = date.strftime("%Y-%m-%d")

    print (f"#Retrieve data from Mosaiq on the date: {date}")

    query = site_statement.format(date)
    #print(f"#Executing site statement query: {query}")
    df_site = (
        pd.read_sql(query, connect_mssql()).reset_index(drop=True)
    )
    if len(df_site) == 0:  # No appointment
        print (f"#No appointment for the date: {date}")
        return df_site

    df_site = df_site.map(lambda x: x.strip() if type(x) == str else x)
    df_site.to_csv('site.csv')
    print(f"#Retrieve {len(df_site)} SITE SET records from Mosaiq")

    return df_site

## Function to pull treatment records of each prescription from Mosaiq
def get_record_data(df_site):

    if df_site.empty:  # No appointment
        return df_site

    # Reterive Treatment records from Mosaiq
    for idx, site_id in enumerate(df_site.iloc[:, 0]):
        print (f"## {idx} SITE_SET_ID: {site_id}")

        query = record_statement.format(site_id)
        df_record = (
            pd.read_sql(query, connect_mssql()).reset_index(drop=True)
        )

        if idx == 0:
            df_record.to_csv('record.txt', sep='\t', index=False, mode ='a', header=True)
        else:
            df_record.to_csv('record.txt', sep='\t', index=False, mode ='a', header=False)

        print(f"#Retrieve {len(df_record)} records from Mosaiq")

    return df_record

## Function to geenrate rtrecord DICOM file
## We will generate one RTRecord file for each valid RTPlan
def generate_rt_record(df_site):

    if df_site.empty:  # No appointment
        return df_site

    for idx_site, site_id in enumerate(df_site.iloc[:, 0]):
        print (f"#{idx_site} SITE_SET_ID: {site_id}")

        series_site = df_site.iloc[idx_site]

        # Retieve STUDY/SERIES/PLAN information from MosaiqDB
        # There is possiblity that there are multiple RT-Plans assocaited with one SITE_SET_ID
        query = uid_statement.format(site_id)
        df_uid = (   
            pd.read_sql(query, connect_mssql()).reset_index(drop=True)
        )        
        print(f"..Number of rtplans: "+ str(len(df_uid)))
        if df_uid.empty:  # No plan records
            continue

        for idx_plan, series_uid in df_uid.iterrows():
            write_rt_recrod_file(series_site, series_uid, idx_plan)


## Fucntion to write RTRecord file in DICOM format
def write_rt_recrod_file(series_site, series_uid, idx_plan):

    site_id = series_site["SIT_SET_ID"]
    mrd_id = series_site["MRN"]
    pat_id = series_site["Pat_ID1"]
    site_name = series_site["Site_Name"]
    setup_note = series_site["SetupNote"]
    activity = series_site["Activity"]

    rt_plan_uid = series_uid["RTPlanInstanceUID"]
    rt_plan_label = series_uid["Label"]
    plan_num = idx_plan + 1

    print(f"....pat_id: {pat_id}; mrd_id: {mrd_id}; plan_number: {plan_num}")

    # File meta
    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = RTBeamsTreatmentRecordStorage
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.ImplementationClassUID = "1.2.826.0.1.3680043.10.1.99999" # for research/test use
    file_meta.ImplementationVersionName = 'CUSTOMIZED_1.0' # customized script
    file_meta.TransferSyntaxUID = ImplicitVRLittleEndian

    # Dataset
    filename = file_meta.MediaStorageSOPInstanceUID    
    path = Path("rtrecords") / f"{filename}.dcm"
    ds = FileDataset(path, {}, file_meta=file_meta, preamble=b"\0" * 128)
    ds.is_little_endian = True
    ds.is_implicit_VR = True

    # Set creation date/time
    now = datetime.datetime.now()
    ds.InstanceCreationDate = now.strftime('%Y%m%d')
    ds.InstanceCreationTime = now.strftime('%H%M%S')
    ds.SOPClassUID = RTBeamsTreatmentRecordStorage
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    ds.Modality = "RTRECORD"
    
    # Retrieve Treatment records from MosaiqDB
    query = record_statement.format(site_id, rt_plan_uid)

    df_record = (
        pd.read_sql(query, connect_mssql()).reset_index(drop=True)
    )
    if df_record.empty:  # No treatment records
        return
    beams = df_record["FLD_ID"].unique()


    # Patient/Study/Series info
    ds.PatientName = df_record.loc[0,"Last_Name"] + "^" + df_record.loc[0,"First_Name"]
    ds.PatientID = mrd_id
    ds.PatientBirthDate = df_record.loc[0,"Birth_DtTm"]
    ds.StudyDate = series_uid["Create_DtTm"].strftime('%Y%m%d')
    ds.StudyTime = series_uid["Create_DtTm"].strftime('%H%M%S')
    ds.StudyDescription = series_uid["StudyDescription"]
    ds.SeriesDescription = series_uid["SeriesDescription"]
    ds.StudyInstanceUID = series_uid["StudyInstanceUID"]
    ds.SeriesInstanceUID = series_uid["SeriesInstanceUID"]
    ds.StudyID = series_uid["StudyID"]
    ds.SeriesNumber = series_uid["SeriesNumber"]
    ds.InstanceNumber = 1       # place hoder for the dicom file

    # Treatment info
    ds.TreatmentDate = df_record.loc[0, "Last_Tx_DtTm"].strftime('%Y%m%d')
    ds.TreatmentTime = df_record.loc[0, "Last_Tx_DtTm"].strftime('%H%M%S')
    ds.NumberOfFractionsPlanned = df_record.loc[0,"Fractions"]
    tmp_index = int(df_record.loc[0,"IndexReference_Enum"])
    ds.PrimaryDosimeterUnit = ["", "MU", "Gantry"][tmp_index] if tmp_index in range(3) else ""
    ds.NumberOfBeams = len(df_record["FLD_ID"].unique())

    # Treatment Beam info
    ds.TreatmentSessionBeamSequence = Sequence()
    for beam in beams:
        df_beam = df_record[df_record["FLD_ID"] == beam].reset_index(drop=True)

        beam_record = Dataset()
        beam_record.CurrentFractionNumber = df_beam.loc[0,'Fractions_Tx']            
        tmp_index = int(df_beam.loc[0,'Termination_Status_Enum'])
        beam_record.TreatmentTerminationStatus = ["UNKNOWN", "NORMAL", "OPERATOR","MACHINE"][tmp_index] if tmp_index in range(4) else "UNKNOWN"
        beam_record.TreatmentTerminationCode = df_beam.loc[0,'TerminationCode']
        tmp_index = int(df_beam.loc[0,'Termination_Verify_Status_Enum'])
        beam_record.TreatmentVerificationStatus = ["", "VERIFIED", "VERIFIED_OVR","NOT_VERIFIED"][tmp_index] if tmp_index in range(4) else ""

        # Control Points from Mosaiq delivery (On;y the first and the last control points)
        cp_seq = []

        # First Control Points
        i = 0
        cp_ds = Dataset()
        cp_ds.TreatmentControlPointDate = df_beam.loc[i, "PointTime"].strftime('%Y%m%d')
        cp_ds.TreatmentControlPointTime = df_beam.loc[i, "PointTime"].strftime('%H%M%S')
        cp_ds.DeliveredMeterset = df_beam.loc[i, "Meterset"]
        tmp_index = int(df_beam.loc[i, "Energy_Unit_Enum"])
        cp_ds.NominalBeamEnergyUnit = ["KV", "MV", "MEV"][tmp_index] if tmp_index in range(3) else ""
        cp_ds.NominalBeamEnergy = df_beam.loc[i, "Energy"]
        cp_ds.GantryAngle = df_beam.loc[i, "Gantry_Ang"]
        tmp_index = int(df_beam.loc[i, "Gantry_Dir_Enum"])
        cp_ds.GantryRotationDirection = ["", "CW", "CC","NONE"][tmp_index] if tmp_index in range(4) else ""
        cp_ds.BeamLimitingDeviceAngle = df_beam.loc[i, "Coll_Ang"]
        tmp_index = int(df_beam.loc[i, "Coll_Dir_Enum"])
        cp_ds.BeamLimitingDeviceRotationDirection = ["", "CW", "CC","NONE"][tmp_index] if tmp_index in range(4) else ""
        cp_ds.PatientSupportAngle = df_beam.loc[i, "Couch_Ang"]
        tmp_index = int(df_beam.loc[i, "Couch_Roll_Dir_Enum"])
        cp_ds.PatientSupportRotationDirection = ["", "CW", "CC","NONE"][tmp_index] if tmp_index in range(4) else ""
        cp_ds.TableTopEccentricAxisDistance = df_beam.loc[i, "Couch_Top_Axis_Distance"]
        cp_ds.TableTopEccentricAngle = df_beam.loc[i, "Couch_Top"]
        tmp_index = int(df_beam.loc[i, "Couch_Top_Dir_Enum"])
        cp_ds.TableTopEccentricRotationDirection = ["", "CW", "CC","NONE"][tmp_index] if tmp_index in range(4) else ""
        cp_ds.TableTopVerticalPosition = df_beam.loc[i, "Couch_Vrt"]
        cp_ds.TableTopLongitudinalPosition = df_beam.loc[i, "Couch_Lng"]
        cp_ds.TableTopLateralPosition = df_beam.loc[i, "Couch_Lat"]
        cp_ds.ReferencedControlPointIndex = i
        cp_seq.append(cp_ds)

        # Last Control Point
        n_controls = df_beam.loc[0, "ControlPoints"]
        if int(n_controls) > 1:
            i = int(n_controls) - 1
            cp_ds = Dataset()
            cp_ds.TreatmentControlPointDate = df_beam.loc[i, "PointTime"].strftime('%Y%m%d')
            cp_ds.TreatmentControlPointTime = df_beam.loc[i, "PointTime"].strftime('%H%M%S')
            cp_ds.DeliveredMeterset = df_beam.loc[i, "Meterset"]
            cp_ds.GantryAngle = df_beam.loc[i, "Gantry_Ang"]
            cp_ds.ReferencedControlPointIndex = i
            cp_seq.append(cp_ds)

        beam_record.ControlPointSequence = Sequence(cp_seq)

        # Beam information
        beam_record.SourceAxisDistance = df_beam.loc[0, "Sad"]
        beam_record.BeamName = df_beam.loc[0, "Field_Name"]
        tmp_index = int(df_beam.loc[0, "Beam_Type_Flag"])
        beam_record.BeamType = ["", "STATIC", "DYNAMIC"][tmp_index] if tmp_index in range(3) else "Invalid"
        beam_record.TreatmentDeliveryType = 'TREATMENT'
        beam_record.NumberOfControlPoints = n_controls
        beam_record.ReferencedBeamNumber = df_beam.loc[0, "OriginalBeamNumber"]

        ds.TreatmentSessionBeamSequence.append(beam_record)

    # Private creator and tags 
    creator = 'TJU RadOnc Customized Data'
    block = ds.private_block(0x3261, creator, create=True)
    block.add_new(0x01, 'LT', site_name) # Site Name
    block.add_new(0x02, 'LT', setup_note) # Setup Notes
    block.add_new(0x03, 'LT', activity) # Setup Notes
    block.add_new(0x04, 'LT', rt_plan_label) # RT_plan_label

    # One can use following code to retrive private tag info from dcm files
    '''
    import pydicom
    ds = pydicom.dcmread("path/filename.dcm")
    block = ds.private_block(0x3261, 'TJU RadOnc Customized Data')
    elem1 = block[(0x01)]
    elem2 = block[(0x02)]
    elem3 = block[(0x03)]
    elem4 = block[(0x04)]
    print(f"Site Name:  {elem1.value}")
    print(f"Setup Notes:  {elem2.value}")
    print(f"Activity:  {elem3.value}")
    print(f"RTPlan label:  {elem4.value}")
    '''

    # Treatment Machine info
    machine = Dataset()
    machine.InstitutionName = "Thomas Jefferson University"
    machine.InstitutionalDepartmentName = "Radiation Oncology"
    machine.TreatmentMachineName = df_record.loc[0, "Machine"][:16]
    ds.TreatmentMachineSequence = Sequence([machine])

    # Reference RT Plan
    ref_plan = Dataset()
    ref_plan.ReferencedSOPClassUID = "1.2.840.10008.5.1.4.1.1.481.5"
    ref_plan.ReferencedSOPInstanceUID = rt_plan_uid
    ds.ReferencedRTPlanSequence = Sequence([ref_plan])

    # Save RT Record file
    dcmwrite(path, ds)
    print(f"......Writing dataset to: {path}")

    # reopen the data just for checking
    #print(f"Load dataset from: {path} ...")
    #ds = dcmread(path)
    #print(ds)    


## Main workflow
def main():
    
    # Get Mosaiq data
    site_data = get_site_data()
    #site_data = site_data.iloc[:10,]
    #record_data = get_record_data(site_data)
    generate_rt_record(site_data)
    reset_db_connections()



if __name__ == "__main__":
    os.register_at_fork(before=reset_db_connections, after_in_child=reset_db_connections)
    main()

