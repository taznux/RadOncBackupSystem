import unittest
from unittest.mock import patch, MagicMock, mock_open, call as mock_call
import argparse
from argparse import Namespace # Added
import tomllib
import os
import logging
import io 
# import functools # No longer needed

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from src.cli.backup import (
    backup_data, 
    main as backup_main, 
    # handle_store, # Removed
    _load_configurations,
    _initialize_source_system,
    _initialize_orthanc_uploader,
    _build_aria_mim_cfind_dataset,
    _handle_aria_mim_backup,
    _build_mosaiq_dataset_from_row,
    _handle_mosaiq_backup,
    BackupError,
    BackupConfigError
)
from src.cli.backup import ENVIRONMENTS_CONFIG_PATH, DICOM_CONFIG_PATH
from src.cli.dicom_utils import DicomOperationError, DicomConnectionError # Added

from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid, UID 
# from pynetdicom import evt # No longer needed
from src.data_sources.aria import ARIA
from src.data_sources.mim import MIM
from src.data_sources.mosaiq import Mosaiq
from src.backup_systems.orthanc import Orthanc
# import requests.exceptions  # No longer needed if Orthanc uploader uses DICOM


backup_cli_logger = logging.getLogger('src.cli.backup')


class TestBackupMainFunction(unittest.TestCase):
    """Tests for the main() entry point of backup.py."""

    def setUp(self):
        self.addCleanup(patch.stopall)

    @patch('src.cli.backup.backup_data') 
    def test_main_calls_backup_data_and_exits_success(self, mock_backup_data_func):
        test_args = ['backup.py', 'test_env']
        with patch.object(sys, 'argv', test_args):
            with self.assertRaises(SystemExit) as cm:
                backup_main()
            self.assertEqual(cm.exception.code, 0)
        mock_backup_data_func.assert_called_once_with('test_env')

    def test_main_missing_environment_arg_exits_argparse_error(self):
        test_args = ['backup.py'] 
        with patch.object(sys, 'argv', test_args), \
             patch('sys.stderr', new_callable=io.StringIO) as mock_stderr:
            with self.assertRaises(SystemExit) as cm:
                backup_main()
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("arguments are required: environment", mock_stderr.getvalue())

    @patch('src.cli.backup.backup_data', side_effect=BackupConfigError("Config Test Error"))
    def test_main_backup_data_raises_backupconfigerror_exits_error(self, mock_backup_data_func):
        test_args = ['backup.py', 'test_env']
        with patch.object(sys, 'argv', test_args), \
             patch('sys.stderr', new_callable=io.StringIO) as mock_stderr:
            with self.assertRaises(SystemExit) as cm:
                backup_main()
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("Error: Config Test Error", mock_stderr.getvalue())
    
    @patch('src.cli.backup.backup_data', side_effect=BackupError("Generic Backup Error"))
    def test_main_backup_data_raises_backuperror_exits_error(self, mock_backup_data_func):
        test_args = ['backup.py', 'test_env']
        with patch.object(sys, 'argv', test_args), \
             patch('sys.stderr', new_callable=io.StringIO) as mock_stderr:
            with self.assertRaises(SystemExit) as cm:
                backup_main()
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("Backup Error: Generic Backup Error", mock_stderr.getvalue())

    @patch('src.cli.backup.backup_data', side_effect=Exception("Unexpected Error"))
    def test_main_backup_data_raises_unexpected_exception_exits_error(self, mock_backup_data_func):
        test_args = ['backup.py', 'test_env']
        with patch.object(sys, 'argv', test_args), \
             patch('sys.stderr', new_callable=io.StringIO) as mock_stderr:
            with self.assertRaises(SystemExit) as cm:
                backup_main()
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("An unexpected critical error occurred: Unexpected Error", mock_stderr.getvalue())


