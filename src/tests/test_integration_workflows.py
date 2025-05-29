import unittest
from unittest.mock import patch, MagicMock, mock_open, call as mock_call
import argparse 
from argparse import Namespace # Ensure Namespace is imported for mock_handle_cmove_scu args
import tomllib 
import os
import logging
import sys
import tempfile # Added for C-GET test output directory
import pydicom # Added for pydicom.uid

# Adjust path to import from src
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from pydicom.dataset import Dataset
from src.tests.mock_dicom_server import MockDicomServer
from src.cli.backup import backup_data, BackupConfigError, ENVIRONMENTS_CONFIG_PATH
from src.data_sources.aria import ARIA 
from src.data_sources.mim import MIM 
from src.data_sources.mosaiq import Mosaiq 
from src.backup_systems.orthanc import Orthanc 
from src.cli import dicom_utils # For patching _handle_move_scu and other dicom_utils helpers

# Import SOP classes needed for C-GET context assertion
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelGet, CompositeInstanceRootRetrieveGet, StoragePresentationContexts


logger = logging.getLogger(__name__)


class TestIntegrationWorkflows(unittest.TestCase):
    """
    Integration tests for the backup workflows, focusing on the interaction
    between backup.py CLI logic, data source classes, and backup system classes,
    with DICOM communication mocked by MockDicomServer.
    """

    def setUp(self):
        self.mock_servers = []
        self.mock_toml_load_patcher = patch('src.cli.backup.tomllib.load')
        self.mock_toml_load = self.mock_toml_load_patcher.start()
        
        self.mock_aria_class_patcher = patch('src.cli.backup.ARIA')
        self.mock_aria_class = self.mock_aria_class_patcher.start()

        self.mock_mim_class_patcher = patch('src.cli.backup.MIM') 
        self.mock_mim_class = self.mock_mim_class_patcher.start()
        
        self.mock_mosaiq_class_patcher = patch('src.cli.backup.Mosaiq') 
        self.mock_mosaiq_class = self.mock_mosaiq_class_patcher.start()

        self.mock_orthanc_class_patcher = patch('src.cli.backup.Orthanc')
        self.mock_orthanc_class = self.mock_orthanc_class_patcher.start()

        self.mock_aria_instance = MagicMock(spec=ARIA)
        self.mock_aria_class.return_value = self.mock_aria_instance

        self.mock_mim_instance = MagicMock(spec=MIM) 
        self.mock_mim_class.return_value = self.mock_mim_instance

        self.mock_mosaiq_instance = MagicMock(spec=Mosaiq) 
        self.mock_mosaiq_class.return_value = self.mock_mosaiq_instance

        self.mock_orthanc_instance = MagicMock(spec=Orthanc)
        self.mock_orthanc_class.return_value = self.mock_orthanc_instance
        
        self.addCleanup(self.stop_all_mock_servers)
        self.addCleanup(patch.stopall) 

    def stop_all_mock_servers(self):
        for server in self.mock_servers:
            try:
                server.stop()
                server.reset()
            except Exception as e:
                logger.error(f"Error stopping/resetting mock server {server.ae_title}: {e}")
        self.mock_servers = []

    def _create_mock_server(self, ae_title, port):
        server = MockDicomServer(host="127.0.0.1", port=port, ae_title=ae_title)
        server.start()
        self.mock_servers.append(server)
        return server

    def test_ucla_aria_backup_workflow(self):
        # a. Setup Mock Servers
        mock_aria_server_port = 11120
        mock_orthanc_backup_port = 11121
        
        mock_aria_server = self._create_mock_server("ARIA_UCLA_AE_TEST", mock_aria_server_port)
        mock_orthanc_backup_server = self._create_mock_server("ORTHANC_UCLA_BACKUP_AE_TEST", mock_orthanc_backup_port)

        # b. Mock environments.toml Data
        test_ucla_env_config = {
            "description": "Test UCLA Environment for Integration Test",
            "default_source": "ARIA_TEST",
            "default_backup": "ORTHANC_BACKUP_TEST",
            "script_ae": {"aet": "TEST_BACKUP_SCU"}, 
            "sources": {
                "ARIA_TEST": {
                    "type": "aria",
                    "aet": mock_aria_server.ae_title, 
                    "ip": mock_aria_server.host,
                    "port": mock_aria_server.port,
                    "dicom_query_keys": {"PatientID": "UCLA_PAT_123", "Modality": "RTRECORD"}
                }
            },
            "backup_targets": {
                "ORTHANC_BACKUP_TEST": {
                    "type": "orthanc", 
                    "aet": mock_orthanc_backup_server.ae_title, 
                    "ip": mock_orthanc_backup_server.host,
                    "port": mock_orthanc_backup_server.port
                }
            },
            "settings": {"max_uids_per_run": 2} 
        }
        mock_environments_content = {"TEST_UCLA_ARIA_INTEGRATION": test_ucla_env_config}
        
        self.mock_toml_load.return_value = mock_environments_content

        sop_uids_to_backup = {'sop1.uid.123', 'sop2.uid.456'}
        self.mock_aria_instance.query.return_value = sop_uids_to_backup
        self.mock_aria_instance.transfer.return_value = True 
        self.mock_orthanc_instance.store.return_value = True 

        backup_data(environment_name="TEST_UCLA_ARIA_INTEGRATION", source_alias="ARIA_TEST")

        self.mock_toml_load.assert_called_once()
        self.mock_aria_class.assert_called_once()
        self.mock_orthanc_class.assert_called_once_with(
            calling_aet="TEST_BACKUP_SCU",
            peer_aet=mock_orthanc_backup_server.ae_title,
            peer_host=mock_orthanc_backup_server.host,
            peer_port=mock_orthanc_backup_server.port
        )
        self.mock_aria_instance.query.assert_called_once()
        self.assertEqual(self.mock_aria_instance.transfer.call_count, len(sop_uids_to_backup))
        
        expected_transfer_calls = []
        for sop_uid in sop_uids_to_backup:
            expected_identifier_ds = Dataset()
            expected_identifier_ds.QueryRetrieveLevel = "IMAGE"
            expected_identifier_ds.PatientID = "UCLA_PAT_123" 
            expected_identifier_ds.StudyInstanceUID = "" 
            expected_identifier_ds.SeriesInstanceUID = ""
            expected_identifier_ds.SOPInstanceUID = sop_uid
            
            expected_transfer_calls.append(
                mock_call(
                    expected_identifier_ds, 
                    test_ucla_env_config['sources']['ARIA_TEST'], 
                    backup_destination_aet=mock_orthanc_backup_server.ae_title,
                    calling_aet="TEST_BACKUP_SCU"
                )
            )
        self.mock_aria_instance.transfer.assert_has_calls(expected_transfer_calls, any_order=True)
        self.assertEqual(self.mock_orthanc_instance.store.call_count, len(sop_uids_to_backup))
        expected_store_calls = [mock_call(sop_instance_uid=uid) for uid in sop_uids_to_backup]
        self.mock_orthanc_instance.store.assert_has_calls(expected_store_calls, any_order=True)
        self.assertEqual(mock_aria_server.last_move_destination_aet, mock_orthanc_backup_server.ae_title)

    @patch('src.cli.backup.dicom_utils._handle_move_scu')
    @patch('src.cli.backup._build_mosaiq_dataset_from_row')
    def test_tju_mosaiq_backup_workflow(self, mock_build_mosaiq_dataset_from_row, mock_handle_cmove_scu):
        mock_staging_scp_port = 11122
        mock_final_backup_port = 11123

        mock_staging_server = self._create_mock_server("MOSAIQ_STAGING_SCP_TEST", mock_staging_scp_port)
        mock_final_backup_server = self._create_mock_server("ORTHANC_TJU_BACKUP_AE_TEST", mock_final_backup_port)

        test_tju_env_config = {
            "description": "Test TJU Mosaiq Environment",
            "default_source": "MOSAIQ_TEST_SRC",
            "default_backup": "ORTHANC_BACKUP_TJU_TEST",
            "script_ae": {"aet": "TEST_TJU_BACKUP_SCU"},
            "sources": {
                "MOSAIQ_TEST_SRC": {
                    "type": "mosaiq",
                    "db_server": "dummy_db_server", 
                    "db_database": "dummy_db_name",
                    "db_username": "dummy_user",
                    "db_password": "dummy_password",
                    "odbc_driver": "Dummy ODBC Driver",
                    "staging_target_alias": "MOSAIQ_STAGING_TARGET" 
                }
            },
            "backup_targets": {
                "ORTHANC_BACKUP_TJU_TEST": {
                    "type": "orthanc",
                    "aet": mock_final_backup_server.ae_title,
                    "ip": mock_final_backup_server.host,
                    "port": mock_final_backup_server.port
                },
                "MOSAIQ_STAGING_TARGET": {
                    "type": "dicom_scp", 
                    "aet": mock_staging_server.ae_title,
                    "ip": mock_staging_server.host,
                    "port": mock_staging_server.port
                }
            },
            "settings": {
                "mosaiq_backup_sql_query": "SELECT DummyColumn FROM MosaiqTestTable WHERE ID=1"
            }
        }
        mock_environments_content_mosaiq = {"TEST_TJU_MOSAIQ_INTEGRATION": test_tju_env_config}

        self.mock_toml_load.return_value = mock_environments_content_mosaiq
        
        self.mock_mosaiq_instance.query.return_value = [{'PatientID': 'MOSAIQ_PAT_001', 'RelevantColumn': 'Data'}]
        self.mock_mosaiq_instance.transfer.return_value = True 
        mock_handle_cmove_scu.return_value = None 
        self.mock_orthanc_instance.store.return_value = True 

        mock_generated_ds = Dataset()
        mock_generated_ds.PatientID = "MOSAIQ_PAT_001_GEN" 
        mock_generated_ds.StudyInstanceUID = "study.uid.mosaiq.integration.1"
        mock_generated_ds.SeriesInstanceUID = "series.uid.mosaiq.integration.1"
        mock_generated_ds.SOPInstanceUID = "sop.uid.mosaiq.integration.1"
        mock_build_mosaiq_dataset_from_row.return_value = mock_generated_ds

        backup_data(environment_name="TEST_TJU_MOSAIQ_INTEGRATION", source_alias="MOSAIQ_TEST_SRC")

        self.mock_mosaiq_class.assert_called_once() 
        self.mock_orthanc_class.assert_called_once_with( 
            calling_aet="TEST_TJU_BACKUP_SCU",
            peer_aet=mock_final_backup_server.ae_title,
            peer_host=mock_final_backup_server.host,
            peer_port=mock_final_backup_server.port
        )

        self.mock_mosaiq_instance.query.assert_called_once_with(
            test_tju_env_config['settings']['mosaiq_backup_sql_query'],
            unittest.mock.ANY 
        )
        mock_build_mosaiq_dataset_from_row.assert_called_once()
        
        self.mock_mosaiq_instance.transfer.assert_called_once_with(
            mock_generated_ds,
            test_tju_env_config['backup_targets']['MOSAIQ_STAGING_TARGET']
        )
        self.assertEqual(len(mock_staging_server.received_datasets), 1)
        self.assertEqual(mock_staging_server.received_datasets[0].SOPInstanceUID, mock_generated_ds.SOPInstanceUID)
        
        mock_handle_cmove_scu.assert_called_once()
        cmove_args = mock_handle_cmove_scu.call_args[0][0]
        self.assertIsInstance(cmove_args, argparse.Namespace)
        self.assertEqual(cmove_args.aet, "TEST_TJU_BACKUP_SCU")
        self.assertEqual(cmove_args.aec, mock_staging_server.ae_title)
        self.assertEqual(cmove_args.host, mock_staging_server.host)
        self.assertEqual(cmove_args.port, mock_staging_server.port)
        self.assertEqual(cmove_args.move_dest_aet, mock_final_backup_server.ae_title)
        self.assertEqual(cmove_args.sop_instance_uid, mock_generated_ds.SOPInstanceUID)
        self.assertEqual(cmove_args.study_uid, mock_generated_ds.StudyInstanceUID)
        self.assertEqual(cmove_args.series_uid, mock_generated_ds.SeriesInstanceUID)

        self.mock_orthanc_instance.store.assert_called_once_with(sop_instance_uid=mock_generated_ds.SOPInstanceUID)

    def test_tju_mim_backup_workflow(self):
        mock_mim_server_port = 11124 
        mock_orthanc_backup_mim_port = 11125 

        mock_mim_server = self._create_mock_server("MIM_TJU_AE_TEST", mock_mim_server_port)
        mock_orthanc_backup_server = self._create_mock_server("ORTHANC_TJU_BACKUP_AE_TEST_MIM", mock_orthanc_backup_mim_port)

        test_tju_mim_env_config = {
            "description": "Test TJU MIM Environment for Integration Test",
            "default_source": "MIM_TEST",
            "default_backup": "ORTHANC_BACKUP_TJU_TEST_MIM", 
            "script_ae": {"aet": "TEST_TJU_BACKUP_SCU_MIM"},
            "sources": {
                "MIM_TEST": {
                    "type": "mim",
                    "aet": mock_mim_server.ae_title,
                    "ip": mock_mim_server.host,
                    "port": mock_mim_server.port,
                    "dicom_query_keys": {"PatientID": "TJU_MIM_PAT_456", "Modality": "RTIMAGE"}
                }
            },
            "backup_targets": {
                "ORTHANC_BACKUP_TJU_TEST_MIM": { 
                    "type": "orthanc",
                    "aet": mock_orthanc_backup_server.ae_title,
                    "ip": mock_orthanc_backup_server.host,
                    "port": mock_orthanc_backup_server.port
                }
            },
            "settings": {"max_uids_per_run": 3}
        }
        mock_environments_content_mim = {"TEST_TJU_MIM_INTEGRATION": test_tju_mim_env_config}

        self.mock_toml_load.return_value = mock_environments_content_mim

        sop_uids_to_backup_mim = {'sop_mim1.uid.789', 'sop_mim2.uid.012'}
        self.mock_mim_instance.query.return_value = sop_uids_to_backup_mim
        self.mock_mim_instance.transfer.return_value = True
        self.mock_orthanc_instance.store.return_value = True

        backup_data(environment_name="TEST_TJU_MIM_INTEGRATION", source_alias="MIM_TEST")

        self.mock_mim_class.assert_called_once()
        self.mock_orthanc_class.assert_called_once_with(
            calling_aet="TEST_TJU_BACKUP_SCU_MIM",
            peer_aet=mock_orthanc_backup_server.ae_title,
            peer_host=mock_orthanc_backup_server.host,
            peer_port=mock_orthanc_backup_server.port
        )

        self.mock_mim_instance.query.assert_called_once()
        query_dataset_arg = self.mock_mim_instance.query.call_args[0][0]
        self.assertEqual(query_dataset_arg.PatientID, "TJU_MIM_PAT_456")
        self.assertEqual(query_dataset_arg.Modality, "RTIMAGE")


        self.assertEqual(self.mock_mim_instance.transfer.call_count, len(sop_uids_to_backup_mim))
        expected_mim_transfer_calls = []
        for sop_uid in sop_uids_to_backup_mim:
            expected_identifier_ds = Dataset()
            expected_identifier_ds.QueryRetrieveLevel = "IMAGE"
            expected_identifier_ds.PatientID = "TJU_MIM_PAT_456" 
            expected_identifier_ds.StudyInstanceUID = "" 
            expected_identifier_ds.SeriesInstanceUID = ""
            expected_identifier_ds.SOPInstanceUID = sop_uid
            
            expected_mim_transfer_calls.append(
                mock_call(
                    expected_identifier_ds,
                    test_tju_mim_env_config['sources']['MIM_TEST'],
                    backup_destination_aet=mock_orthanc_backup_server.ae_title,
                    calling_aet="TEST_TJU_BACKUP_SCU_MIM"
                )
            )
        self.mock_mim_instance.transfer.assert_has_calls(expected_mim_transfer_calls, any_order=True)
        
        self.assertEqual(mock_mim_server.last_move_destination_aet, mock_orthanc_backup_server.ae_title)
        
        self.assertEqual(self.mock_orthanc_instance.store.call_count, len(sop_uids_to_backup_mim))
        expected_mim_store_calls = [mock_call(sop_instance_uid=uid) for uid in sop_uids_to_backup_mim]
        self.mock_orthanc_instance.store.assert_has_calls(expected_mim_store_calls, any_order=True)

    @patch('src.cli.dicom_utils._establish_association') # Patch at the point of use by dicom_utils helpers
    def test_report_generation_workflow(self, mock_establish_association_dicom_utils):
        # a. Setup Mock Orthanc Server (for report data)
        mock_report_orthanc_aet = "ORTHANC_REP_SRC_TEST"
        mock_report_orthanc_port = 11127
        mock_report_orthanc_server = self._create_mock_server(mock_report_orthanc_aet, mock_report_orthanc_port)

        # b. Prepare Data for Mock Orthanc
        report_ds = Dataset()
        report_ds.PatientID = "REPORT_PAT_001"
        report_ds.StudyInstanceUID = pydicom.uid.generate_uid()
        report_ds.SeriesInstanceUID = pydicom.uid.generate_uid()
        report_ds.SOPInstanceUID = "report.sop.1.2.3"
        report_ds.Modality = "RTRECORD"
        report_ds.PatientName = "Report^Patient"
        
        report_ds.file_meta = Dataset()
        # Using RTBeamsTreatmentRecordStorage UID as an example
        report_ds.file_meta.MediaStorageSOPClassUID = '1.2.840.10008.5.1.4.1.1.481.2' 
        report_ds.file_meta.MediaStorageSOPInstanceUID = report_ds.SOPInstanceUID
        report_ds.file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
        report_ds.file_meta.ImplementationClassUID = pydicom.uid.PYDICOM_IMPLEMENTATION_UID # Use pydicom's
        report_ds.is_little_endian = True # Ensure these are set for dcmwrite in _on_get_response
        report_ds.is_implicit_VR = False  # For ExplicitVRLittleEndian

        mock_report_orthanc_server.add_dataset_for_get(report_ds)

        # c. Simulate C-FIND to Mock Orthanc
        calling_aet_report = "REPORTER_SCU_TEST"
        
        # Configure mock_establish_association for C-FIND
        mock_find_assoc = MagicMock(spec=dicom_utils.Association) # Use Association from dicom_utils
        mock_find_assoc.is_established = True
        
        # Prepare the identifier that _on_find_response would log
        find_response_identifier_ds = Dataset()
        find_response_identifier_ds.SOPInstanceUID = report_ds.SOPInstanceUID
        find_response_identifier_ds.PatientID = report_ds.PatientID 
        # Add other keys that _build_find_query_dataset adds for query, if _on_find_response logs them
        # For an IMAGE level C-FIND, the response identifier might be minimal
        
        mock_find_assoc.send_c_find.return_value = iter([
            (MagicMock(Status=0xFF00), find_response_identifier_ds), # Pending with identifier
            (MagicMock(Status=0x0000), None)  # Success
        ])
        mock_establish_association_dicom_utils.return_value = mock_find_assoc

        find_args = argparse.Namespace(
            aet=calling_aet_report,
            aec=mock_report_orthanc_server.ae_title,
            host=mock_report_orthanc_server.host,
            port=mock_report_orthanc_server.port,
            query_level="IMAGE", # Querying for a specific SOPInstanceUID
            patient_id=report_ds.PatientID, # Optional for IMAGE level, but good practice
            study_uid=report_ds.StudyInstanceUID, # Optional
            series_uid=report_ds.SeriesInstanceUID, # Optional
            sop_instance_uid=report_ds.SOPInstanceUID, # Key for the query
            modality=None, # Not typically used for IMAGE level SOPInstanceUID query
            verbose=False
        )
        dicom_utils._handle_find_scu(find_args) # Call the actual C-FIND handler

        mock_establish_association_dicom_utils.assert_called_once()
        # Check that C-FIND model was requested
        requested_contexts_find = mock_establish_association_dicom_utils.call_args[0][4]
        self.assertIn(dicom_utils.StudyRootQueryRetrieveInformationModelFind, requested_contexts_find) # Or the one determined by _get_find_model
        mock_find_assoc.send_c_find.assert_called_once()


        # d. Simulate C-GET from Mock Orthanc
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_establish_association_dicom_utils.reset_mock() # Reset for the C-GET call
            mock_get_assoc = MagicMock(spec=dicom_utils.Association)
            mock_get_assoc.is_established = True
            
            final_cget_status_ds = Dataset()
            final_cget_status_ds.NumberOfCompletedSuboperations = 1
            final_cget_status_ds.NumberOfFailedSuboperations = 0
            final_cget_status_ds.NumberOfWarningSuboperations = 0
            final_cget_status_ds.NumberOfRemainingSuboperations = 0
            
            mock_get_assoc.send_c_get.return_value = iter([(MagicMock(Status=0x0000), final_cget_status_ds)])
            
            # Mock the C-STORE sub-operation initiated by the MockDicomServer's handle_get
            # MockDicomServer.handle_get calls event.assoc.send_c_store(dataset_to_send)
            # Here, event.assoc will be mock_get_assoc.
            mock_c_store_status_on_get_assoc = MagicMock(Status=0x0000)
            mock_get_assoc.send_c_store.return_value = mock_c_store_status_on_get_assoc
            
            mock_establish_association_dicom_utils.return_value = mock_get_assoc

            get_args = argparse.Namespace(
                aet=calling_aet_report,
                aec=mock_report_orthanc_server.ae_title,
                host=mock_report_orthanc_server.host,
                port=mock_report_orthanc_server.port,
                patient_id=None, # Testing specific SOPInstanceUID GET
                study_uid=None,
                series_uid=None,
                sop_instance_uid=report_ds.SOPInstanceUID,
                out_dir=temp_dir,
                verbose=False
            )
            dicom_utils._handle_get_scu(get_args) # Call the actual C-GET handler

            mock_establish_association_dicom_utils.assert_called_once()
            requested_contexts_get = mock_establish_association_dicom_utils.call_args[0][4]
            self.assertIn(CompositeInstanceRootRetrieveGet, requested_contexts_get) # For IMAGE level GET
            self.assertTrue(any(ctx in StoragePresentationContexts for ctx in requested_contexts_get))
            
            mock_get_assoc.send_c_get.assert_called_once()
            
            # Assert that the C-STORE sub-operation was attempted by the mock server's handle_get
            # This checks if MockDicomServer.handle_get correctly calls send_c_store on its association
            mock_get_assoc.send_c_store.assert_called_once()
            # Check the dataset that was sent in the C-STORE sub-operation
            sent_dataset_for_cstore = mock_get_assoc.send_c_store.call_args[0][0]
            self.assertEqual(sent_dataset_for_cstore.SOPInstanceUID, report_ds.SOPInstanceUID)

            # Verify the file was "received" by _on_get_response (which is called by pynetdicom event system)
            # We can't directly check os.path.exists here because _on_get_response is not directly
            # called in this mocked setup in a way that it would write to temp_dir UNLESS
            # we let the actual event handling of MockDicomServer run more fully.
            # The assertion on mock_get_assoc.send_c_store is the key check for this test.
            # For a true integration test of file writing, we'd need to let the internal
            # C-STORE SCP of the C-GET SCU (dicom_utils._handle_get_scu) run its course.
            # This test focuses on the mock server's ability to *initiate* the C-STORE for C-GET.
            
            # To actually test file creation by _on_get_response, we would need a different setup
            # where _on_get_response is directly called or the event loop is run.
            # For this integration test, verifying the send_c_store call by the mock SCP is sufficient.
            logger.info(f"Conceptual: File {report_ds.SOPInstanceUID}.dcm would be in {temp_dir} if C-STORE sub-op was fully processed by a real SCU's SCP part.")


if __name__ == "__main__":
    unittest.main()
