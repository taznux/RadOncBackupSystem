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
        test_args = ['backup.py', 'UCLA', 'ARIA_1'] # Updated
        with patch.object(sys, 'argv', test_args):
            with self.assertRaises(SystemExit) as cm:
                backup_main()
            self.assertEqual(cm.exception.code, 0)
        mock_backup_data_func.assert_called_once_with('UCLA', 'ARIA_1') # Updated

    def test_main_missing_environment_arg_exits_argparse_error(self):
        test_args = ['backup.py'] 
        with patch.object(sys, 'argv', test_args), \
             patch('sys.stderr', new_callable=io.StringIO) as mock_stderr:
            with self.assertRaises(SystemExit) as cm:
                backup_main()
        self.assertEqual(cm.exception.code, 2)
        # ArgumentParser error messages can vary slightly, check for key part
        self.assertIn("the following arguments are required: environment_name", mock_stderr.getvalue())

    @patch('src.cli.backup.backup_data', side_effect=BackupConfigError("Config Test Error"))
    def test_main_backup_data_raises_backupconfigerror_exits_error(self, mock_backup_data_func):
        test_args = ['backup.py', 'UCLA', 'ARIA_1'] # Updated
        with patch.object(sys, 'argv', test_args), \
             patch('sys.stderr', new_callable=io.StringIO) as mock_stderr:
            with self.assertRaises(SystemExit) as cm:
                backup_main()
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("Error: Config Test Error", mock_stderr.getvalue())
    
    @patch('src.cli.backup.backup_data', side_effect=BackupError("Generic Backup Error"))
    def test_main_backup_data_raises_backuperror_exits_error(self, mock_backup_data_func):
        test_args = ['backup.py', 'UCLA', 'ARIA_1'] # Updated
        with patch.object(sys, 'argv', test_args), \
             patch('sys.stderr', new_callable=io.StringIO) as mock_stderr:
            with self.assertRaises(SystemExit) as cm:
                backup_main()
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("Backup Error: Generic Backup Error", mock_stderr.getvalue())

    @patch('src.cli.backup.backup_data', side_effect=Exception("Unexpected Error"))
    def test_main_backup_data_raises_unexpected_exception_exits_error(self, mock_backup_data_func):
        test_args = ['backup.py', 'UCLA', 'ARIA_1'] # Updated
        with patch.object(sys, 'argv', test_args), \
             patch('sys.stderr', new_callable=io.StringIO) as mock_stderr:
            with self.assertRaises(SystemExit) as cm:
                backup_main()
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("An unexpected critical error occurred: Unexpected Error", mock_stderr.getvalue())


class TestLoadEnvironmentBlock(unittest.TestCase): # Renamed
    """Tests for the _load_configurations helper function (loading environment block)."""
    def setUp(self):
        self.mock_env_path = "mock_environments.toml"
        # self.mock_dicom_path removed
        self.addCleanup(patch.stopall)

    @patch('src.cli.backup.tomllib.load')
    @patch('builtins.open', new_callable=mock_open)
    def test_load_environment_success(self, mock_file_open, mock_toml_load): # Renamed
        mock_environments_content = {
            "UCLA": {"description": "UCLA Env", "default_source": "ARIA"},
            "TJU": {"description": "TJU Env"}
        }
        mock_toml_load.return_value = mock_environments_content

        env_block = _load_configurations("UCLA", self.mock_env_path) # Updated call
        
        self.assertEqual(env_block, {"description": "UCLA Env", "default_source": "ARIA"})
        mock_file_open.assert_called_once_with(self.mock_env_path, 'rb')
        mock_toml_load.assert_called_once_with(mock_file_open.return_value.__enter__.return_value)


    @patch('builtins.open', side_effect=FileNotFoundError("Environments file missing"))
    def test_load_environments_file_not_found(self, mock_file_open): # Renamed & Updated
        with self.assertRaisesRegex(BackupConfigError, "Environments configuration file error: Environments file missing not found."):
            _load_configurations("UCLA", "missing_env.toml")

    @patch('builtins.open', new_callable=mock_open)
    @patch('src.cli.backup.tomllib.load', side_effect=tomllib.TOMLDecodeError("Bad TOML in environments"))
    def test_load_invalid_toml_format(self, mock_toml_load, mock_file_open): # Renamed & Updated
        with self.assertRaisesRegex(BackupConfigError, "TOML decoding error in environments configuration file"):
            _load_configurations("UCLA", self.mock_env_path)

    @patch('src.cli.backup.tomllib.load')
    @patch('builtins.open', new_callable=mock_open)
    def test_load_missing_environment_raises_error(self, mock_file_open, mock_toml_load): # Renamed
        mock_toml_load.return_value = {"UCLA": {"description": "Exists"}} 
        with self.assertRaisesRegex(BackupConfigError, "Environment 'NONEXISTENT_ENV' not found"):
            _load_configurations("NONEXISTENT_ENV", self.mock_env_path)


