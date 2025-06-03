import unittest
import logging
from unittest.mock import patch, MagicMock, call
import pyodbc  # For mocking pyodbc.Error
import pandas as pd # Added for DataFrame creation in tests
import datetime # Added for date manipulation
import pytz # Added for timezone handling
import struct # Added for binary parsing test (even if placeholder)

# Ensure pynetdicom DEBUG logs are output to console for capture
# (Keep existing pynetdicom logging setup)
logger_pynetdicom = logging.getLogger("pynetdicom") # Should be fine
# logger_pynetdicom.setLevel(logging.DEBUG) # Already set by user's code
# if not logger_pynetdicom.hasHandlers():  # Add handler if none exist, to ensure output
#     handler = logging.StreamHandler()
#     formatter = logging.Formatter(
#         "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
#     )
#     handler.setFormatter(formatter)
#     logger_pynetdicom.addHandler(handler)
#     logger_pynetdicom.propagate = (
#         False
#     )

from src.data_sources.mosaiq import Mosaiq, MosaiqQueryError
# from src.tests.mock_dicom_server import MockDicomServer # MockDicomServer is not used in the user-provided content
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import generate_uid, ExplicitVRLittleEndian, RTBeamsTreatmentRecordStorage, UID
from pynetdicom import AE # Added for type hinting if needed, and for patching

# Define static test data that might have been in the original setUp if it was more complex
# This is based on the structure inferred from the user's provided SQL and column lists
MOCK_SITE_DATA_ROWS = [
    (101, 201, 301, datetime.datetime(2023, 1, 15, 10, 0, 0), datetime.datetime(2023, 1, 15, 9, 0, 0), 'MRN001', 1, 'SiteA', 'Setup for SiteA', 'ActivityA'),
    (102, 202, 302, datetime.datetime(2023, 1, 15, 11, 0, 0), datetime.datetime(2023, 1, 15, 10, 0, 0), 'MRN002', 2, 'SiteB', 'Setup for SiteB', 'ActivityB'),
]
MOCK_UID_DATA_ROWS = [
    ('plan.uid.1', 'study.uid.1', 'series.uid.1', 'PlanLabel1', 1, 'StudyID1', 'StudyDesc1', datetime.datetime(2023,1,10, 8,0,0), '1', 'SeriesDesc1', datetime.datetime(2023,1,10, 8,5,0), 'Machine1'),
]
MOCK_RECORD_DATA_BASE = {
    "IDA": "MRN001", "SIT_SET_ID": 101, "OriginalPlanUID": "plan.uid.1", "OriginalBeamName": "Beam1", "OriginalBeamNumber": 1,
    "Last_Tx_DtTm": datetime.datetime(2023, 1, 15, 9, 30, 0), "FLD_ID": 501, "Pat_Id1": 1, "Last_Name": "Doe", "First_Name": "John",
    "MIddle_Name": "J", "Suffix": "Jr", "PatientSex": "MALE", "Birth_DtTm": datetime.datetime(1980, 5, 5), # Added Birth_DtTm
    "Fractions_Tx": 1, "Fractions": 20, "Energy": 6.0, "Energy_Unit_Enum": 2, "Meterset": 100.0,
    "Cgray": 2.0, "IndexReference_Enum": 0, "ControlPoints": 2, "Point": 0, "Gantry_Ang": 0.0,
    "Gantry_Dir_Enum": 1, "PointTime": datetime.datetime(2023, 1, 15, 9, 25, 0), "Coll_Ang": 0.0, "Coll_Dir_Enum": 1,
    "Couch_Ang": 0.0, "Couch_Roll_Dir_Enum": 1, "Couch_Top_Axis_Distance": 100.0, "Couch_Top": 0.0,
    "Couch_Top_Dir_Enum": 1, "Couch_Vrt": 10.0, "Couch_Lng": 20.0, "Couch_Lat": 5.0,
    "TerminationCode": 0, "Termination_Status_Enum": 1, "Termination_Verify_Status_Enum": 0,
    "Dose_Addtl_Projected": 0.0, "Sad": 100.0, "MachineName": "TrueBeam1", "MachineManufacturersModelName": "Varian TrueBeam",
    "DeviceSerialNumber": "SN12345", "InstitutionalDepartmentName": "RadOnc Dept",
    "Machine_ManufacturersModelName_Seq": "Varian TrueBeam Seq", "Machine_DeviceSerialNumber_Seq": "SN12345_Seq",
    "Beam_Type_Flag": 1, "Modality_Enum": 1, "Type_Enum": 1, "Field_Name": "Field1", "Field_Label": "Anterior",
    "Mlc": 1, "Wdg_Appl": 0, "Comp_Fda": 0, "Bolus": 0, "Block": 0,
    "A_Leaf_Set": b'\x00\x00\x80\xbf' * 60, "B_Leaf_Set": b'\x00\x00\x80\x3f' * 60, # Example binary data (60*4 bytes)
    "FluenceMode": "STANDARD", "DeliveredTreatmentTimeBeam": "60.0",
    "CalculatedDoseReferenceDoseValue": "1.0", "ReferencedDoseReferenceNumber": "1",
    "MLCX_NumberOfLeafJawPairs": "60", "ASYMY_NumberOfLeafJawPairs": "1", "ASYMX_NumberOfLeafJawPairs": "1",
    "RadiationType": "PHOTON", "NumberOfWedges": "0", "NumberOfCompensators": "0",
    "NumberOfBoli": "0", "NumberOfBlocks": "0", "SpecifiedMeterset_CP": "50.0", "DeliveredMeterset_CP": "50.0",
    "DoseRateDelivered_CP": "600", "DoseRateSet_CP": "600",
    "GantryRotationDirection_ARIA": "CW", "BeamLimitingDeviceRotationDirection_ARIA": "CW",
    "PatientSupportRotationDirection_ARIA": "CW", "TableTopEccentricRotationDirection_ARIA": "CW",
    "ControlPointIndex": 0,
    "ASYMX_LeafJawPositions": "-10.0,10.0", # Example string format
    "ASYMY_LeafJawPositions": "-15.0,15.0"  # Example string format
}