class TestConfigLoadingHelper(unittest.TestCase):
    """Tests for the _load_configurations helper function."""
    def setUp(self):
        self.mock_env_path = "mock_env.toml"
        self.mock_dicom_path = "mock_dicom.toml"
        self.addCleanup(patch.stopall)

    @patch('src.cli.backup.tomllib.load')
    @patch('builtins.open', new_callable=mock_open)
    def test_load_configurations_success(self, mock_file_open, mock_toml_load):
        mock_env_cfg_content = {"test_env": {"source": "ARIA_1"}}
        mock_dicom_cfg_content = {
            "ARIA_1": {"AETitle": "ARIA_AE"},
            "backup_script_ae": {"AETitle": "BACKUP_SCU"},
            "staging_scp_for_mosaiq": {"AETitle": "MOSAIQ_STAGE", "IP": "stage_host", "Port": 11113}
        }
        mock_toml_load.side_effect = [mock_env_cfg_content, mock_dicom_cfg_content]

        env_config, dicom_cfg, source_ae_details, local_ae_config, staging_scp_config = _load_configurations(
            "test_env", self.mock_env_path, self.mock_dicom_path
        )
        self.assertEqual(env_config, mock_env_cfg_content["test_env"])
        self.assertEqual(dicom_cfg, mock_dicom_cfg_content)
        self.assertEqual(source_ae_details, mock_dicom_cfg_content["ARIA_1"])
        self.assertEqual(local_ae_config, mock_dicom_cfg_content["backup_script_ae"])
        self.assertEqual(staging_scp_config, mock_dicom_cfg_content["staging_scp_for_mosaiq"])
        mock_file_open.assert_any_call(self.mock_env_path, 'rb')
        mock_file_open.assert_any_call(self.mock_dicom_path, 'rb')

    @patch('builtins.open', side_effect=FileNotFoundError("File missing"))
    def test_load_configurations_file_not_found_raises_backupconfigerror(self, mock_file_open):
        with self.assertRaisesRegex(BackupConfigError, "Configuration file error: File missing not found."):
            _load_configurations("test_env", "missing_env.toml", "missing_dicom.toml")

    @patch('builtins.open', new_callable=mock_open)
    @patch('src.cli.backup.tomllib.load', side_effect=tomllib.TOMLDecodeError("Bad TOML"))
    def test_load_configurations_toml_decode_error_raises_backupconfigerror(self, mock_toml_load, mock_file_open):
        with self.assertRaisesRegex(BackupConfigError, "TOML decoding error"):
            _load_configurations("test_env", self.mock_env_path, self.mock_dicom_path)

    @patch('src.cli.backup.tomllib.load')
    @patch('builtins.open', new_callable=mock_open)
    def test_load_configurations_env_not_found_raises_backupconfigerror(self, mock_file_open, mock_toml_load):
        mock_toml_load.return_value = {} 
        with self.assertRaisesRegex(BackupConfigError, "Environment 'test_env' not found"):
            _load_configurations("test_env", self.mock_env_path, self.mock_dicom_path)
    
    @patch('src.cli.backup.tomllib.load')
    @patch('builtins.open', new_callable=mock_open)
    def test_load_configurations_missing_source_raises_backupconfigerror(self, mock_file_open, mock_toml_load):
        mock_toml_load.return_value = {"test_env": {}} 
        with self.assertRaisesRegex(BackupConfigError, "No 'source' or 'source1' defined"):
            _load_configurations("test_env", self.mock_env_path, self.mock_dicom_path)

    @patch('src.cli.backup.tomllib.load')
    @patch('builtins.open', new_callable=mock_open)
    def test_load_configurations_missing_source_ae_raises_backupconfigerror(self, mock_file_open, mock_toml_load):
        mock_env_cfg = {"test_env": {"source": "ARIA_NONEXIST"}}
        mock_dicom_cfg = {"ARIA_EXISTS": {}, "backup_script_ae": {"AETitle": "ANY"}} # backup_script_ae must exist
        mock_toml_load.side_effect = [mock_env_cfg, mock_dicom_cfg]
        with self.assertRaisesRegex(BackupConfigError, "AE details for source 'ARIA_NONEXIST' not found"):
            _load_configurations("test_env", self.mock_env_path, self.mock_dicom_path)

    @patch('src.cli.backup.tomllib.load')
    @patch('builtins.open', new_callable=mock_open)
    def test_load_configurations_missing_backup_script_ae_raises_error(self, mock_file_open, mock_toml_load):
        mock_env_cfg = {"test_env": {"source": "ARIA_1"}}
        mock_dicom_cfg = {"ARIA_1": {"AETitle": "ARIA_AE"}} # Missing backup_script_ae
        mock_toml_load.side_effect = [mock_env_cfg, mock_dicom_cfg]
        with self.assertRaisesRegex(BackupConfigError, "Local AE configuration 'backup_script_ae' with an 'AETitle' not found"):
            _load_configurations("test_env", self.mock_env_path, self.mock_dicom_path)

    @patch('src.cli.backup.tomllib.load')
    @patch('builtins.open', new_callable=mock_open)
    def test_load_configurations_missing_staging_scp_is_ok(self, mock_file_open, mock_toml_load):
        mock_env_cfg = {"test_env": {"source": "ARIA_1"}}
        mock_dicom_cfg = {"ARIA_1": {"AETitle": "ARIA_AE"}, "backup_script_ae": {"AETitle": "BACKUP_SCU"}}
        mock_toml_load.side_effect = [mock_env_cfg, mock_dicom_cfg]
        
        _, _, _, _, staging_scp_config = _load_configurations(
            "test_env", self.mock_env_path, self.mock_dicom_path
        )
        self.assertIsNone(staging_scp_config)


