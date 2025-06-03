from . import DataSource
import pyodbc
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import generate_uid, ExplicitVRLittleEndian, UID
import logging
import struct
from typing import List, Dict, Optional, Any, Tuple, Iterator

import pandas as pd
from pathlib import Path
import datetime
import pytz

logger = logging.getLogger(__name__)


class MosaiqQueryError(Exception):
    """Custom exception for errors during Mosaiq database queries."""
    pass


class Mosaiq(DataSource):
    """
    Represents the Mosaiq data source system.

    This class provides methods to query data directly from the Mosaiq database,
    generate DICOM RT Record objects from this data, and to transfer these objects
    to a DICOM C-STORE SCP.
    """

    DEFAULT_ODBC_DRIVER = "ODBC Driver 17 for SQL Server"
    _TREATMENT_SUMMARY_COLUMNS = [
        "PatientName", "PatientMRN", "StartDate", "EndDate",
        "TotalDose", "NumberOfFractions", "TargetVolume",
    ]

    # --- Static Members for RT Record Generation ---
    _UTC_TZ = pytz.timezone("UTC")
    _EST_TZ = pytz.timezone("America/New_York")

    _SITE_STATEMENT_TEMPLATE = """
    SELECT DISTINCT
        s.SIT_SET_ID, sch.Sch_Id, sch.Sch_Set_Id, sch.Edit_DtTm AS Timestamp, sch.App_DtTm,
        id.IDA AS MRN, p.Pat_ID1, s.Site_Name, su.Setup_Note AS SetupNote, cpt.Short_Desc AS Activity
    FROM Schedule sch
    INNER JOIN Site s ON s.Pat_ID1 = sch.Pat_ID1
    INNER JOIN Patient p ON sch.Pat_ID1 = p.Pat_ID1
    INNER JOIN Ident id ON p.Pat_ID1 = id.Pat_Id1
    INNER JOIN Staff st2 ON sch.Location = st2.Staff_ID
    INNER JOIN CPT cpt ON sch.Activity = cpt.Hsp_Code
    INNER JOIN SiteSetup su ON s.SIT_SET_ID = su.Sit_Set_ID
    WHERE
        s.Version = 0 AND su.Version = 0 AND s.Technique != 'HDR' AND sch.Version = 0 AND
        sch.App_DtTm >= '{0} 05:00:00' AND sch.App_DtTm < '{0} 20:00:00' AND
        CONVERT(DATE, s.Edit_DtTm) > DATEADD(DAY, -90, sch.App_DtTm) AND
        sch.Location IN (SELECT Staff_ID FROM Staff WHERE Machine_Type = 1 or Machine_Type = 2)
    ORDER BY sch.App_DtTm, Timestamp
    """
    _SITE_COLUMNS = [
        "SIT_SET_ID", "Sch_Id", "Sch_Set_Id", "Timestamp", "App_DtTm", "MRN",
        "Pat_ID1", "Site_Name", "SetupNote", "Activity"
    ]

    _UID_STATEMENT_TEMPLATE = """
    SELECT DISTINCT
        DCM.SOPInstanceUID AS RTPlanInstanceUID, DCM1.StudyInstanceUID, DCM2.SeriesInstanceUID,
        RtPlan.Label, RtPlan.Pat_ID1, DCM1.StudyID, DCM1.StudyDescription,
        DCM1.Study_DtTm, DCM2.SeriesNumber, DCM2.SeriesDescription, DCM2.Series_DtTm, -- Added Series_DtTm
        FLD.MachineCharID AS MachineID
    FROM DCMStudy AS DCM1
    INNER JOIN DCMSeries DCM2 ON DCM1.DCMStudy_ID = DCM2.DCMStudy_ID
    INNER JOIN DCMInstance DCM ON DCM.DCMSeries_ID = DCM2.DCMSeries_ID
    INNER JOIN RtPlan ON RtPlan.DCMInstance_ID = DCM.DCMInstance_ID
    INNER JOIN TxField FLD ON FLD.OriginalPlanUID = DCM.SOPInstanceUID
    WHERE FLD.SIT_Set_ID = '{0}' AND FLD.Version = '0' AND FLD.Cgray > 0
    """
    _UID_COLUMNS = [
        "RTPlanInstanceUID", "StudyInstanceUID", "SeriesInstanceUID", "Label", "Pat_ID1",
        "StudyID", "StudyDescription", "Study_DtTm", "SeriesNumber", "SeriesDescription", "Series_DtTm", "MachineID" # Updated
    ]

    _RECORD_STATEMENT_TEMPLATE = """
    SELECT DISTINCT
        ID.IDA, SIT.SIT_SET_ID, FLD.OriginalPlanUID, FLD.OriginalBeamName, FLD.OriginalBeamNumber,
        FLD.Last_Tx_DtTm, FLD.FLD_ID, ID.Pat_Id1, Pa.Last_Name, Pa.First_Name, Pa.MIddle_Name, Pa.Suffix, Pa.Sex AS PatientSex, -- Added Middle_Name, Suffix
        FLD.Fractions_Tx, SIT.Fractions, TFP.Energy, TFP.Energy_Unit_Enum, FLD.Meterset,
        FLD.Cgray, FLD.IndexReference_Enum, FLD.ControlPoints, TFP.Point, TFP.Gantry_Ang,
        TFP.Gantry_Dir_Enum, TFP.Create_DtTm AS PointTime, TFP.Coll_Ang, TFP.Coll_Dir_Enum,
        TFP.Couch_Ang, TFP.Couch_Roll_Dir_Enum, TFP.Couch_Top_Axis_Distance, TFP.Couch_Top,
        TFP.Couch_Top_Dir_Enum, TFP.Couch_Vrt, TFP.Couch_Lng, TFP.Couch_Lat,
        DHS.TerminationCode, DHS.Termination_Status_Enum, DHS.Termination_Verify_Status_Enum,
        DHS.Dose_Addtl_Projected, FLD.Sad,
        MAC.Source_Name AS MachineName, -- Use Source_Name from Machine table
        MAC.Source_Model AS MachineManufacturersModelName, -- Use Source_Model from Machine table
        -- Assuming DeviceSerialNumber can be derived or is a custom field in Staff or Machine
        -- For now, keep as placeholder, or query from Staff.Clinic_ID or a custom Ext_ID link
        'UNKNOWN_SN' AS DeviceSerialNumber, -- Placeholder for Device Serial Number
        CFG.Inst_Name AS InstitutionalDepartmentName, -- Use Inst_Name from Config
        MAC.Source_Model AS Machine_ManufacturersModelName_Seq, -- Renamed for clarity in sequence
        'UNKNOWN_MACHINE_SN_SEQ' AS Machine_DeviceSerialNumber_Seq, -- Placeholder for sequence SN
        FLD.Beam_Type_Flag, FLD.Modality_Enum, FLD.Type_Enum, FLD.Field_Name, FLD.Field_Label,
        FLD.Mlc, -- Added MLC flag
        FLD.Wdg_Appl, FLD.Comp_Fda, FLD.Bolus, FLD.Block, -- Added accessory types
        TFP.A_Leaf_Set, TFP.B_Leaf_Set, -- Added binary leaf set data
        'STANDARD' AS FluenceMode, -- This should be dynamically determined based on FLD.Type_Enum for IMRT/VMAT
        CAST(DHS.DeliveredTreatmentTimeBeam AS VARCHAR) AS DeliveredTreatmentTimeBeam, -- Cast to varchar
        '1.0' AS CalculatedDoseReferenceDoseValue, '1' AS ReferencedDoseReferenceNumber,
        '60' AS MLCX_NumberOfLeafJawPairs, '1' AS ASYMY_NumberOfLeafJawPairs, '1' AS ASYMX_NumberOfLeafJawPairs,
        -- RadiationType will be derived from FLD.Modality_Enum in Python
        '0' AS NumberOfWedges, '0' AS NumberOfCompensators, -- These should be counted from FLD.Wdg_Appl, FLD.Comp_Fda
        '0' AS NumberOfBoli, '0' AS NumberOfBlocks, -- These should be counted from FLD.Bolus, FLD.Block
        CAST(TFP.SpecifiedMeterset AS VARCHAR) AS SpecifiedMeterset_CP, -- Cast to varchar
        CAST(TFP.Meterset AS VARCHAR) AS DeliveredMeterset_CP, -- Cast to varchar
        CAST(TFP.Meterset_Rate AS VARCHAR) AS DoseRateDelivered_CP, -- Use TFP.Meterset_Rate for DoseRate
        CAST(TFP.Meterset_Rate AS VARCHAR) AS DoseRateSet_CP, -- Use TFP.Meterset_Rate for DoseRate
        -- These should be mapped from enum values directly in Python as per DICOM standard
        '' AS GantryRotationDirection_ARIA,
        '' AS BeamLimitingDeviceRotationDirection_ARIA,
        '' AS PatientSupportRotationDirection_ARIA,
        '' AS TableTopEccentricRotationDirection_ARIA,
        TFP.Point AS ControlPointIndex
    FROM Ident AS ID
    INNER JOIN Patient Pa ON ID.Pat_Id1 = Pa.Pat_ID1
    INNER JOIN TxField FLD ON ID.Pat_Id1 = FLD.Pat_ID1
    INNER JOIN TxFieldPoint TFP ON FLD.FLD_ID = TFP.FLD_ID
    INNER JOIN Dose_Hst DHS ON FLD.FLD_ID = DHS.FLD_ID
    INNER JOIN FLD_HST ON DHS.DHS_ID = FLD_HST.DHS_ID
    INNER JOIN Staff STF_Link ON FLD.Machine_ID_Staff_ID = STF_Link.Staff_ID -- Link to Staff for Machine ID
    INNER JOIN Machine MAC ON STF_Link.Staff_ID = MAC.MAC_ID -- Join to Machine table for details
    INNER JOIN Config CFG ON FLD.Inst_ID = CFG.CFG_ID -- Join to Config for Inst_Name
    INNER JOIN Site SIT ON FLD.SIT_Set_ID = SIT.SIT_SET_ID
    WHERE
        SIT.SIT_SET_ID = '{0}' AND FLD.OriginalPlanUID = '{1}' AND
        FLD.Fractions_Tx > 0 AND FLD.Fractions_Tx = DHS.Fractions_Tx
    ORDER BY FLD.OriginalPlanUID, FLD.OriginalBeamNumber, FLD.Fractions_Tx, TFP.Point
    """
    _RECORD_COLUMNS = [
        "IDA", "SIT_SET_ID", "OriginalPlanUID", "OriginalBeamName", "OriginalBeamNumber",
        "Last_Tx_DtTm", "FLD_ID", "Pat_Id1", "Last_Name", "First_Name", "MIddle_Name", "Suffix", "PatientSex",
        "Fractions_Tx", "Fractions", "Energy", "Energy_Unit_Enum", "Meterset",
        "Cgray", "IndexReference_Enum", "ControlPoints", "Point", "Gantry_Ang",
        "Gantry_Dir_Enum", "PointTime", "Coll_Ang", "Coll_Dir_Enum",
        "Couch_Ang", "Couch_Roll_Dir_Enum", "Couch_Top_Axis_Distance", "Couch_Top",
        "Couch_Top_Dir_Enum", "Couch_Vrt", "Couch_Lng", "Couch_Lat",
        "TerminationCode", "Termination_Status_Enum", "Termination_Verify_Status_Enum",
        "Dose_Addtl_Projected", "Sad", "MachineName", "MachineManufacturersModelName", "DeviceSerialNumber",
        "InstitutionalDepartmentName", "Machine_ManufacturersModelName_Seq", "Machine_DeviceSerialNumber_Seq",
        "Beam_Type_Flag", "Modality_Enum", "Type_Enum", "Field_Name", "Field_Label",
        "Mlc", "Wdg_Appl", "Comp_Fda", "Bolus", "Block", # Added accessory types
        "A_Leaf_Set", "B_Leaf_Set", # Added binary leaf set data
        "FluenceMode", "DeliveredTreatmentTimeBeam",
        "CalculatedDoseReferenceDoseValue", "ReferencedDoseReferenceNumber",
        "MLCX_NumberOfLeafJawPairs", "ASYMY_NumberOfLeafJawPairs", "ASYMX_NumberOfLeafJawPairs",
        "RadiationType", "NumberOfWedges", "NumberOfCompensators",
        "NumberOfBoli", "NumberOfBlocks", "SpecifiedMeterset_CP", "DeliveredMeterset_CP",
        "DoseRateDelivered_CP", "DoseRateSet_CP", "GantryRotationDirection_ARIA",
        "BeamLimitingDeviceRotationDirection_ARIA", "PatientSupportRotationDirection_ARIA",
        "TableTopEccentricRotationDirection_ARIA", "ControlPointIndex"
    ]
    # --- End Static Members ---

    # --- DICOM Mapping Helpers ---
    @staticmethod
    def _map_sex_to_dicom(sex_str: str) -> str:
        if sex_str:
            sex_str_upper = sex_str.strip().upper()
            if sex_str_upper == 'MALE':
                return 'M'
            elif sex_str_upper == 'FEMALE':
                return 'F'
        return 'O' # Other or Unknown

    @staticmethod
    def _map_rotation_direction_enum_to_dicom(enum_val: Optional[int]) -> str:
        # Mosaiq enum: 0 = Unspecified, 1 = CW, 2 = CC, 3 = NONE
        if enum_val == 1:
            return 'CW'
        elif enum_val == 2:
            return 'CC'
        elif enum_val == 3:
            return 'NONE'
        return '' # Empty string for unspecified/unknown in DICOM

    @staticmethod
    def _map_energy_unit_enum_to_dicom(enum_val: Optional[int]) -> str:
        # Mosaiq enum: 0 = Unspecified, 1 = KV, 2 = MV, 3 = MEV
        if enum_val == 1:
            return 'KV'
        elif enum_val == 2:
            return 'MV'
        elif enum_val == 3:
            return 'MEV'
        return ''

    @staticmethod
    def _map_termination_status_enum_to_dicom(enum_val: Optional[int]) -> str:
        # Mosaiq enum: 0 = Unknown, 1 = Normal, 2 = Operator, 3 = Machine
        if enum_val == 1:
            return 'NORMAL'
        elif enum_val == 2:
            return 'OPERATOR_INITIATED'
        elif enum_val == 3:
            return 'MACHINE_INITIATED'
        return 'UNKNOWN'

    @staticmethod
    def _map_radiation_type(modality_enum: Optional[int]) -> str:
        # Modality_Enum: 0 = Unspecified, 1 = X-rays, 2 = Electrons, 3 = Co-60, 6 = Protons, 9 = Ion
        if modality_enum == 1: # X-rays
            return 'PHOTON'
        elif modality_enum == 2: # Electrons
            return 'ELECTRON'
        elif modality_enum == 3: # Co-60
            return 'PHOTON' # Co-60 is a photon source
        elif modality_enum == 6 or modality_enum == 9: # Protons, Ion
            return 'PROTON' # Or 'ION' if you want more specific
        return 'UNKNOWN'

    @staticmethod
    def _map_beam_type_flag(beam_type_flag: Optional[int]) -> str:
        # Beam_Type_Flag: 0 = Unspecified, 1 = STATIC, 2 = DYNAMIC
        if beam_type_flag == 1:
            return 'STATIC'
        elif beam_type_flag == 2:
            return 'DYNAMIC'
        return 'UNKNOWN' # DICOM should accept 'UNKNOWN' for Type 1C

    @staticmethod
    def _map_fluence_mode(type_enum: Optional[int]) -> str:
        # Type_Enum: 1=Static, 13=VMAT, 14=DMLC, etc.
        if type_enum == 1: # Static
            return 'STANDARD'
        elif type_enum == 13: # VMAT
            return 'VMAT'
        elif type_enum == 14: # DMLC
            return 'IMRT'
        return 'STANDARD' # Default to standard if unknown/not special

    @staticmethod
    def _parse_binary_leaf_data(binary_data: Optional[bytes]) -> List[str]:
        """
        Parses binary MLC leaf position data into a list of strings.

        Assumption: The binary data is a sequence of 4-byte single-precision
        floating-point numbers in little-endian format. Each float represents
        a leaf position in millimeters. This assumption MUST be verified against
        Mosaiq's actual data specification for A_Leaf_Set/B_Leaf_Set.

        Args:
            binary_data: The binary data representing leaf positions.

        Returns:
            A list of strings, where each string is a float representation
            of a leaf position. Returns an empty list if input is None, empty,
            or if a parsing error occurs (e.g. invalid length).
        """
        if not binary_data:
            logger.debug("_parse_binary_leaf_data received None or empty data, returning empty list.")
            return []

        FLOAT_SIZE = 4  # Size of a single-precision float in bytes
        if len(binary_data) % FLOAT_SIZE != 0:
            logger.warning(
                f"Binary leaf data length ({len(binary_data)} bytes) is not a multiple of {FLOAT_SIZE}. "
                "Cannot parse. Returning empty list."
            )
            return []

        leaf_positions_str: List[str] = []
        try:
            num_leaves = len(binary_data) // FLOAT_SIZE
            for i in range(num_leaves):
                chunk = binary_data[i * FLOAT_SIZE : (i + 1) * FLOAT_SIZE]
                # Unpack as little-endian float ('<f')
                leaf_pos_float = struct.unpack('<f', chunk)[0]
                # Convert to string, potentially with formatting if needed, e.g., "{:.1f}".format(leaf_pos_float)
                leaf_positions_str.append(str(leaf_pos_float))
            logger.debug(f"Successfully parsed {num_leaves} leaf positions from binary data.")
        except struct.error as e:
            logger.error(f"Error unpacking binary leaf data: {e}. Data length: {len(binary_data)}", exc_info=True)
            return [] # Return empty list on struct unpacking errors
        except Exception as e: # Catch any other unexpected errors during parsing
            logger.error(f"Unexpected error parsing binary leaf data: {e}", exc_info=True)
            return []

        return leaf_positions_str

    # --- End DICOM Mapping Helpers ---

    def __init__(self, odbc_driver: Optional[str] = None):
        super().__init__()
        self.odbc_driver = odbc_driver if odbc_driver is not None else self.DEFAULT_ODBC_DRIVER
        logger.debug(f"Mosaiq DataSource initialized with ODBC driver: {self.odbc_driver}")

    @staticmethod
    def _rows_to_dataframe(rows: List[Tuple[Any, ...]], column_names: List[str]) -> pd.DataFrame:
        return pd.DataFrame(rows, columns=column_names)

    def query(
        self,
        sql_query: str,
        db_config: Dict[str, str],
        params: Optional[List[Any]] = None,
    ) -> List[Tuple[Any, ...]]:
        connection_string = (
            f"DRIVER={{{self.odbc_driver}}};SERVER={db_config['server']};"
            f"DATABASE={db_config['database']};UID={db_config['username']};"
            f"PWD={db_config['password']}"
        )
        logger.info(
            f"Connecting to Mosaiq database: {db_config['server']}/"
            f"{db_config['database']} using driver {self.odbc_driver}"
        )
        try:
            with pyodbc.connect(connection_string, autocommit=True) as conn:
                with conn.cursor() as cursor:
                    logger.debug(f"Executing SQL query (first 100 chars): {sql_query[:100]}... with params: {params}")
                    if params:
                        cursor.execute(sql_query, params)
                    else:
                        cursor.execute(sql_query)
                    rows = cursor.fetchall()
                    processed_rows = [tuple(row) for row in rows]
                    logger.info(f"SQL query executed successfully, fetched {len(processed_rows)} rows.")
                    return processed_rows
        except pyodbc.Error as ex:
            sqlstate = ex.args[0] if ex.args else "Unknown SQLSTATE"
            log_msg = f"Mosaiq database query failed. SQLSTATE: {sqlstate}. Error: {ex}"
            logger.error(log_msg, exc_info=True)
            raise MosaiqQueryError(f"Database query failed: {ex}") from ex

    def _get_site_data_df(self, db_config: Dict[str, str], date_str: str) -> pd.DataFrame:
        logger.info(f"Retrieving site data from Mosaiq for date: {date_str}")
        sql_query = self._SITE_STATEMENT_TEMPLATE.format(date_str)
        try:
            rows = self.query(sql_query, db_config)
            df_site = self._rows_to_dataframe(rows, self._SITE_COLUMNS)
        except Exception as e:
            logger.error(f"Error processing site data query for date {date_str}: {e}", exc_info=True)
            return pd.DataFrame()

        if df_site.empty:
            logger.info(f"No appointment data found for date: {date_str}")
            return df_site

        # Strip whitespace from string columns
        for col in df_site.select_dtypes(include=['object']).columns:
            df_site[col] = df_site[col].str.strip()

        logger.info(f"Retrieved {len(df_site)} SITE SET records from Mosaiq for date {date_str}.")
        return df_site

    def _create_rt_record_dataset(
        self, series_site_data: pd.Series, series_uid_data: pd.Series,
        plan_idx: int, db_config: Dict[str, str]
    ) -> Optional[FileDataset]:

        site_id = str(series_site_data["SIT_SET_ID"])
        mrd_id = str(series_site_data["MRN"])
        site_name = str(series_site_data.get("Site_Name", ""))
        setup_note = str(series_site_data.get("SetupNote", ""))
        activity = str(series_site_data.get("Activity", ""))

        rt_plan_uid = str(series_uid_data["RTPlanInstanceUID"])
        rt_plan_label = str(series_uid_data.get("Label", ""))
        plan_num = plan_idx + 1

        logger.info(f"Creating RT Record for MRN: {mrd_id}, Plan Index: {plan_idx}, RTPlanUID: {rt_plan_uid}")

        sql_query = self._RECORD_STATEMENT_TEMPLATE.format(site_id, rt_plan_uid)
        try:
            record_rows = self.query(sql_query, db_config)
            df_record_all_cps = self._rows_to_dataframe(record_rows, self._RECORD_COLUMNS)
        except Exception as e:
            logger.error(f"Error querying/processing record data for plan {rt_plan_uid}: {e}", exc_info=True)
            return None

        if df_record_all_cps.empty:
            logger.warning(f"No treatment records found for site_id {site_id}, plan {rt_plan_uid}.")
            return None

        file_meta = FileMetaDataset()
        file_meta.MediaStorageSOPClassUID = RTBeamsTreatmentRecordStorage
        file_meta.MediaStorageSOPInstanceUID = generate_uid()
        file_meta.ImplementationClassUID = '2.16.840.1.114362.1' # Example from Varian
        file_meta.ImplementationVersionName = 'MIM735O11703' # Example from Varian
        file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

        ds = FileDataset(None, {}, file_meta=file_meta, preamble=b"\x00" * 128)
        ds.is_little_endian = True
        ds.is_implicit_VR = False

        now = datetime.datetime.now(self._UTC_TZ)
        ds.InstanceCreationDate = now.strftime('%Y%m%d')
        ds.InstanceCreationTime = now.strftime('%H%M%S.%f')[:13] # DICOM DT format
        ds.SOPClassUID = RTBeamsTreatmentRecordStorage
        ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
        ds.Modality = "RTRECORD"
        ds.SpecificCharacterSet = 'ISO_IR 192' # UTF-8

        first_record_row = df_record_all_cps.iloc[0]

        # Patient Module
        patient_name = pydicom.valuerep.PersonName(
            f"{first_record_row.get('Last_Name', '')}^"
            f"{first_record_row.get('First_Name', '')}^"
            f"{first_record_row.get('MIddle_Name', '')}^"
            f"^{first_record_row.get('Suffix', '')}"
        )
        ds.PatientName = patient_name
        ds.PatientID = mrd_id # MRN from Ident table
        ds.PatientBirthDate = pd.to_datetime(first_record_row["Birth_DtTm"]).strftime('%Y%m%d') if pd.notna(first_record_row["Birth_DtTm"]) else ""
        ds.PatientSex = self._map_sex_to_dicom(first_record_row.get("PatientSex", "")) # Use helper

        # General Study Module
        ds.StudyInstanceUID = str(series_uid_data["StudyInstanceUID"])
        ds.StudyID = str(series_uid_data.get("StudyID", ""))
        ds.StudyDescription = str(series_uid_data.get("StudyDescription", ""))
        ds.StudyDate = pd.to_datetime(series_uid_data["Study_DtTm"]).strftime('%Y%m%d') if pd.notna(series_uid_data["Study_DtTm"]) else ""
        ds.StudyTime = pd.to_datetime(series_uid_data["Study_DtTm"]).strftime('%H%M%S') if pd.notna(series_uid_data["Study_DtTm"]) else ""

        # General Series Module
        ds.SeriesInstanceUID = generate_uid() # Generate new UID for this RT Record series
        ds.SeriesNumber = str(series_uid_data["SeriesNumber"]) if pd.notna(series_uid_data["SeriesNumber"]) else "1"
        ds.SeriesDescription = str(series_uid_data.get("SeriesDescription", ""))
        ds.SeriesDate = pd.to_datetime(series_uid_data["Series_DtTm"]).strftime('%Y%m%d') if pd.notna(series_uid_data["Series_DtTm"]) else ""
        ds.SeriesTime = pd.to_datetime(series_uid_data["Series_DtTm"]).strftime('%H%M%S') if pd.notna(series_uid_data["Series_DtTm"]) else ""

        # General Equipment Module
        ds.Manufacturer = str(first_record_row.get("MachineManufacturersModelName", "Elekta"))
        ds.ManufacturersModelName = str(first_record_row.get("MachineManufacturersModelName", "Mosaiq System"))
        ds.DeviceSerialNumber = str(first_record_row.get("DeviceSerialNumber", "UNKNOWN_SN"))
        ds.InstitutionalDepartmentName = str(first_record_row.get("InstitutionalDepartmentName", "Radiation Oncology"))

        # RT Common Module
        ds.TreatmentDate = pd.to_datetime(first_record_row["Last_Tx_DtTm"]).strftime('%Y%m%d') if pd.notna(first_record_row["Last_Tx_DtTm"]) else ""
        ds.TreatmentTime = pd.to_datetime(first_record_row["Last_Tx_DtTm"]).strftime('%H%M%S') if pd.notna(first_record_row["Last_Tx_DtTm"]) else ""
        ds.NumberOfFractionsPlanned = int(first_record_row["Fractions"]) if pd.notna(first_record_row["Fractions"]) else 0

        # RT Treatment Record Module (main content)
        ds.FractionGroupSequence = Sequence([])

        # Group by OriginalPlanUID and OriginalBeamNumber to process each unique beam delivery
        grouped_by_beam = df_record_all_cps.groupby(["OriginalPlanUID", "OriginalBeamNumber"])

        fg_item = Dataset()
        fg_item.FractionGroupNumber = 1 # Assuming one fraction group for the entire plan
        fg_item.NumberOfFractionsDelivered = int(first_record_row["Fractions_Tx"]) # Use the total delivered fractions for this beam/plan
        
        # Referenced RT Plan Sequence
        fg_item.ReferencedRTPlanSequence = Sequence([Dataset()])
        fg_item.ReferencedRTPlanSequence[0].ReferencedSOPClassUID = UID("1.2.840.10008.5.1.4.1.1.481.5") # RT Plan Storage
        fg_item.ReferencedRTPlanSequence[0].ReferencedSOPInstanceUID = rt_plan_uid
        
        fg_item.ReferencedBeamSequence = Sequence([])

        for (plan_uid, beam_number), beam_cps_df in grouped_by_beam:
            beam_data_common = beam_cps_df.iloc[0] # Use the first row for common beam data

            beam_record = Dataset()
            beam_record.BeamNumber = int(beam_number)
            beam_record.BeamName = str(beam_data_common.get("OriginalBeamName", beam_data_common.get("Field_Name", "")))
            beam_record.BeamDescription = str(beam_data_common.get("Field_Label", ""))
            beam_record.RadiationType = self._map_radiation_type(beam_data_common.get("Modality_Enum"))
            beam_record.TreatmentDeliveryType = self._map_beam_type_flag(beam_data_common.get("Beam_Type_Flag"))
            beam_record.SourceAxisDistance = float(beam_data_common["Sad"]) * 10.0 if pd.notna(beam_data_common["Sad"]) else 1000.0 # cm to mm

            # Fluence Mode
            beam_record.PrimaryFluenceModeSequence = Sequence([Dataset()])
            beam_record.PrimaryFluenceModeSequence[0].FluenceMode = self._map_fluence_mode(beam_data_common.get("Type_Enum"))

            # Delivered Meterset (total for the beam)
            beam_record.DeliveredPrimaryMeterset = float(beam_data_common.get("Meterset", 0.0)) # Total meterset for the beam
            
            # Control Point Sequence for the current beam
            beam_record.ControlPointSequence = Sequence()
            for _, cp_row_data in beam_cps_df.iterrows():
                cp_delivery_ds = Dataset()
                cp_delivery_ds.ControlPointIndex = int(cp_row_data.get("ControlPointIndex", cp_row_data.get("Point", 0)))
                
                cp_delivery_ds.TreatmentControlPointDate = pd.to_datetime(cp_row_data["PointTime"]).strftime('%Y%m%d') if pd.notna(cp_row_data["PointTime"]) else ""
                cp_delivery_ds.TreatmentControlPointTime = pd.to_datetime(cp_row_data["PointTime"]).strftime('%H%M%S.%f')[:13] if pd.notna(cp_row_data["PointTime"]) else ""
                
                cp_delivery_ds.SpecifiedMeterset = float(cp_row_data.get("SpecifiedMeterset_CP", 0.0))
                cp_delivery_ds.DeliveredMeterset = float(cp_row_data.get("DeliveredMeterset_CP", cp_row_data.get("Meterset", 0.0)))
                cp_delivery_ds.DoseRateDelivered = float(cp_row_data.get("DoseRateDelivered_CP", 0.0))
                cp_delivery_ds.NominalBeamEnergyUnit = self._map_energy_unit_enum_to_dicom(cp_row_data.get("Energy_Unit_Enum"))
                cp_delivery_ds.NominalBeamEnergy = float(cp_row_data["Energy"]) if pd.notna(cp_row_data["Energy"]) else 0.0
                cp_delivery_ds.DoseRateSet = float(cp_row_data.get("DoseRateSet_CP", 0.0))
                
                cp_delivery_ds.GantryAngle = float(cp_row_data["Gantry_Ang"]) if pd.notna(cp_row_data["Gantry_Ang"]) else 0.0
                cp_delivery_ds.GantryRotationDirection = self._map_rotation_direction_enum_to_dicom(cp_row_data.get("Gantry_Dir_Enum"))

                cp_delivery_ds.BeamLimitingDeviceAngle = float(cp_row_data["Coll_Ang"]) if pd.notna(cp_row_data["Coll_Ang"]) else 0.0
                cp_delivery_ds.BeamLimitingDeviceRotationDirection = self._map_rotation_direction_enum_to_dicom(cp_row_data.get("Coll_Dir_Enum"))

                cp_delivery_ds.PatientSupportAngle = float(cp_row_data["Couch_Ang"]) if pd.notna(cp_row_data["Couch_Ang"]) else 0.0
                cp_delivery_ds.PatientSupportRotationDirection = self._map_rotation_direction_enum_to_dicom(cp_row_data.get("Couch_Roll_Dir_Enum"))
                
                # Table Top Positions - convert cm to mm
                cp_delivery_ds.TableTopVerticalPosition = float(cp_row_data["Couch_Vrt"]) * 10.0 if pd.notna(cp_row_data["Couch_Vrt"]) else 0.0
                cp_delivery_ds.TableTopLongitudinalPosition = float(cp_row_data["Couch_Lng"]) * 10.0 if pd.notna(cp_row_data["Couch_Lng"]) else 0.0
                cp_delivery_ds.TableTopLateralPosition = float(cp_row_data["Couch_Lat"]) * 10.0 if pd.notna(cp_row_data["Couch_Lat"]) else 0.0
                
                cp_delivery_ds.TableTopEccentricAxisDistance = float(cp_row_data["Couch_Top_Axis_Distance"]) if pd.notna(cp_row_data["Couch_Top_Axis_Distance"]) else 0.0
                cp_delivery_ds.TableTopEccentricAngle = float(cp_row_data["Couch_Top"]) if pd.notna(cp_row_data["Couch_Top"]) else 0.0
                cp_delivery_ds.TableTopEccentricRotationDirection = self._map_rotation_direction_enum_to_dicom(cp_row_data.get("Couch_Top_Dir_Enum"))

                # Beam Limiting Device Position Sequence (Jaws and MLC)
                cp_delivery_ds.BeamLimitingDevicePositionSequence = Sequence()

                # Jaws (ASYMX, ASYMY)
                if pd.notna(cp_row_data.get("ASYMX_NumberOfLeafJawPairs")) and int(cp_row_data["ASYMX_NumberOfLeafJawPairs"]) > 0:
                    blds_asymx_cp = Dataset()
                    blds_asymx_cp.RTBeamLimitingDeviceType = "ASYMX"
                    # AsymX_LeafJawPositions is still a placeholder for string, assume it contains delimited floats
                    asymx_pos_str = cp_row_data.get("ASYMX_LeafJawPositions", "")
                    blds_asymx_cp.LeafJawPositions = [float(p.strip()) for p in asymx_pos_str.split(',') if p.strip()] if asymx_pos_str else [0.0, 0.0]
                    cp_delivery_ds.BeamLimitingDevicePositionSequence.append(blds_asymx_cp)

                if pd.notna(cp_row_data.get("ASYMY_NumberOfLeafJawPairs")) and int(cp_row_data["ASYMY_NumberOfLeafJawPairs"]) > 0:
                    blds_asymy_cp = Dataset()
                    blds_asymy_cp.RTBeamLimitingDeviceType = "ASYMY"
                    asymy_pos_str = cp_row_data.get("ASYMY_LeafJawPositions", "")
                    blds_asymy_cp.LeafJawPositions = [float(p.strip()) for p in asymy_pos_str.split(',') if p.strip()] if asymy_pos_str else [0.0, 0.0]
                    cp_delivery_ds.BeamLimitingDevicePositionSequence.append(blds_asymy_cp)

                # MLC
                if pd.notna(beam_data_common.get("Mlc")) and int(beam_data_common["Mlc"]) == 1:
                    blds_mlcx_cp = Dataset()
                    blds_mlcx_cp.RTBeamLimitingDeviceType = "MLCX"
                    # This is where the actual binary parsing needs to happen.
                    # Currently, it's just passing an empty list as a placeholder for parsing logic.
                    a_leaf_data = cp_row_data.get("A_Leaf_Set")
                    b_leaf_data = cp_row_data.get("B_Leaf_Set")
                    
                    # Assuming parse_binary_leaf_data returns a list of floats
                    mlc_a_positions = self._parse_binary_leaf_data(a_leaf_data)
                    mlc_b_positions = self._parse_binary_leaf_data(b_leaf_data)
                    
                    # Combine A and B leaf positions correctly for DICOM, typically in pairs
                    # (e.g., [leaf_A_position, leaf_B_position, leaf_A_position, leaf_B_position, ...])
                    # This requires specific understanding of how A_Leaf_Set and B_Leaf_Set relate.
                    # For a simple 60-leaf MLC, it might be 30 pairs, or 60 floats for each leaf bank.
                    # Example for a simple case, adjust as needed:
                    leaf_positions_combined = []
                    if len(mlc_a_positions) == len(mlc_b_positions):
                        for i in range(len(mlc_a_positions)):
                            leaf_positions_combined.extend([mlc_a_positions[i], mlc_b_positions[i]])
                    
                    blds_mlcx_cp.LeafJawPositions = [float(pos) for pos in leaf_positions_combined]
                    cp_delivery_ds.BeamLimitingDevicePositionSequence.append(blds_mlcx_cp)
                
                beam_record.ControlPointSequence.append(cp_delivery_ds)
            
            beam_record.NumberOfControlPoints = len(beam_record.ControlPointSequence)
            fg_item.ReferencedBeamSequence.append(beam_record)

        ds.FractionGroupSequence.append(fg_item)

        # Private Block (custom data)
        creator = 'TJU RadOnc Customized Data'
        try:
            block = ds.private_block(0x3261, creator, create=True)
            block.add_new(0x01, 'LT', site_name)
            block.add_new(0x02, 'LT', setup_note)
            block.add_new(0x03, 'LT', activity)
            block.add_new(0x04, 'LT', rt_plan_label)
        except Exception as e:
            logger.warning(f"Could not create private block 0x3261 for plan {rt_plan_uid}: {e}")

        # Modified Treatment Machine Sequence (populated with actual data)
        machine_ds = Dataset()
        machine_ds.Manufacturer = str(first_record_row.get("MachineManufacturersModelName", "Varian Medical Systems"))
        machine_ds.InstitutionalDepartmentName = str(first_record_row.get("InstitutionalDepartmentName", "Radiation Oncology Dept"))
        machine_ds.TreatmentMachineName = str(first_record_row.get("MachineName", "Unknown"))[:16] # MachineName is from Staff.Last_Name
        machine_ds.ManufacturerModelName = str(first_record_row.get("Machine_ManufacturersModelName_Seq", "Unknown"))
        machine_ds.DeviceSerialNumber = str(first_record_row.get("Machine_DeviceSerialNumber_Seq", "Unknown"))
        ds.TreatmentMachineSequence = Sequence([machine_ds])

        # Referenced RT Plan Sequence (already added inside FractionGroupSequence, but can also be top-level if needed)
        # For RT Treatment Record, it's typically within FractionGroupSequence.

        logger.info(f"Successfully created RT Record dataset for MRN: {mrd_id}, Plan UID: {rt_plan_uid}")
        return ds

    def generate_rt_records_for_sites(self, site_data_df: pd.DataFrame, db_config: Dict[str, str]) -> List[FileDataset]:
        rt_record_datasets: List[FileDataset] = []
        if site_data_df.empty:
            logger.info("No site data provided to generate_rt_records_for_sites.")
            return rt_record_datasets

        for _, series_site_data in site_data_df.iterrows():
            site_id = str(series_site_data["SIT_SET_ID"])
            logger.info(f"Processing site SIT_SET_ID: {site_id} for RT Record generation.")

            sql_query = self._UID_STATEMENT_TEMPLATE.format(site_id)
            try:
                uid_rows = self.query(sql_query, db_config)
                df_uid = self._rows_to_dataframe(uid_rows, self._UID_COLUMNS)
            except Exception as e:
                logger.error(f"Error querying/processing UID data for site {site_id}: {e}", exc_info=True)
                continue

            logger.info(f"Found {len(df_uid)} RT plan(s) for site {site_id}.")
            if df_uid.empty:
                continue

            for idx_plan, series_uid_data in df_uid.iterrows():
                dataset = self._create_rt_record_dataset(series_site_data, series_uid_data, idx_plan, db_config)
                if dataset:
                    rt_record_datasets.append(dataset)

        logger.info(f"Generated {len(rt_record_datasets)} RT Record datasets in total.")
        return rt_record_datasets

    def get_rt_records_for_date(self, db_config: Dict[str, str], target_date: Optional[str] = None) -> List[FileDataset]:
        if target_date is None:
            date_str = datetime.datetime.now(tz=self._EST_TZ).date().strftime("%Y-%m-%d")
            logger.info(f"No target date provided, using current EST date: {date_str}")
        else:
            date_str = target_date
            logger.info(f"Target date for RT Record generation: {date_str}")

        site_df = self._get_site_data_df(db_config, date_str)
        if site_df.empty:
            logger.warning(f"No site data found for date {date_str}, cannot generate RT Records.")
            return []

        return self.generate_rt_records_for_sites(site_df, db_config)

    def transfer(self, rt_record: Dataset, store_scp: Dict[str, Any]) -> bool:
        if not isinstance(rt_record, Dataset):
            logger.error("Invalid rt_record type. Must be a pydicom Dataset.")
            # raise TypeError("rt_record must be a pydicom Dataset object") # Keep original exception type for now
            return False

        logger.info(
            f"Preparing to transfer RT Record SOPInstanceUID "
            f"{rt_record.get('SOPInstanceUID', 'Not Set Yet')} to SCP {store_scp['AETitle']}."
        )

        self._prepare_rt_record_for_transfer(rt_record)

        ae = AE()
        # Ensure a default AET for the AE SCU if not specified elsewhere, or use a passed in calling_aet
        # For now, pynetdicom will generate one if ae_title is not set on AE()
        transfer_syntax = getattr(rt_record.file_meta, 'TransferSyntaxUID', ExplicitVRLittleEndian)
        ae.add_requested_context(rt_record.SOPClassUID, transfer_syntax)

        logger.info(
            f"Attempting C-STORE association to SCP: {store_scp['AETitle']} "
            f"at {store_scp['IP']}:{store_scp['Port']}"
        )
        assoc = None
        store_successful = False
        try:
            assoc = ae.associate(store_scp["IP"], store_scp["Port"], ae_title=store_scp["AETitle"])
            if assoc.is_established:
                logger.info("C-STORE Association established.")
                if not assoc.accepted_contexts:
                    logger.error(f"No presentation contexts accepted by the SCP for SOP Class {rt_record.SOPClassUID} and syntax {transfer_syntax}.")
                    # No MosaiqQueryError raised here, will return False

                status = assoc.send_c_store(rt_record)
                if status:
                    logger.info(f"C-STORE request completed. Status: 0x{status.Status:04X}.")
                    if hasattr(status, "ErrorComment") and status.ErrorComment:
                        logger.warning(f"C-STORE Error Comment: {status.ErrorComment}")
                    if status.Status == 0x0000:
                        store_successful = True
                    else:
                        logger.error(f"C-STORE operation failed with status 0x{status.Status:04X}. SCP Comment: {status.ErrorComment or 'N/A'}")
                        # No MosaiqQueryError raised here, will return False
                else:
                    logger.error("C-STORE request failed: No status returned (connection timed out or aborted).")
                    # No MosaiqQueryError raised here, will return False
            else:
                reason = (assoc.acceptor.primitive.result_str if assoc.acceptor and assoc.acceptor.primitive else "Unknown reason")
                logger.error(f"C-STORE Association rejected or aborted: {reason}")
                # No MosaiqQueryError raised here, will return False
        except Exception as e:
            # Catching broader exceptions as per original code, but now ensuring False is returned.
            log_msg = f"Exception during C-STORE operation or association: {e}"
            logger.error(log_msg, exc_info=True)
            store_successful = False # Ensure failure on any exception
        finally:
            if assoc and assoc.is_established:
                logger.debug("Releasing C-STORE association.")
                assoc.release()
        
        if store_successful:
            logger.info(f"C-STORE to {store_scp['AETitle']} for {rt_record.SOPInstanceUID} reported success.")
        else:
            logger.error(f"C-STORE to {store_scp['AETitle']} for {rt_record.SOPInstanceUID} reported failure or was not established.")
        return store_successful

    def _prepare_rt_record_for_transfer(self, rt_record: Dataset) -> None:
        if not hasattr(rt_record, 'SOPClassUID') or not rt_record.SOPClassUID:
             rt_record.SOPClassUID = RTBeamsTreatmentRecordStorage

        if not hasattr(rt_record, 'SOPInstanceUID') or not rt_record.SOPInstanceUID:
            rt_record.SOPInstanceUID = generate_uid()
            logger.debug(f"Generated new SOPInstanceUID for RT Record: {rt_record.SOPInstanceUID}")

        if not hasattr(rt_record, "file_meta"):
            rt_record.file_meta = FileMetaDataset()
            logger.debug("Created new FileMetaDataset for RT Record.")

        rt_record.file_meta.FileMetaInformationVersion = b"\x00\x01"
        rt_record.file_meta.MediaStorageSOPClassUID = rt_record.SOPClassUID
        rt_record.file_meta.MediaStorageSOPInstanceUID = rt_record.SOPInstanceUID

        if not getattr(rt_record.file_meta, 'TransferSyntaxUID', None):
            rt_record.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        if not getattr(rt_record.file_meta, 'ImplementationClassUID', None):
            implementation_uid_prefix = "1.2.826.0.1.3680043.9.7156.1.99."
            rt_record.file_meta.ImplementationClassUID = generate_uid(prefix=implementation_uid_prefix)
        if not getattr(rt_record.file_meta, 'ImplementationVersionName', None):
            rt_record.file_meta.ImplementationVersionName = "RadOncBackupSystem_Mosaiq_1.1"

        rt_record.is_little_endian = True
        rt_record.is_implicit_VR = (rt_record.file_meta.TransferSyntaxUID == ImplicitVRLittleEndian)


    def get_treatment_summary_report(
        self,
        patient_mrn: str,
        db_config: Dict[str, str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        sql_query, params = self._build_treatment_summary_sql(
            patient_mrn, start_date, end_date
        )
        logger.info(f"Fetching treatment summary report for MRN: {patient_mrn} with date range: {start_date or 'N/A'} - {end_date or 'N/A'}")
        try:
            rows = self.query(sql_query, db_config, params=params)
            if not rows:
                logger.info(f"No treatment records found for MRN: {patient_mrn}.")
                return []
            report_data: List[Dict[str, Any]] = []
            for row_tuple in rows:
                if len(row_tuple) != len(self._TREATMENT_SUMMARY_COLUMNS):
                    error_msg = f"Query for patient {patient_mrn} returned an unexpected number of columns. Expected {len(self._TREATMENT_SUMMARY_COLUMNS)}, got {len(row_tuple)}."
                    logger.error(error_msg)
                    raise ValueError("Mismatch between expected columns and query result columns.")
                record = dict(zip(self._TREATMENT_SUMMARY_COLUMNS, row_tuple))
                report_data.append(record)
            logger.info(f"Successfully fetched {len(report_data)} treatment records for patient {patient_mrn}.")
            return report_data
        except MosaiqQueryError:
            raise
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"An unexpected error occurred while processing treatment summary for patient {patient_mrn}: {e}", exc_info=True)
            raise MosaiqQueryError(f"Unexpected error processing report data for {patient_mrn}: {e}") from e

    def _build_treatment_summary_sql(
        self, patient_mrn: str, start_date: Optional[str], end_date: Optional[str]
    ) -> Tuple[str, List[Any]]:
        params: List[Any] = []
        sql_query_base = """
            SELECT
                Pat.Last_Name + ', ' + Pat.First_Name AS PatientName,
                ID.IDA AS PatientMRN, -- Using IDA for MRN based on other queries
                TxFld.Start_DtTm AS StartDate, -- Use TxField.Start_DtTm
                TxFld.Last_Tx_DtTm AS EndDate, -- Use TxField.Last_Tx_DtTm
                SIT.Dose_Ttl AS TotalDose, -- Use Site.Dose_Ttl for total dose
                SIT.Fractions AS NumberOfFractions, -- Use Site.Fractions for planned fractions
                SIT.Site_Name AS TargetVolume -- Using Site.Site_Name as target volume
            FROM
                Patient Pat
            INNER JOIN Ident ID ON Pat.Pat_ID1 = ID.Pat_Id1 -- Join to Ident
            INNER JOIN TxField TxFld ON Pat.Pat_ID1 = TxFld.Pat_ID1 -- Join to TxField
            INNER JOIN Site SIT ON TxFld.SIT_Set_ID = SIT.SIT_SET_ID -- Join to Site for dose/fractions
            WHERE
                ID.IDA = ? AND TxFld.Version = 0 AND SIT.Version = 0 -- Filter for current versions
        """
        params.append(patient_mrn)
        date_filters_str: str = ""
        if start_date:
            date_filters_str += " AND TxFld.Start_DtTm >= ?"
            params.append(start_date)
        if end_date:
            date_filters_str += " AND TxFld.Last_Tx_DtTm <= ?"
            params.append(end_date)
        sql_query_suffix = """
            GROUP BY
                Pat.Last_Name, Pat.First_Name, ID.IDA, -- Group by patient identifiers
                TxFld.Start_DtTm, TxFld.Last_Tx_DtTm, SIT.Dose_Ttl, SIT.Fractions, SIT.Site_Name -- Group by relevant fields
            ORDER BY
                TxFld.Start_DtTm DESC;
        """
        final_sql = sql_query_base + date_filters_str + sql_query_suffix
        return final_sql, params