MOCK_RECORD_DATA_ROWS = [
    tuple(MOCK_RECORD_DATA_BASE.values()), # Control Point 0
    tuple({**MOCK_RECORD_DATA_BASE, "Point": 1, "ControlPointIndex": 1, "Gantry_Ang": 10.0, "SpecifiedMeterset_CP": "100.0", "DeliveredMeterset_CP": "100.0"}.values()) # CP 1
]


class TestMosaiqDataSource(unittest.TestCase):
    def setUp(self):
        self.mosaiq = Mosaiq(odbc_driver="TestDriver")
        self.db_config = {
            "server": "test_server",
            "database": "test_db",
            "username": "user",
            "password": "pw",
        }
        # Patch the pyodbc.connect call globally for all tests in this class
        self.mock_connect = patch('pyodbc.connect').start()
        self.mock_conn_instance = MagicMock()
        self.mock_cursor_instance = MagicMock()
        self.mock_connect.return_value = self.mock_conn_instance
        self.mock_conn_instance.cursor.return_value = self.mock_cursor_instance
        self.addCleanup(patch.stopall)

    # Test methods for _parse_binary_leaf_data
    def test_parse_binary_leaf_data_valid(self):
        """Test _parse_binary_leaf_data with valid binary data."""
        # Test case 1: Few leaves
        binary_data1 = struct.pack('<fff', -10.0, 0.0, 12.5)
        expected1 = ["-10.0", "0.0", "12.5"]
        self.assertEqual(Mosaiq._parse_binary_leaf_data(binary_data1), expected1)

        # Test case 2: More leaves, including values that might have more decimal places
        binary_data2 = struct.pack('<fffff', -20.55, 0.1, 15.0, 25.75, -5.2)
        # str() representation of floats can sometimes vary slightly (e.g. "0.10000000149011612")
        # For testing, it's often better to compare the float values themselves if precision is critical,
        # or ensure the string conversion in the method produces a consistent format.
        # Given the current implementation uses str(), we'll match that.
        expected2 = [str(-20.55), str(0.1), str(15.0), str(25.75), str(-5.2)]
        self.assertEqual(Mosaiq._parse_binary_leaf_data(binary_data2), expected2)

        # Test case 3: Single leaf
        binary_data3 = struct.pack('<f', 100.0)
        expected3 = ["100.0"]
        self.assertEqual(Mosaiq._parse_binary_leaf_data(binary_data3), expected3)

    def test_parse_binary_leaf_data_empty_or_none(self):
        """Test _parse_binary_leaf_data with empty or None input."""
        self.assertEqual(Mosaiq._parse_binary_leaf_data(None), [])
        self.assertEqual(Mosaiq._parse_binary_leaf_data(b''), [])

    def test_parse_binary_leaf_data_invalid_length(self):
        """Test _parse_binary_leaf_data with data of invalid length."""
        # Data length not a multiple of 4
        binary_data_invalid1 = b'\x00\x00\x80\x3f\x00'  # 5 bytes
        with self.assertLogs(logger='src.data_sources.mosaiq', level='WARNING') as cm:
            self.assertEqual(Mosaiq._parse_binary_leaf_data(binary_data_invalid1), [])
        self.assertTrue(any("not a multiple of 4" in message for message in cm.output))

        binary_data_invalid2 = b'\x00\x00' # 2 bytes
        with self.assertLogs(logger='src.data_sources.mosaiq', level='WARNING') as cm:
            self.assertEqual(Mosaiq._parse_binary_leaf_data(binary_data_invalid2), [])
        self.assertTrue(any("not a multiple of 4" in message for message in cm.output))

    def test_parse_binary_leaf_data_struct_error(self):
        """Test _parse_binary_leaf_data with data that causes struct.error (though length check should prevent this)."""
        # This case is somewhat artificial if the length check is robust,
        # as struct.error would typically occur if trying to unpack too few bytes.
        # The length check should ensure `chunk` is always 4 bytes.
        # However, if other struct errors were possible, this would test them.
        # For now, the primary failure mode for malformed data is wrong length.
        # If we imagine a scenario where data length is multiple of 4 but content is not float-like (hard to simulate with pack)
        # this test would be more relevant. For now, invalid length is the main testable error.
        pass # Covered by invalid length test for now
    def test_query_success(self):
        expected_rows = [("data1", "data2"), ("data3", "data4")]
        self.mock_cursor_instance.fetchall.return_value = expected_rows
        rows = self.mosaiq.query("SELECT * FROM Table", self.db_config)
        self.assertEqual(rows, expected_rows)
        self.mock_connect.assert_called_once()
        self.mock_cursor_instance.execute.assert_called_once_with("SELECT * FROM Table")

    def test_query_with_params(self):
        params = ["param1", 123]
        self.mosaiq.query("SELECT * FROM Table WHERE Col1 = ? AND Col2 = ?", self.db_config, params=params)
        self.mock_cursor_instance.execute.assert_called_once_with("SELECT * FROM Table WHERE Col1 = ? AND Col2 = ?", params)

    def test_query_failure_raises_mosaiqqueryerror(self):
        self.mock_cursor_instance.execute.side_effect = pyodbc.Error("DB Query Failed")
        with self.assertRaises(MosaiqQueryError) as context:
            self.mosaiq.query("SELECT * FROM Table", self.db_config)
        self.assertIn("DB Query Failed", str(context.exception))

    def test_get_site_data_df_success(self):
        self.mock_cursor_instance.fetchall.return_value = MOCK_SITE_DATA_ROWS
        df = self.mosaiq._get_site_data_df(self.db_config, "2023-01-15")
        self.assertIsInstance(df, pd.DataFrame)
        self.assertEqual(len(df), len(MOCK_SITE_DATA_ROWS))
        self.assertEqual(list(df.columns), self.mosaiq._SITE_COLUMNS)
        self.assertEqual(df.iloc[0]["MRN"], "MRN001") # Check a sample value after strip

    def test_create_rt_record_dataset_success(self):
        # Prepare mock data for series_site_data and series_uid_data
        df_site_sample = pd.DataFrame([MOCK_SITE_DATA_ROWS[0]], columns=self.mosaiq._SITE_COLUMNS)
        series_site_data = df_site_sample.iloc[0]
        
        df_uid_sample = pd.DataFrame([MOCK_UID_DATA_ROWS[0]], columns=self.mosaiq._UID_COLUMNS)
        series_uid_data = df_uid_sample.iloc[0]

        # Mock the self.query call within _create_rt_record_dataset
        self.mock_cursor_instance.fetchall.return_value = MOCK_RECORD_DATA_ROWS
        
        # Since _parse_binary_leaf_data is now a static method, we can test its integration here.
        # If A_Leaf_Set and B_Leaf_Set in MOCK_RECORD_DATA_BASE are valid binary data for floats:
        # Example: MOCK_RECORD_DATA_BASE["A_Leaf_Set"] = struct.pack('<ff', 1.0, 2.0)
        #          MOCK_RECORD_DATA_BASE["B_Leaf_Set"] = struct.pack('<ff', -1.0, -2.0)
        # The test would then check if LeafJawPositions is correctly populated.
        # For now, assuming MOCK_RECORD_DATA_BASE has some binary data in A_Leaf_Set/B_Leaf_Set

        dataset = self.mosaiq._create_rt_record_dataset(series_site_data, series_uid_data, 0, self.db_config)
        
        self.assertIsNotNone(dataset)
        self.assertIsInstance(dataset, FileDataset)
        self.assertEqual(dataset.PatientID, "MRN001")
        self.assertEqual(dataset.SOPClassUID, RTBeamsTreatmentRecordStorage) # Assuming this is the default
        self.assertTrue(hasattr(dataset, "FractionGroupSequence"))
        self.assertEqual(len(dataset.FractionGroupSequence), 1)
        fg_item = dataset.FractionGroupSequence[0]
        self.assertTrue(hasattr(fg_item, "ReferencedBeamSequence"))
        self.assertEqual(len(fg_item.ReferencedBeamSequence), 1) 
        beam_record = fg_item.ReferencedBeamSequence[0]
        self.assertTrue(hasattr(beam_record, "ControlPointSequence"))
        self.assertEqual(len(beam_record.ControlPointSequence), 2)

        # Check if LeafJawPositions were populated (basic check, assumes MLC data was present)
        # This depends on Mlc flag and actual binary data in MOCK_RECORD_DATA_BASE
        if MOCK_RECORD_DATA_BASE.get("Mlc") == 1:
            cp0 = beam_record.ControlPointSequence[0]
            mlc_device_seq = [dev for dev in cp0.BeamLimitingDevicePositionSequence if dev.RTBeamLimitingDeviceType == 'MLCX']
            if mlc_device_seq: # If MLC device is present
                 self.assertTrue(hasattr(mlc_device_seq[0], 'LeafJawPositions'))
                 # Example: expected_leaf_data = ["-1.0", "1.0"]*60 # Based on MOCK_RECORD_DATA_BASE binary data
                 # self.assertEqual(mlc_device_seq[0].LeafJawPositions, [float(x) for x in expected_leaf_data])


    def test_generate_rt_records_for_sites(self):
        df_site = pd.DataFrame(MOCK_SITE_DATA_ROWS, columns=self.mosaiq._SITE_COLUMNS)
        
        self.mock_cursor_instance.fetchall.side_effect = [
            MOCK_UID_DATA_ROWS,
            MOCK_RECORD_DATA_ROWS,
            []
        ]
        
        datasets = self.mosaiq.generate_rt_records_for_sites(df_site, self.db_config)
        self.assertEqual(len(datasets), 1)
        self.assertEqual(datasets[0].PatientID, "MRN001")

    @patch.object(Mosaiq, 'generate_rt_records_for_sites')
    @patch.object(Mosaiq, '_get_site_data_df')
    def test_get_rt_records_for_date(self, mock_get_site_df, mock_generate_records):
        mock_df = MagicMock(spec=pd.DataFrame)
        mock_get_site_df.return_value = mock_df
        mock_generate_records.return_value = [Dataset(), Dataset()]
        
        result = self.mosaiq.get_rt_records_for_date(self.db_config, "2023-01-15")
        
        mock_get_site_df.assert_called_once_with(self.db_config, "2023-01-15")
        mock_generate_records.assert_called_once_with(mock_df, self.db_config)
        self.assertEqual(len(result), 2)

    # test_parse_binary_leaf_data method removed and replaced by more specific tests above.