class TestInitializationAndBuildingHelpers(unittest.TestCase):
    """Tests for initialization and dataset building helper functions."""
    def setUp(self):
        self.addCleanup(patch.stopall)

    @patch('src.cli.backup.ARIA')
    @patch('src.cli.backup.MIM')
    @patch('src.cli.backup.Mosaiq')
    def test_initialize_source_system(self, mock_mosaiq, mock_mim, mock_aria):
        env_cfg_aria = {} 
        _initialize_source_system("ARIA", env_cfg_aria, {})
        mock_aria.assert_called_once()

        _initialize_source_system("MIM", env_cfg_aria, {})
        mock_mim.assert_called_once()

        env_cfg_mosaiq = {"mosaiq_odbc_driver": "TestDriver"}
        _initialize_source_system("Mosaiq", env_cfg_mosaiq, {})
        mock_mosaiq.assert_called_once_with(odbc_driver="TestDriver")

        with self.assertRaisesRegex(BackupConfigError, "Invalid source system specified: FOOBAR"):
            _initialize_source_system("FOOBAR", {}, {})

    @patch('src.cli.backup.Orthanc')
    def test_initialize_orthanc_uploader(self, mock_orthanc):
        env_cfg = {"backup": "ORTHANC_MAIN"}
        dicom_cfg_ok = {"ORTHANC_MAIN": {"AETitle": "BACKUP_AE", "IP": "orthanc.peer", "Port": 104}}
        local_aet = "SCRIPT_AET"
        uploader = _initialize_orthanc_uploader(env_cfg, dicom_cfg_ok, local_aet)
        mock_orthanc.assert_called_with(calling_aet=local_aet, peer_aet="BACKUP_AE", peer_host="orthanc.peer", peer_port=104)
        self.assertIsNotNone(uploader)

        dicom_cfg_no_ip = {"ORTHANC_MAIN": {"AETitle": "BACKUP_AE", "Port": 104}} # IP missing
        with self.assertLogs(backup_cli_logger, level='WARNING') as log_watcher:
            uploader_no_ip = _initialize_orthanc_uploader(env_cfg, dicom_cfg_no_ip, local_aet)
        self.assertIsNone(uploader_no_ip)
        self.assertTrue(any("DICOM AE configuration for backup target 'ORTHANC_MAIN' " in msg for msg in log_watcher.output))

        dicom_cfg_no_backup_key = {} 
        with self.assertLogs(backup_cli_logger, level='WARNING'):
            uploader_no_key = _initialize_orthanc_uploader(env_cfg, dicom_cfg_no_backup_key, local_aet)
        self.assertIsNone(uploader_no_key)

        mock_orthanc.reset_mock()
        env_cfg_no_backup_in_env = {} 
        with self.assertLogs(backup_cli_logger, level='WARNING'):
            uploader_no_env_key = _initialize_orthanc_uploader(env_cfg_no_backup_in_env, dicom_cfg_ok, local_aet)
        self.assertIsNone(uploader_no_env_key)
        mock_orthanc.assert_not_called()


    def test_build_aria_mim_cfind_dataset_with_config(self):
        env_cfg = {
            "dicom_query_level": "PATIENT",
            "dicom_query_keys": {"PatientID": "123*", "Modality": "CT", "PatientName": "Doe^John"}
        }
        ds = _build_aria_mim_cfind_dataset(env_cfg, "test_env_aria_mim_cfind")
        self.assertEqual(ds.QueryRetrieveLevel, "PATIENT")
        self.assertEqual(ds.PatientID, "123*")
        self.assertEqual(ds.Modality, "CT")
        self.assertEqual(ds.PatientName, "Doe^John")
        self.assertTrue(hasattr(ds, "StudyDate")) 
        self.assertEqual(ds.StudyDate, "") 

    def test_build_aria_mim_cfind_dataset_no_config_uses_defaults(self):
        env_cfg = {} 
        with self.assertLogs(backup_cli_logger, level='WARNING') as log_watcher:
            ds = _build_aria_mim_cfind_dataset(env_cfg, "test_env_no_keys_cfind")
        self.assertTrue(any("'dicom_query_keys' is missing" in msg for msg in log_watcher.output))
        self.assertEqual(ds.QueryRetrieveLevel, "SERIES") 
        self.assertEqual(ds.PatientID, "*") 
        self.assertEqual(ds.Modality, "")

    @patch('src.cli.backup.generate_uid')
    def test_build_mosaiq_dataset_from_row_dict_input(self, mock_generate_uid):
        # Ensure enough UIDs are generated for PatientID, Study, Series, SOPInstance
        mock_generate_uid.side_effect = ["STUDY_UID_GEN", "SERIES_UID_GEN", "SOP_UID_GEN"]
        row = {"DB_PatientID": "MOSAIQ1", "DB_Modality": "RTIMAGE", "DB_SOPClassUID": "1.2.3"}
        mapping = {"DB_PatientID": "PatientID", "DB_Modality": "Modality", "DB_SOPClassUID": "SOPClassUID"}
        defaults = {"PatientName": "Unknown"}
        
        ds = _build_mosaiq_dataset_from_row(row, mapping, defaults, 0)
        self.assertEqual(ds.PatientID, "MOSAIQ1")
        self.assertEqual(ds.Modality, "RTIMAGE")
        self.assertEqual(ds.PatientName, "Unknown")
        self.assertEqual(ds.SOPClassUID, "1.2.3") 
        self.assertEqual(ds.SOPInstanceUID, "SOP_UID_GEN")
        self.assertTrue(isinstance(ds.file_meta, FileMetaDataset))
        self.assertEqual(ds.file_meta.MediaStorageSOPClassUID, "1.2.3")

    def test_build_mosaiq_dataset_from_row_tuple_input_logs_warning(self):
        row_tuple = ("TuplePatientData",) # Example tuple
        with self.assertLogs(backup_cli_logger, level='WARNING') as log_watcher:
            ds = _build_mosaiq_dataset_from_row(row_tuple, {}, {"SOPClassUID": "1.2.3"}, 0)
        self.assertTrue(any("Mosaiq record_data_row (row 0) is a tuple." in msg for msg in log_watcher.output))
        self.assertEqual(ds.PatientID, "MOSAIQ_PAT_1") # Updated placeholder
        self.assertEqual(ds.SOPClassUID, "1.2.3") 