class TestInitializationAndBuildingHelpers(unittest.TestCase):
    """Tests for initialization and dataset building helper functions."""
    def setUp(self):
        self.addCleanup(patch.stopall)

    @patch('src.cli.backup.ARIA')
    @patch('src.cli.backup.MIM')
    @patch('src.cli.backup.Mosaiq')
    def test_initialize_source_system(self, mock_mosaiq, mock_mim, mock_aria):
        # Test with 'aria' type
        aria_config = {"aet": "A", "ip": "H", "port": 104, "type": "aria"}
        _initialize_source_system("aria", aria_config)
        mock_aria.assert_called_once()

        # Test with 'mim' type
        mim_config = {"aet": "M", "ip": "H2", "port": 105, "type": "mim"}
        _initialize_source_system("mim", mim_config)
        mock_mim.assert_called_once()

        # Test with 'mosaiq' type
        mosaiq_config = {"type": "mosaiq", "odbc_driver": "TestDriver", "db_server": "db_s"}
        _initialize_source_system("mosaiq", mosaiq_config)
        mock_mosaiq.assert_called_once_with(odbc_driver="TestDriver")

        with self.assertRaisesRegex(BackupConfigError, "Invalid source system type specified: foobar"): # Lowercase to match code
            _initialize_source_system("foobar", {})

    @patch('src.cli.backup.Orthanc')
    def test_initialize_orthanc_uploader(self, mock_orthanc):
        backup_target_config_ok = {"aet": "BACKUP_AE", "ip": "orthanc.peer", "port": 104, "type": "orthanc"}
        local_aet = "SCRIPT_AET"
        uploader = _initialize_orthanc_uploader(backup_target_config_ok, local_aet)
        mock_orthanc.assert_called_with(calling_aet=local_aet, peer_aet="BACKUP_AE", peer_host="orthanc.peer", peer_port=104)
        self.assertIsNotNone(uploader)

        # Test with None config
        uploader_none_config = _initialize_orthanc_uploader(None, local_aet)
        self.assertIsNone(uploader_none_config)
        
        # Test with missing key
        backup_target_missing_key = {"aet": "BACKUP_AE", "ip": "orthanc.peer"} # Port missing
        with self.assertLogs(backup_cli_logger, level='WARNING') as log_watcher:
            uploader_missing_key = _initialize_orthanc_uploader(backup_target_missing_key, local_aet)
        self.assertIsNone(uploader_missing_key)
        self.assertTrue(any("DICOM AE configuration for backup target 'BACKUP_AE' " in msg for msg in log_watcher.output))


    def test_build_aria_mim_cfind_dataset_with_config(self):
        source_config = {
            "dicom_query_level": "PATIENT",
            "dicom_query_keys": {"PatientID": "123*", "Modality": "CT", "PatientName": "Doe^John"}
        }
        env_settings = {} # No fallback settings needed for this test part
        ds = _build_aria_mim_cfind_dataset(source_config, env_settings)
        self.assertEqual(ds.QueryRetrieveLevel, "PATIENT")
        self.assertEqual(ds.PatientID, "123*")
        self.assertEqual(ds.Modality, "CT")
        self.assertEqual(ds.PatientName, "Doe^John")
        self.assertTrue(hasattr(ds, "StudyDate")) 
        self.assertEqual(ds.StudyDate, "") 

    def test_build_aria_mim_cfind_dataset_no_config_uses_defaults(self):
        source_config = {} 
        env_settings = {}
        with self.assertLogs(backup_cli_logger, level='WARNING') as log_watcher:
            ds = _build_aria_mim_cfind_dataset(source_config, env_settings)
        self.assertTrue(any("No 'dicom_query_keys' found" in msg for msg in log_watcher.output))
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
        self.source_config = {"aet": "ARIA_SCP_AE", "ip": "aria.host", "port": 104, "type": "aria"}
        self.backup_target_config = {"aet": "BACKUP_TARGET_AE", "ip": "backup.host", "port": 104}
        self.local_aet_title = "SCRIPT_SCU_AET"
        self.env_settings = {"max_uids_per_run": 2}
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
            self.mock_source_instance, self.env_name, 
            self.source_config, 
            self.backup_target_config, 
            self.local_aet_title, 
            self.mock_orthanc_uploader_instance
        )
        
        mock_build_cfind.assert_called_once_with(self.source_config, self.env_settings)
        self.mock_source_instance.query.assert_called_once_with(mock_cfind_ds, self.source_config)
        self.assertEqual(self.mock_source_instance.transfer.call_count, 2) 
        
        # Check calls to transfer
        expected_transfer_calls = [
            mock_call(
                unittest.mock.ANY, # dataset_to_retrieve
                self.source_config,
                backup_destination_aet=self.backup_target_config['aet'],
                calling_aet=self.local_aet_title
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
            self.mock_source_instance, self.env_name, 
            self.source_config, self.backup_target_config, 
            self.local_aet_title,
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
                self.mock_source_instance, self.env_name, 
                self.source_config, self.backup_target_config, 
                self.local_aet_title,
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
        self.source_config = {
            "type": "mosaiq", "db_server": "db.host", "db_database": "db_name", 
            "db_username": "user", "db_password": "pw", 
            "db_column_to_dicom_tag": {"PatientID_DB": "PatientID"}, 
            "dicom_defaults": {"Modality": "OT"}
        }
        self.backup_target_config = {"aet": "FINAL_BACKUP_AE", "ip": "final.host", "port": 104}
        self.staging_scp_config = {"aet": "STAGE_AE", "ip": "stage.host", "port": 113}
        self.local_aet_title = "SCRIPT_MOSAIQ_SCU"
        self.env_settings = {"mosaiq_backup_sql_query": "SELECT PatientID_DB FROM Treatments", "max_uids_per_run": 1}
        self.mock_orthanc_uploader_instance = MagicMock(spec=Orthanc) # Now a MagicMock
        self.addCleanup(patch.stopall)

    def test_workflow_success(self, mock_build_dataset_helper, mock_dicom_utils_handle_move_scu): # Renamed
        mock_db_rows = [{"PatientID_DB": "P1_DB"}] 
        self.mock_source_instance.query.return_value = mock_db_rows
        
        mock_ds1 = Dataset()
        mock_ds1.SOPInstanceUID = "sop_uid_1"
        mock_ds1.PatientID = "P1_Mapped" 
        mock_ds1.StudyInstanceUID = "study1"
        mock_ds1.SeriesInstanceUID = "series1"
        mock_build_dataset_helper.return_value = mock_ds1
        
        self.mock_source_instance.transfer.return_value = True 
        mock_dicom_utils_handle_move_scu.return_value = None 
        self.mock_orthanc_uploader_instance.store.return_value = True 

        _handle_mosaiq_backup(
            self.mock_source_instance, self.env_name, 
            self.source_config, 
            self.backup_target_config,
            self.staging_scp_config,
            self.local_aet_title,
            self.mock_orthanc_uploader_instance,
            self.staging_scp_config
        )
        
        expected_db_config = {
            "server": "db.host", "database": "db_name", 
            "username": "user", "password": "pw"
        }
        self.mock_source_instance.query.assert_called_once_with(self.env_settings["mosaiq_backup_sql_query"], expected_db_config)
        mock_build_dataset_helper.assert_called_once_with(
            mock_db_rows[0], 
            self.source_config["db_column_to_dicom_tag"], 
            self.source_config["dicom_defaults"], 
            0
        )
        self.mock_source_instance.transfer.assert_called_once_with(mock_ds1, self.staging_scp_config)
        
        mock_dicom_utils_handle_move_scu.assert_called_once()
        move_args_ns = mock_dicom_utils_handle_move_scu.call_args[0][0]
        self.assertIsInstance(move_args_ns, Namespace)
        self.assertEqual(move_args_ns.aet, self.local_aet_title)
        self.assertEqual(move_args_ns.aec, self.staging_scp_config['aet'])
        self.assertEqual(move_args_ns.host, self.staging_scp_config['ip'])
        self.assertEqual(move_args_ns.port, self.staging_scp_config['port'])
        self.assertEqual(move_args_ns.move_dest_aet, self.backup_target_config['aet'])
        self.assertEqual(move_args_ns.sop_instance_uid, "sop_uid_1")
        
        self.mock_orthanc_uploader_instance.store.assert_called_once_with(sop_instance_uid="sop_uid_1")

    def test_workflow_cstore_to_staging_fails(self, mock_build_dataset_helper, mock_dicom_utils_handle_move_scu):
        self.mock_source_instance.query.return_value = [{"PatientID": "P1"}]
        mock_build_dataset_helper.return_value = Dataset()
        self.mock_source_instance.transfer.return_value = False # C-STORE to staging fails

        _handle_mosaiq_backup(
            self.mock_source_instance, self.env_name, 
            self.source_config, self.backup_target_config, self.staging_scp_config,
            self.local_aet_title, self.mock_orthanc_uploader_instance, self.env_settings
            self.mock_source_instance, self.env_name, 
            self.source_config, self.backup_target_config, self.staging_scp_config,
            self.local_aet_title, self.mock_orthanc_uploader_instance, self.env_settings
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
                self.mock_source_instance, self.env_name, 
                self.source_config, self.backup_target_config, 
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
        self.mock_ucla_env_block = {
            "description": "UCLA Environment",
            "default_source": "ARIA_UCLA",
            "default_backup": "ORTHANC_UCLA",
            "script_ae": {"aet": "UCLA_SCRIPT_AE"},
            "sources": {
                "ARIA_UCLA": {"type": "aria", "aet": "UCLA_ARIA_SRC", "ip": "host", "port": 104, 
                              "dicom_query_keys": {"Modality": "RTPLAN"}},
                "MOSAIQ_UCLA": {"type": "mosaiq", "db_server": "db.host", "staging_target_alias": "MOSAIQ_UCLA_STAGE",
                                "db_database": "ucla_mosaiq_db", "db_username": "ucla_user", "db_password": "ucla_password"}
            },
            "backup_targets": {
                "ORTHANC_UCLA": {"type": "orthanc", "aet": "UCLA_ORTHANC_BK", "ip": "host", "port": 104},
                "MOSAIQ_UCLA_STAGE": {"type": "dicom_scp", "aet": "UCLA_STAGE_AE", "ip": "host", "port": 113}
            },
            "settings": {"max_uids_per_run": 5, "mosaiq_backup_sql_query": "SELECT UCLA_DATA..."}
        }
        self.mock_orthanc_uploader_instance = MagicMock(spec=Orthanc)
        self.addCleanup(patch.stopall)

    def test_backup_data_calls_aria_mim_handler_for_aria_source(
        self, mock_init_orthanc, mock_mosaiq_handler, mock_aria_mim_handler, mock_init_source, mock_load_configs
    ):
        mock_load_configs.return_value = self.mock_ucla_env_block # Patch to return the whole block
        
        mock_aria_instance = MagicMock(spec=ARIA)
        mock_init_source.return_value = mock_aria_instance
        mock_init_orthanc.return_value = self.mock_orthanc_uploader_instance
        
        backup_data(self.env_name, 'ARIA_UCLA') # Specify source alias
        
        mock_load_configs.assert_called_once_with(self.env_name, ENVIRONMENTS_CONFIG_PATH)
        mock_init_source.assert_called_once_with("aria", self.mock_ucla_env_block['sources']['ARIA_UCLA'])
        mock_init_orthanc.assert_called_once_with(
            self.mock_ucla_env_block['backup_targets']['ORTHANC_UCLA'], 
            self.mock_ucla_env_block['script_ae']['aet']
        )
        
        mock_aria_mim_handler.assert_called_once()
        call_args_tuple = mock_aria_mim_handler.call_args[0]
        self.assertEqual(call_args_tuple[0], mock_aria_instance) # source_instance
        self.assertEqual(call_args_tuple[2], self.mock_ucla_env_block['sources']['ARIA_UCLA']) # source_config
        self.assertEqual(call_args_tuple[3], self.mock_ucla_env_block['backup_targets']['ORTHANC_UCLA']) # backup_target_config
        self.assertEqual(call_args_tuple[4], self.mock_ucla_env_block['script_ae']['aet']) # local_aet_title
        self.assertEqual(call_args_tuple[5], self.mock_orthanc_uploader_instance) # orthanc_uploader
        self.assertEqual(call_args_tuple[6], self.mock_ucla_env_block['settings']) # env_settings
        mock_mosaiq_handler.assert_not_called()

    def test_backup_data_calls_mosaiq_handler_for_mosaiq_source(
        self, mock_init_orthanc, mock_mosaiq_handler, mock_aria_mim_handler, mock_init_source, mock_load_configs
    ):
        mock_load_configs.return_value = self.mock_ucla_env_block
        
        mock_mosaiq_instance = MagicMock(spec=Mosaiq)
        mock_init_source.return_value = mock_mosaiq_instance
        mock_init_orthanc.return_value = self.mock_orthanc_uploader_instance
        
        backup_data(self.env_name, 'MOSAIQ_UCLA') # Specify Mosaiq source alias
        
        mock_init_source.assert_called_once_with("mosaiq", self.mock_ucla_env_block['sources']['MOSAIQ_UCLA'])
        mock_init_orthanc.assert_called_once_with(
            self.mock_ucla_env_block['backup_targets']['ORTHANC_UCLA'], 
            self.mock_ucla_env_block['script_ae']['aet']
        )
        
        mock_mosaiq_handler.assert_called_once()
        call_args_tuple = mock_mosaiq_handler.call_args[0]
        self.assertEqual(call_args_tuple[0], mock_mosaiq_instance) # source_instance
        self.assertEqual(call_args_tuple[2], self.mock_ucla_env_block['sources']['MOSAIQ_UCLA']) # source_config
        self.assertEqual(call_args_tuple[3], self.mock_ucla_env_block['backup_targets']['ORTHANC_UCLA']) # backup_target_config
        self.assertEqual(call_args_tuple[4], self.mock_ucla_env_block['backup_targets']['MOSAIQ_UCLA_STAGE']) # staging_scp_config
        self.assertEqual(call_args_tuple[5], self.mock_ucla_env_block['script_ae']['aet']) # local_aet_title
        self.assertEqual(call_args_tuple[6], self.mock_orthanc_uploader_instance) # orthanc_uploader
        self.assertEqual(call_args_tuple[7], self.mock_ucla_env_block['settings']) # env_settings
        mock_aria_mim_handler.assert_not_called()

    def test_backup_data_re_raises_config_error_from_load(
        self, mock_init_orthanc, mock_mosaiq_handler, mock_aria_mim_handler, mock_init_source, mock_load_configs
    ):
        mock_load_configs.side_effect = BackupConfigError("Config load fail")
        with self.assertRaisesRegex(BackupConfigError, "Config load fail"):
            backup_data(self.env_name, 'ANY_SOURCE') # Pass source alias
        mock_init_source.assert_not_called() 

    def test_backup_data_re_raises_error_from_source_init(
        self, mock_init_orthanc, mock_mosaiq_handler, mock_aria_mim_handler, mock_init_source, mock_load_configs
    ):
        mock_load_configs.return_value = self.mock_ucla_env_block # Assume config loading is fine
        mock_init_source.side_effect = BackupConfigError("Source init fail")
        with self.assertRaisesRegex(BackupConfigError, "Source init fail"):
            backup_data(self.env_name, 'ARIA_UCLA') # Specify source alias
        mock_aria_mim_handler.assert_not_called()
        mock_mosaiq_handler.assert_not_called()


if __name__ == '__main__':
    unittest.main()