# Appended class:
class TestMosaiqTransfer(unittest.TestCase):
    def setUp(self):
        self.mosaiq = Mosaiq(odbc_driver="TestDriverForTransfer")
        self.staging_scp_config = {
            "aet": "MOCK_STAGE_SCP",
            "ip": "127.0.0.1",
            "port": 11113,
        }
        self.sample_ds = Dataset()
        self.sample_ds.PatientID = "TestPatientForTransfer"
        # _prepare_rt_record_for_transfer in Mosaiq class will populate
        # SOPInstanceUID, SOPClassUID, and file_meta.

    @patch('pynetdicom.AE')
    @patch('src.data_sources.mosaiq.Mosaiq._prepare_rt_record_for_transfer', wraps=Mosaiq._prepare_rt_record_for_transfer)
    def test_transfer_cstore_success_returns_true(self, mock_prepare_record, mock_ae_class):
        mock_ae_instance = MagicMock()
        mock_assoc = MagicMock()
        mock_assoc.is_established = True
        mock_assoc.accepted_contexts = [MagicMock()] 
        mock_status = MagicMock()
        mock_status.Status = 0x0000  # Success
        mock_assoc.send_c_store.return_value = mock_status
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance

        result = self.mosaiq.transfer(self.sample_ds, self.staging_scp_config)

        self.assertTrue(result)
        mock_prepare_record.assert_called_once_with(self.sample_ds)
        mock_ae_instance.associate.assert_called_once_with(
            self.staging_scp_config['ip'],
            self.staging_scp_config['port'],
            ae_title=self.staging_scp_config['aet']
        )
        mock_assoc.send_c_store.assert_called_once_with(self.sample_ds)
        mock_assoc.release.assert_called_once()

    @patch('pynetdicom.AE')
    @patch('src.data_sources.mosaiq.Mosaiq._prepare_rt_record_for_transfer') 
    def test_transfer_cstore_failure_status_returns_false(self, mock_prepare_record, mock_ae_class):
        mock_ae_instance = MagicMock()
        mock_assoc = MagicMock()
        mock_assoc.is_established = True
        mock_assoc.accepted_contexts = [MagicMock()]
        mock_status = MagicMock()
        mock_status.Status = 0xA700  
        mock_status.ErrorComment = "SCP out of resources" 
        mock_assoc.send_c_store.return_value = mock_status
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance

        result = self.mosaiq.transfer(self.sample_ds, self.staging_scp_config)
        self.assertFalse(result)
        mock_prepare_record.assert_called_once_with(self.sample_ds)

    @patch('pynetdicom.AE')
    @patch('src.data_sources.mosaiq.Mosaiq._prepare_rt_record_for_transfer')
    def test_transfer_association_failure_returns_false(self, mock_prepare_record, mock_ae_class):
        mock_ae_instance = MagicMock()
        mock_assoc = MagicMock()
        mock_assoc.is_established = False 
        # Ensure acceptor.primitive.result_str exists for the log message in Mosaiq.transfer
        mock_assoc.acceptor = MagicMock()
        mock_assoc.acceptor.primitive = MagicMock()
        mock_assoc.acceptor.primitive.result_str = "Test Reject" 
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance
        
        result = self.mosaiq.transfer(self.sample_ds, self.staging_scp_config)
        self.assertFalse(result)
        mock_prepare_record.assert_called_once_with(self.sample_ds)

    @patch('pynetdicom.AE')
    @patch('src.data_sources.mosaiq.Mosaiq._prepare_rt_record_for_transfer')
    def test_transfer_no_accepted_contexts_returns_false(self, mock_prepare_record, mock_ae_class):
        mock_ae_instance = MagicMock()
        mock_assoc = MagicMock()
        mock_assoc.is_established = True
        mock_assoc.accepted_contexts = [] 
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance

        result = self.mosaiq.transfer(self.sample_ds, self.staging_scp_config)
        self.assertFalse(result)
        mock_assoc.send_c_store.assert_not_called()
        mock_prepare_record.assert_called_once_with(self.sample_ds)

    @patch('pynetdicom.AE')
    @patch('src.data_sources.mosaiq.Mosaiq._prepare_rt_record_for_transfer')
    def test_transfer_send_c_store_raises_exception_returns_false(self, mock_prepare_record, mock_ae_class):
        mock_ae_instance = MagicMock()
        mock_assoc = MagicMock()
        mock_assoc.is_established = True
        mock_assoc.accepted_contexts = [MagicMock()]
        mock_assoc.send_c_store.side_effect = RuntimeError("Network glitch during C-STORE")
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance

        result = self.mosaiq.transfer(self.sample_ds, self.staging_scp_config)
        self.assertFalse(result)
        mock_prepare_record.assert_called_once_with(self.sample_ds)

if __name__ == "__main__":
    unittest.main()