@patch('src.cli.backup._initialize_orthanc_uploader')
@patch('src.cli.backup._build_aria_mim_cfind_dataset')
class TestAriaMimBackupWorkflow(unittest.TestCase): # Renamed
    """Tests for the _handle_aria_mim_backup orchestrator function."""
    def setUp(self):
        self.mock_source_instance = MagicMock(spec=ARIA)
        self.env_name = "ARIA_WORKFLOW_ENV"
        self.env_config = {"source": "ARIA_SOURCE_KEY", "backup": "ARIA_BACKUP_DEST_KEY", "max_uids_per_run": 2}
        self.source_ae_details = {"AETitle": "ARIA_SCP_DETAILS"}
        self.dicom_cfg = {"ARIA_BACKUP_DEST_KEY": {"AETitle": "ARIA_BACKUP_AET"}}
        self.local_ae_config = {"AETitle": "LOCAL_CALLING_AET"}
        self.mock_orthanc_uploader_instance = MagicMock(spec=Orthanc) # Now a MagicMock
        self.addCleanup(patch.stopall)

    def test_workflow_success(self, mock_build_cfind, mock_init_orthanc): # Renamed
        # mock_init_orthanc is actually for _initialize_orthanc_uploader, not used directly by _handle_aria_mim_backup
        # _handle_aria_mim_backup receives the uploader instance.
        mock_cfind_ds = Dataset()
        mock_cfind_ds.PatientID = "Test*" # Needed for C-MOVE identifier construction
        mock_cfind_ds.StudyInstanceUID = "StudyUID_From_Find"
        mock_cfind_ds.SeriesInstanceUID = "SeriesUID_From_Find"
        mock_build_cfind.return_value = mock_cfind_ds
        
        mock_uids = {"uid1", "uid2", "uid3"} 
        self.mock_source_instance.query.return_value = mock_uids
        self.mock_source_instance.transfer.return_value = True # Simulate C-MOVE success
        self.mock_orthanc_uploader_instance.store.return_value = True # Simulate Orthanc verification success

        _handle_aria_mim_backup(
            self.mock_source_instance, self.env_name, self.env_config, 
            self.source_ae_details, self.dicom_cfg, 
            self.local_ae_config['AETitle'], # Pass local_aet_title
            self.mock_orthanc_uploader_instance
        )
        
        mock_build_cfind.assert_called_once_with(self.env_config, self.env_name)
        self.mock_source_instance.query.assert_called_once_with(mock_cfind_ds, self.source_ae_details)
        self.assertEqual(self.mock_source_instance.transfer.call_count, 2) 
        
        # Check calls to transfer
        expected_transfer_calls = [
            mock_call(
                unittest.mock.ANY, # dataset_to_retrieve
                self.source_ae_details,
                backup_destination_aet="ARIA_BACKUP_AET",
                calling_aet=self.local_ae_config['AETitle']
            )
        ] * 2 # Called twice
        self.mock_source_instance.transfer.assert_has_calls(expected_transfer_calls, any_order=True)
        
        # Check dataset_to_retrieve argument for one of the calls
        first_call_args = self.mock_source_instance.transfer.call_args_list[0][0][0]
        self.assertEqual(first_call_args.QueryRetrieveLevel, "IMAGE")
        self.assertIn(first_call_args.SOPInstanceUID, mock_uids)
        self.assertEqual(first_call_args.PatientID, "Test*")


        self.assertEqual(self.mock_orthanc_uploader_instance.store.call_count, 2)
        self.mock_orthanc_uploader_instance.store.assert_any_call(sop_instance_uid='uid1') # Order might vary
        self.mock_orthanc_uploader_instance.store.assert_any_call(sop_instance_uid='uid2')

    def test_workflow_transfer_fails(self, mock_build_cfind, mock_init_orthanc):
        mock_build_cfind.return_value = Dataset()
        self.mock_source_instance.query.return_value = {"uid1"}
        self.mock_source_instance.transfer.return_value = False # Simulate C-MOVE failure

        _handle_aria_mim_backup(
            self.mock_source_instance, self.env_name, self.env_config, 
            self.source_ae_details, self.dicom_cfg, 
            self.local_ae_config['AETitle'],
            self.mock_orthanc_uploader_instance
        )
        self.mock_source_instance.transfer.assert_called_once()
        self.mock_orthanc_uploader_instance.store.assert_not_called()

    def test_workflow_orthanc_store_fails(self, mock_build_cfind, mock_init_orthanc):
        mock_build_cfind.return_value = Dataset()
        self.mock_source_instance.query.return_value = {"uid1"}
        self.mock_source_instance.transfer.return_value = True 
        self.mock_orthanc_uploader_instance.store.return_value = False # Simulate Orthanc verification failure

        with self.assertLogs(backup_cli_logger, level="WARNING") as log_watcher:
            _handle_aria_mim_backup(
                self.mock_source_instance, self.env_name, self.env_config, 
                self.source_ae_details, self.dicom_cfg, 
                self.local_ae_config['AETitle'],
                self.mock_orthanc_uploader_instance
            )
        self.mock_source_instance.transfer.assert_called_once()
        self.mock_orthanc_uploader_instance.store.assert_called_once_with(sop_instance_uid='uid1')
        self.assertTrue(any("NOT verified in Orthanc backup" in msg for msg in log_watcher.output))


