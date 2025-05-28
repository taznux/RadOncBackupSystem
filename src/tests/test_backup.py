import unittest
from unittest.mock import patch, MagicMock, mock_open, call as mock_call
import argparse
import tomllib
import os
import logging
import io 
import functools

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from src.cli.backup import (
    backup_data, 
    main as backup_main, 
    handle_store,
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

from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid, UID # Added UID for type check
from pynetdicom import evt
from src.data_sources.aria import ARIA
from src.data_sources.mim import MIM
from src.data_sources.mosaiq import Mosaiq
from src.backup_systems.orthanc import Orthanc
import requests.exceptions 

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
        mock_dicom_cfg_content = {"ARIA_1": {"AETitle": "ARIA_AE"}}
        mock_toml_load.side_effect = [mock_env_cfg_content, mock_dicom_cfg_content]

        env_config, dicom_cfg, source_ae_details = _load_configurations(
            "test_env", self.mock_env_path, self.mock_dicom_path
        )
        self.assertEqual(env_config, mock_env_cfg_content["test_env"])
        self.assertEqual(dicom_cfg, mock_dicom_cfg_content)
        self.assertEqual(source_ae_details, mock_dicom_cfg_content["ARIA_1"])
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
        mock_toml_load.return_value = {"test_env": {}} # Env exists but no source
        with self.assertRaisesRegex(BackupConfigError, "No 'source' or 'source1' defined"):
            _load_configurations("test_env", self.mock_env_path, self.mock_dicom_path)

    @patch('src.cli.backup.tomllib.load')
    @patch('builtins.open', new_callable=mock_open)
    def test_load_configurations_missing_source_ae_raises_backupconfigerror(self, mock_file_open, mock_toml_load):
        mock_env_cfg = {"test_env": {"source": "ARIA_NONEXIST"}}
        mock_dicom_cfg = {"ARIA_EXISTS": {}}
        mock_toml_load.side_effect = [mock_env_cfg, mock_dicom_cfg]
        with self.assertRaisesRegex(BackupConfigError, "AE details for source 'ARIA_NONEXIST' not found"):
            _load_configurations("test_env", self.mock_env_path, self.mock_dicom_path)


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
        dicom_cfg_ok = {"ORTHANC_MAIN": {"URL": "http://orthanc.test"}}
        uploader = _initialize_orthanc_uploader(env_cfg, dicom_cfg_ok)
        mock_orthanc.assert_called_with(orthanc_url="http://orthanc.test")
        self.assertIsNotNone(uploader)

        dicom_cfg_no_url = {"ORTHANC_MAIN": {}} # URL missing
        with self.assertLogs(backup_cli_logger, level='WARNING') as log_watcher:
            uploader_no_url = _initialize_orthanc_uploader(env_cfg, dicom_cfg_no_url)
        self.assertIsNone(uploader_no_url)
        self.assertTrue(any("Orthanc URL for backup target 'ORTHANC_MAIN' not found" in msg for msg in log_watcher.output))

        dicom_cfg_no_backup_key = {} # Orthanc config key missing
        with self.assertLogs(backup_cli_logger, level='WARNING'):
            uploader_no_key = _initialize_orthanc_uploader(env_cfg, dicom_cfg_no_backup_key)
        self.assertIsNone(uploader_no_key)

        mock_orthanc.reset_mock()
        env_cfg_no_backup_in_env = {} # 'backup' key missing in env_config
        with self.assertLogs(backup_cli_logger, level='WARNING'):
            uploader_no_env_key = _initialize_orthanc_uploader(env_cfg_no_backup_in_env, dicom_cfg_ok)
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
        self.assertTrue(hasattr(ds, "StudyDate")) # Default added
        self.assertEqual(ds.StudyDate, "") 

    def test_build_aria_mim_cfind_dataset_no_config_uses_defaults(self):
        env_cfg = {} # No dicom_query_keys
        with self.assertLogs(backup_cli_logger, level='WARNING') as log_watcher:
            ds = _build_aria_mim_cfind_dataset(env_cfg, "test_env_no_keys_cfind")
        self.assertTrue(any("'dicom_query_keys' is missing" in msg for msg in log_watcher.output))
        self.assertEqual(ds.QueryRetrieveLevel, "SERIES") 
        self.assertEqual(ds.PatientID, "*") 
        self.assertEqual(ds.Modality, "")

    @patch('src.cli.backup.generate_uid')
    def test_build_mosaiq_dataset_from_row_dict_input(self, mock_generate_uid):
        mock_generate_uid.side_effect = ["SOP_UID_GEN", "SERIES_UID_GEN", "STUDY_UID_GEN"]
        row = {"DB_PatientID": "MOSAIQ1", "DB_Modality": "RTIMAGE", "DB_SOPClassUID": "1.2.3"}
        mapping = {"DB_PatientID": "PatientID", "DB_Modality": "Modality", "DB_SOPClassUID": "SOPClassUID"}
        defaults = {"PatientName": "Unknown"}
        
        ds = _build_mosaiq_dataset_from_row(row, mapping, defaults, 0)
        self.assertEqual(ds.PatientID, "MOSAIQ1")
        self.assertEqual(ds.Modality, "RTIMAGE")
        self.assertEqual(ds.PatientName, "Unknown")
        self.assertEqual(ds.SOPClassUID, "1.2.3") # From mapping
        self.assertEqual(ds.SOPInstanceUID, "SOP_UID_GEN")
        self.assertTrue(isinstance(ds.file_meta, FileMetaDataset))
        self.assertEqual(ds.file_meta.MediaStorageSOPClassUID, "1.2.3")

    def test_build_mosaiq_dataset_from_row_tuple_input_logs_warning(self):
        row_tuple = ("TuplePatientID",)
        with self.assertLogs(backup_cli_logger, level='WARNING') as log_watcher:
            ds = _build_mosaiq_dataset_from_row(row_tuple, {}, {"SOPClassUID": "1.2.3"}, 0)
        self.assertTrue(any("Mosaiq record_data_row (row 0) is a tuple." in msg for msg in log_watcher.output))
        self.assertEqual(ds.PatientID, "MOSAIQ_REC_1") # Placeholder
        self.assertEqual(ds.SOPClassUID, "1.2.3") # From defaults


@patch('src.cli.backup._initialize_orthanc_uploader')
@patch('src.cli.backup._build_aria_mim_cfind_dataset')
@patch('src.cli.backup.functools.partial') # To check it's called
class TestHandleAriaMimBackupOrchestrator(unittest.TestCase):
    """Tests for the _handle_aria_mim_backup orchestrator function."""
    def setUp(self):
        self.mock_source_instance = MagicMock(spec=ARIA)
        self.env_name = "ARIA_WORKFLOW_ENV"
        self.env_config = {"source": "ARIA_SOURCE_KEY", "max_uids_per_run": 2}
        self.source_ae_details = {"AETitle": "ARIA_SCP_DETAILS"}
        self.dicom_cfg = {} # Passed but _initialize_orthanc_uploader is mocked
        self.local_scp_config = {"AETitle": "LOCAL_SCP_AET", "Port": 12345, "IP": "0.0.0.0"}
        self.mock_orthanc_uploader_instance = MagicMock(spec=Orthanc)
        self.addCleanup(patch.stopall)

    def test_aria_mim_backup_full_flow(self, mock_functools_partial, mock_build_cfind, mock_init_orthanc):
        mock_init_orthanc.return_value = self.mock_orthanc_uploader_instance
        mock_cfind_ds = Dataset()
        mock_cfind_ds.PatientID = "Test*"
        mock_build_cfind.return_value = mock_cfind_ds
        
        mock_uids = {"uid1", "uid2", "uid3"} # 3 UIDs, but max_uids_per_run is 2
        self.mock_source_instance.query.return_value = mock_uids
        
        mock_bound_handler = MagicMock()
        mock_functools_partial.return_value = mock_bound_handler

        _handle_aria_mim_backup(
            self.mock_source_instance, self.env_name, self.env_config, 
            self.source_ae_details, self.dicom_cfg, self.local_scp_config, 
            self.mock_orthanc_uploader_instance
        )
        
        mock_build_cfind.assert_called_once_with(self.env_config, self.env_name)
        self.mock_source_instance.query.assert_called_once_with(mock_cfind_ds, self.source_ae_details)
        mock_functools_partial.assert_called_once_with(handle_store, self.mock_orthanc_uploader_instance)
        self.assertEqual(self.mock_source_instance.transfer.call_count, 2) # Due to max_uids_per_run

    def test_aria_mim_backup_no_orthanc_skips_transfer(self, mock_functools_partial, mock_build_cfind, mock_init_orthanc):
        mock_init_orthanc.return_value = None # Simulate Orthanc not being configured/initialized
        mock_build_cfind.return_value = Dataset() # Query will still run
        self.mock_source_instance.query.return_value = {"uid1"}

        with self.assertLogs(backup_cli_logger, level="ERROR") as log_watcher:
            _handle_aria_mim_backup(
                self.mock_source_instance, self.env_name, self.env_config, 
                self.source_ae_details, self.dicom_cfg, self.local_scp_config, 
                None # Pass None as orthanc_uploader
            )
        self.assertTrue(any("Orthanc uploader not initialized" in msg for msg in log_watcher.output))
        self.mock_source_instance.transfer.assert_not_called()
        mock_functools_partial.assert_not_called() # handle_store binding shouldn't happen if no uploader for transfer


@patch('src.cli.backup._build_mosaiq_dataset_from_row')
class TestHandleMosaiqBackupOrchestrator(unittest.TestCase):
    """Tests for the _handle_mosaiq_backup orchestrator function."""
    def setUp(self):
        self.mock_source_instance = MagicMock(spec=Mosaiq)
        self.env_name = "MOSAIQ_WORKFLOW_ENV"
        self.sql_query = "SELECT PatientID FROM Patients WHERE Name = 'Doe'"
        self.env_config = {"source": "Mosaiq_AE_CONFIG", "mosaiq_backup_sql_query": self.sql_query, "backup": "MOSAIQ_BACKUP_DEST"}
        self.source_ae_details = {"db_config": {"server": "db_server"}, "db_column_to_dicom_tag": {"PatientID": "PatientID"}, "dicom_defaults": {}}
        self.dicom_cfg = {"MOSAIQ_BACKUP_DEST": {"AETitle": "TARGET_AET", "IP": "target_host", "Port": 104}}
        self.addCleanup(patch.stopall)

    def test_mosaiq_backup_full_flow(self, mock_build_dataset_helper):
        mock_db_rows = [{"PatientID": "P1"}, {"PatientID": "P2"}] # Assume query returns list of dicts
        self.mock_source_instance.query.return_value = mock_db_rows
        
        mock_ds1 = Dataset(); mock_ds1.SOPInstanceUID = "sop_uid_1"
        mock_ds2 = Dataset(); mock_ds2.SOPInstanceUID = "sop_uid_2"
        mock_build_dataset_helper.side_effect = [mock_ds1, mock_ds2]

        _handle_mosaiq_backup(
            self.mock_source_instance, self.env_name, self.env_config, 
            self.source_ae_details, self.dicom_cfg
        )
        
        self.mock_source_instance.query.assert_called_once_with(self.sql_query, {"server": "db_server"})
        self.assertEqual(mock_build_dataset_helper.call_count, 2)
        mock_build_dataset_helper.assert_any_call(mock_db_rows[0], {"PatientID": "PatientID"}, {}, 0)
        
        self.assertEqual(self.mock_source_instance.transfer.call_count, 2)
        self.mock_source_instance.transfer.assert_any_call(mock_ds1, self.dicom_cfg["MOSAIQ_BACKUP_DEST"])

    def test_mosaiq_backup_missing_db_config_raises_error(self, mock_build_dataset_helper):
        faulty_source_ae_details = self.source_ae_details.copy()
        del faulty_source_ae_details["db_config"]
        with self.assertRaisesRegex(BackupConfigError, "Database configuration .* not found"):
            _handle_mosaiq_backup(
                self.mock_source_instance, self.env_name, self.env_config, 
                faulty_source_ae_details, self.dicom_cfg
            )
        self.mock_source_instance.query.assert_not_called()


class TestHandleStoreFunction(unittest.TestCase):
    def setUp(self):
        self.mock_dcmwrite_patch = patch('src.cli.backup.dcmwrite')
        self.mock_dcmwrite = self.mock_dcmwrite_patch.start()
        
        self.mock_bytesio_patch = patch('src.cli.backup.io.BytesIO')
        self.mock_bytesio_constructor = self.mock_bytesio_patch.start()
        self.mock_bio_instance = MagicMock(spec=io.BytesIO)
        self.mock_bio_instance.getvalue.return_value = b"test_dicom_bytes_content"
        self.mock_bytesio_constructor.return_value.__enter__.return_value = self.mock_bio_instance

        self.mock_logger_patch = patch('src.cli.backup.logger')
        self.mock_logger = self.mock_logger_patch.start()
        
        self.addCleanup(patch.stopall)

        self.mock_event = MagicMock(spec=evt.Event)
        self.mock_ds = Dataset()
        self.mock_ds.SOPInstanceUID = "1.2.3.HANDLE.STORE"
        self.mock_ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.4" # MR Image Storage
        self.mock_event.context = MagicMock()
        self.mock_event.context.transfer_syntax = [ExplicitVRLittleEndian]
        self.mock_event.dataset = self.mock_ds
        
    def test_handle_store_with_orthanc_success(self):
        mock_orthanc_uploader_arg = MagicMock(spec=Orthanc)
        mock_orthanc_uploader_arg.store.return_value = None 

        status = handle_store(mock_orthanc_uploader_arg, self.mock_event)
        
        self.assertEqual(status, 0x0000)
        self.mock_dcmwrite.assert_called_once_with(self.mock_bio_instance, self.mock_ds, write_like_original=False)
        mock_orthanc_uploader_arg.store.assert_called_once_with(b"test_dicom_bytes_content")
        self.mock_logger.info.assert_any_call(f"Successfully stored SOPInstanceUID {self.mock_ds.SOPInstanceUID} to Orthanc.")

    def test_handle_store_with_orthanc_failure_logs_error(self):
        mock_orthanc_uploader_arg = MagicMock(spec=Orthanc)
        mock_orthanc_uploader_arg.store.side_effect = requests.exceptions.Timeout("Orthanc timeout")

        status = handle_store(mock_orthanc_uploader_arg, self.mock_event)
        
        self.assertEqual(status, 0x0000) # Still DICOM success
        mock_orthanc_uploader_arg.store.assert_called_once()
        self.mock_logger.error.assert_any_call(
            f"Failed to store SOPInstanceUID {self.mock_ds.SOPInstanceUID} to Orthanc: Orthanc timeout",
            exc_info=True
        )

    def test_handle_store_orthanc_uploader_is_none_logs_warning(self):
        status = handle_store(None, self.mock_event) # Pass None for orthanc_uploader
        
        self.assertEqual(status, 0x0000)
        self.mock_dcmwrite.assert_not_called() # Should not attempt to write bytes
        self.mock_logger.warning.assert_any_call(
            f"Orthanc uploader not configured/provided. SOPInstanceUID {self.mock_ds.SOPInstanceUID} received but not backed up to Orthanc."
        )


@patch('src.cli.backup._load_configurations')
@patch('src.cli.backup._initialize_source_system')
@patch('src.cli.backup._handle_aria_mim_backup')
@patch('src.cli.backup._handle_mosaiq_backup')
class TestBackupDataOrchestrator(unittest.TestCase):
    """High-level tests for the backup_data orchestrator function."""
    def setUp(self):
        self.env_name = "MY_ENV"
        self.mock_env_config = {"source": "ARIA"} # Example
        self.mock_dicom_cfg = {}
        self.mock_source_ae_details = {}
        self.addCleanup(patch.stopall)

    def test_backup_data_calls_aria_mim_handler_for_aria_source(
        self, mock_mosaiq_handler, mock_aria_mim_handler, mock_init_source, mock_load_configs
    ):
        mock_load_configs.return_value = (self.mock_env_config, self.mock_dicom_cfg, self.mock_source_ae_details)
        mock_aria_instance = MagicMock(spec=ARIA)
        mock_init_source.return_value = mock_aria_instance
        
        backup_data(self.env_name)
        
        mock_load_configs.assert_called_once_with(self.env_name, ENVIRONMENTS_CONFIG_PATH, DICOM_CONFIG_PATH)
        mock_init_source.assert_called_once_with("ARIA", self.mock_env_config, self.mock_source_ae_details)
        mock_aria_mim_handler.assert_called_once()
        # More detailed assertions on args passed to _handle_aria_mim_backup can be added here
        self.assertEqual(mock_aria_mim_handler.call_args[0][0], mock_aria_instance)
        mock_mosaiq_handler.assert_not_called()

    def test_backup_data_calls_mosaiq_handler_for_mosaiq_source(
        self, mock_mosaiq_handler, mock_aria_mim_handler, mock_init_source, mock_load_configs
    ):
        mosaiq_env_config = {"source": "Mosaiq"}
        mock_load_configs.return_value = (mosaiq_env_config, self.mock_dicom_cfg, self.mock_source_ae_details)
        mock_mosaiq_instance = MagicMock(spec=Mosaiq)
        mock_init_source.return_value = mock_mosaiq_instance
        
        backup_data(self.env_name)
        
        mock_init_source.assert_called_once_with("Mosaiq", mosaiq_env_config, self.mock_source_ae_details)
        mock_mosaiq_handler.assert_called_once()
        self.assertEqual(mock_mosaiq_handler.call_args[0][0], mock_mosaiq_instance)
        mock_aria_mim_handler.assert_not_called()

    def test_backup_data_re_raises_config_error_from_load(
        self, mock_mosaiq_handler, mock_aria_mim_handler, mock_init_source, mock_load_configs
    ):
        mock_load_configs.side_effect = BackupConfigError("Config load fail")
        with self.assertRaisesRegex(BackupConfigError, "Config load fail"):
            backup_data(self.env_name)
        mock_init_source.assert_not_called() # Should not proceed

    def test_backup_data_re_raises_error_from_source_init(
        self, mock_mosaiq_handler, mock_aria_mim_handler, mock_init_source, mock_load_configs
    ):
        mock_load_configs.return_value = (self.mock_env_config, self.mock_dicom_cfg, self.mock_source_ae_details)
        mock_init_source.side_effect = BackupConfigError("Source init fail")
        with self.assertRaisesRegex(BackupConfigError, "Source init fail"):
            backup_data(self.env_name)
        mock_aria_mim_handler.assert_not_called()
        mock_mosaiq_handler.assert_not_called()


if __name__ == '__main__':
    unittest.main()