@patch('src.cli.backup.dicom_utils._handle_move_scu') # Added patch
@patch('src.cli.backup._build_mosaiq_dataset_from_row')
class TestMosaiqBackupWorkflow(unittest.TestCase): # Renamed
    """Tests for the _handle_mosaiq_backup orchestrator function."""
    def setUp(self):
        self.mock_source_instance = MagicMock(spec=Mosaiq)
        self.env_name = "MOSAIQ_WORKFLOW_ENV"
        self.sql_query = "SELECT PatientID FROM Patients WHERE Name = 'Doe'"
        self.env_config = {"source": "Mosaiq_AE_CONFIG", "mosaiq_backup_sql_query": self.sql_query, "backup": "MOSAIQ_BACKUP_DEST_KEY"}
        self.source_ae_details = {"db_config": {"server": "db_server"}, "db_column_to_dicom_tag": {"PatientID": "PatientID"}, "dicom_defaults": {}}
        self.dicom_cfg = {"MOSAIQ_BACKUP_DEST_KEY": {"AETitle": "FINAL_MOSAIQ_BACKUP_AET"}}
        self.local_ae_config = {"AETitle": "LOCAL_CALLING_AET"}
        self.staging_scp_config = {"AETitle": "MOSAIQ_STAGE_AE", "IP": "stage.host", "Port": 12345}
        self.mock_orthanc_uploader_instance = MagicMock(spec=Orthanc) # Now a MagicMock
        self.addCleanup(patch.stopall)

    def test_workflow_success(self, mock_build_dataset_helper, mock_dicom_utils_handle_move_scu): # Renamed
        mock_db_rows = [{"PatientID": "P1"}] 
        self.mock_source_instance.query.return_value = mock_db_rows
        
        mock_ds1 = Dataset()
        mock_ds1.SOPInstanceUID = "sop_uid_1"
        mock_ds1.PatientID = "P1" # Ensure these are set for C-MOVE args
        mock_ds1.StudyInstanceUID = "study1"
        mock_ds1.SeriesInstanceUID = "series1"
        mock_build_dataset_helper.return_value = mock_ds1
        
        self.mock_source_instance.transfer.return_value = True # C-STORE to staging success
        mock_dicom_utils_handle_move_scu.return_value = None # C-MOVE from staging success
        self.mock_orthanc_uploader_instance.store.return_value = True # Orthanc verification success

        _handle_mosaiq_backup(
            self.mock_source_instance, self.env_name, self.env_config, 
            self.source_ae_details, self.dicom_cfg,
            self.local_ae_config['AETitle'],
            self.mock_orthanc_uploader_instance,
            self.staging_scp_config
        )
        
        self.mock_source_instance.query.assert_called_once_with(self.sql_query, {"server": "db_server"})
        mock_build_dataset_helper.assert_called_once_with(mock_db_rows[0], {"PatientID": "PatientID"}, {}, 0)
        self.mock_source_instance.transfer.assert_called_once_with(mock_ds1, self.staging_scp_config)
        
        mock_dicom_utils_handle_move_scu.assert_called_once()
        move_args_ns = mock_dicom_utils_handle_move_scu.call_args[0][0]
        self.assertIsInstance(move_args_ns, Namespace)
        self.assertEqual(move_args_ns.aet, self.local_ae_config['AETitle'])
        self.assertEqual(move_args_ns.aec, self.staging_scp_config['AETitle'])
        self.assertEqual(move_args_ns.host, self.staging_scp_config['IP'])
        self.assertEqual(move_args_ns.port, self.staging_scp_config['Port'])
        self.assertEqual(move_args_ns.move_dest_aet, "FINAL_MOSAIQ_BACKUP_AET")
        self.assertEqual(move_args_ns.sop_instance_uid, "sop_uid_1")
        
        self.mock_orthanc_uploader_instance.store.assert_called_once_with(sop_instance_uid="sop_uid_1")

    def test_workflow_cstore_to_staging_fails(self, mock_build_dataset_helper, mock_dicom_utils_handle_move_scu):
        self.mock_source_instance.query.return_value = [{"PatientID": "P1"}]
        mock_build_dataset_helper.return_value = Dataset()
        self.mock_source_instance.transfer.return_value = False # C-STORE to staging fails

        _handle_mosaiq_backup(
            self.mock_source_instance, self.env_name, self.env_config, 
            self.source_ae_details, self.dicom_cfg,
            self.local_ae_config['AETitle'], self.mock_orthanc_uploader_instance, self.staging_scp_config
        )
        self.mock_source_instance.transfer.assert_called_once()
        mock_dicom_utils_handle_move_scu.assert_not_called()
        self.mock_orthanc_uploader_instance.store.assert_not_called()

    def test_workflow_cmove_from_staging_fails(self, mock_build_dataset_helper, mock_dicom_utils_handle_move_scu):
        self.mock_source_instance.query.return_value = [{"PatientID": "P1"}]
        mock_build_dataset_helper.return_value = Dataset()
        self.mock_source_instance.transfer.return_value = True # C-STORE to staging succeeds
        mock_dicom_utils_handle_move_scu.side_effect = DicomOperationError("C-MOVE Staging Failed")

        _handle_mosaiq_backup(
            self.mock_source_instance, self.env_name, self.env_config, 
            self.source_ae_details, self.dicom_cfg,
            self.local_ae_config['AETitle'], self.mock_orthanc_uploader_instance, self.staging_scp_config
        )
        mock_dicom_utils_handle_move_scu.assert_called_once()
        self.mock_orthanc_uploader_instance.store.assert_not_called()

    def test_workflow_missing_staging_config(self, mock_build_dataset_helper, mock_dicom_utils_handle_move_scu):
        with self.assertRaisesRegex(BackupConfigError, "Staging SCP configuration .* is required for Mosaiq backup"):
            _handle_mosaiq_backup(
                self.mock_source_instance, self.env_name, self.env_config, 
                self.source_ae_details, self.dicom_cfg,
                self.local_ae_config['AETitle'], self.mock_orthanc_uploader_instance, 
                None # staging_scp_config is None
            )
        self.mock_source_instance.query.assert_not_called() # Should fail before query


# Removed TestHandleStoreFunction class

@patch('src.cli.backup._load_configurations')
@patch('src.cli.backup._initialize_source_system')
@patch('src.cli.backup._handle_aria_mim_backup')
@patch('src.cli.backup._handle_mosaiq_backup')
@patch('src.cli.backup._initialize_orthanc_uploader') # Added patch for this
class TestBackupDataOrchestrator(unittest.TestCase):
    """High-level tests for the backup_data orchestrator function."""
    def setUp(self):
        self.env_name = "MY_ENV"
        self.mock_env_config = {"source": "ARIA"} 
        self.mock_dicom_cfg = {}
        self.mock_source_ae_details = {}
        self.mock_local_ae_config = {"AETitle": "SCRIPT_AE"}
        self.mock_staging_scp_config = None # Default to None
        self.mock_orthanc_uploader_instance = MagicMock(spec=Orthanc)
        self.addCleanup(patch.stopall)

    def test_backup_data_calls_aria_mim_handler_for_aria_source(
        self, mock_init_orthanc, mock_mosaiq_handler, mock_aria_mim_handler, mock_init_source, mock_load_configs
    ):
        mock_load_configs.return_value = (
            self.mock_env_config, self.mock_dicom_cfg, self.mock_source_ae_details, 
            self.mock_local_ae_config, self.mock_staging_scp_config
        )
        mock_aria_instance = MagicMock(spec=ARIA)
        mock_init_source.return_value = mock_aria_instance
        mock_init_orthanc.return_value = self.mock_orthanc_uploader_instance # mock uploader init
        
        backup_data(self.env_name)
        
        mock_load_configs.assert_called_once_with(self.env_name, ENVIRONMENTS_CONFIG_PATH, DICOM_CONFIG_PATH)
        mock_init_source.assert_called_once_with("ARIA", self.mock_env_config, self.mock_source_ae_details)
        mock_init_orthanc.assert_called_once_with(self.mock_env_config, self.mock_dicom_cfg, self.mock_local_ae_config['AETitle'])
        
        mock_aria_mim_handler.assert_called_once()
        call_args_tuple = mock_aria_mim_handler.call_args[0]
        self.assertEqual(call_args_tuple[0], mock_aria_instance)
        self.assertEqual(call_args_tuple[4], self.mock_local_ae_config['AETitle']) # local_aet_title
        self.assertEqual(call_args_tuple[5], self.mock_orthanc_uploader_instance) # orthanc_uploader
        mock_mosaiq_handler.assert_not_called()

    def test_backup_data_calls_mosaiq_handler_for_mosaiq_source(
        self, mock_init_orthanc, mock_mosaiq_handler, mock_aria_mim_handler, mock_init_source, mock_load_configs
    ):
        mosaiq_env_config = {"source": "Mosaiq"}
        mock_staging_config = {"AETitle": "STAGE_AE"} # Mosaiq needs staging
        mock_load_configs.return_value = (
            mosaiq_env_config, self.mock_dicom_cfg, self.mock_source_ae_details,
            self.mock_local_ae_config, mock_staging_config
        )
        mock_mosaiq_instance = MagicMock(spec=Mosaiq)
        mock_init_source.return_value = mock_mosaiq_instance
        mock_init_orthanc.return_value = self.mock_orthanc_uploader_instance
        
        backup_data(self.env_name)
        
        mock_init_source.assert_called_once_with("Mosaiq", mosaiq_env_config, self.mock_source_ae_details)
        mock_init_orthanc.assert_called_once_with(mosaiq_env_config, self.mock_dicom_cfg, self.mock_local_ae_config['AETitle'])
        
        mock_mosaiq_handler.assert_called_once()
        call_args_tuple = mock_mosaiq_handler.call_args[0]
        self.assertEqual(call_args_tuple[0], mock_mosaiq_instance)
        self.assertEqual(call_args_tuple[4], self.mock_local_ae_config['AETitle']) # local_aet_title
        self.assertEqual(call_args_tuple[5], self.mock_orthanc_uploader_instance) # orthanc_uploader
        self.assertEqual(call_args_tuple[6], mock_staging_config) # staging_scp_config
        mock_aria_mim_handler.assert_not_called()

    def test_backup_data_re_raises_config_error_from_load(
        self, mock_init_orthanc, mock_mosaiq_handler, mock_aria_mim_handler, mock_init_source, mock_load_configs
    ):
        mock_load_configs.side_effect = BackupConfigError("Config load fail")
        with self.assertRaisesRegex(BackupConfigError, "Config load fail"):
            backup_data(self.env_name)
        mock_init_source.assert_not_called() 

    def test_backup_data_re_raises_error_from_source_init(
        self, mock_init_orthanc, mock_mosaiq_handler, mock_aria_mim_handler, mock_init_source, mock_load_configs
    ):
        mock_load_configs.return_value = (
            self.mock_env_config, self.mock_dicom_cfg, self.mock_source_ae_details,
            self.mock_local_ae_config, self.mock_staging_scp_config
        )
        mock_init_source.side_effect = BackupConfigError("Source init fail")
        with self.assertRaisesRegex(BackupConfigError, "Source init fail"):
            backup_data(self.env_name)
        mock_aria_mim_handler.assert_not_called()
        mock_mosaiq_handler.assert_not_called()


if __name__ == '__main__':
    unittest.main()
