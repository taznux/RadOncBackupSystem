import unittest
import logging
from unittest.mock import patch, MagicMock
import pyodbc  # For mocking pyodbc.Error

# Ensure pynetdicom DEBUG logs are output to console for capture
# (Keep existing pynetdicom logging setup)
logger_pynetdicom = logging.getLogger("pynetdicom")
logger_pynetdicom.setLevel(logging.DEBUG)
if not logger_pynetdicom.hasHandlers():  # Add handler if none exist, to ensure output
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger_pynetdicom.addHandler(handler)
    logger_pynetdicom.propagate = (
        False  # Avoid duplicate logs if root logger also has a handler
    )

from src.data_sources.mosaiq import Mosaiq, MosaiqQueryError
from src.tests.mock_dicom_server import MockDicomServer
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import generate_uid, ExplicitVRLittleEndian
from pynetdicom.sop_class import RTBeamsTreatmentRecordStorage


class TestMosaiq(unittest.TestCase):

    def setUp(self):
        self.mosaiq = Mosaiq()
        # self.sql_query = "SELECT * FROM Patients WHERE PatientID = '12345'" # Not directly used by new tests
        self.db_config = {
            "server": "test_server",
            "database": "test_db",
            "username": "test_user",
            "password": "test_password",
        }
        self.patient_mrn = "MRN123"
        self.start_date = "2023-01-01"
        self.end_date = "2023-12-31"

        self.rt_record_data = Dataset()
        self.rt_record_data.PatientID = "MRN123"
        # Minimal rt_record_data for transfer tests if needed, or adapt existing.
        # For this subtask, focus is on get_treatment_summary_report.
        self.rt_record_data.PatientName = "John Doe"
        
        # Mock Store SCP Server Setup
        self.mock_store_scp_server = None 
        self.start_mock_scp() # Ensure SCP is started for tests that need it

    def start_mock_scp(self):
        """Helper to start mock SCP server."""
        # if self.mock_store_scp_server is None: # Original check removed, always setup for clarity
        mock_store_scp_host = "127.0.0.1"
        mock_store_scp_port = 11116 
        mock_store_scp_ae_title = "MOSAIQ_TEST_SCP"
        self.mock_store_scp_server = MockDicomServer(
            host=mock_store_scp_host,
            port=mock_store_scp_port,
            ae_title=mock_store_scp_ae_title,
        )
        self.mock_store_scp_server.start()
        self.store_scp = { # This is used by tests to tell Mosaiq.transfer where to send
            "AETitle": mock_store_scp_ae_title,
            "IP": mock_store_scp_host,
            "Port": mock_store_scp_port,
        }

    def tearDown(self):
        if self.mock_store_scp_server:
            self.mock_store_scp_server.stop()
            self.mock_store_scp_server.reset()  

    @patch("src.data_sources.mosaiq.Mosaiq.query")
    def test_get_treatment_summary_mrn_only(self, mock_query):
        mock_query.return_value = [] 
        self.mosaiq.get_treatment_summary_report(self.patient_mrn, self.db_config)

        mock_query.assert_called_once()
        args, kwargs = mock_query.call_args
        sql_query_string = args[0]
        params = kwargs.get("params")

        self.assertIn("ID.IDA = ?", sql_query_string) # Corrected based on actual SQL
        self.assertNotIn(
            self.patient_mrn, sql_query_string
        ) 
        self.assertEqual(params, [self.patient_mrn])

    @patch("src.data_sources.mosaiq.Mosaiq.query")
    def test_get_treatment_summary_mrn_start_date(self, mock_query):
        mock_query.return_value = []
        self.mosaiq.get_treatment_summary_report(
            self.patient_mrn, self.db_config, start_date=self.start_date
        )

        mock_query.assert_called_once()
        args, kwargs = mock_query.call_args
        sql_query_string = args[0]
        params = kwargs.get("params")

        self.assertIn("ID.IDA = ?", sql_query_string) # Corrected
        self.assertIn("TxFld.Start_DtTm >= ?", sql_query_string) # Corrected
        self.assertNotIn(self.patient_mrn, sql_query_string)
        self.assertNotIn(self.start_date, sql_query_string)
        self.assertEqual(params, [self.patient_mrn, self.start_date])

    @patch("src.data_sources.mosaiq.Mosaiq.query")
    def test_get_treatment_summary_mrn_start_end_date(self, mock_query):
        mock_query.return_value = []
        self.mosaiq.get_treatment_summary_report(
            self.patient_mrn,
            self.db_config,
            start_date=self.start_date,
            end_date=self.end_date,
        )

        mock_query.assert_called_once()
        args, kwargs = mock_query.call_args
        sql_query_string = args[0]
        params = kwargs.get("params")

        self.assertIn("ID.IDA = ?", sql_query_string) # Corrected
        self.assertIn("TxFld.Start_DtTm >= ?", sql_query_string) # Corrected
        self.assertIn("TxFld.Last_Tx_DtTm <= ?", sql_query_string) # Corrected based on SQL
        self.assertNotIn(self.patient_mrn, sql_query_string)
        self.assertNotIn(self.start_date, sql_query_string)
        self.assertNotIn(self.end_date, sql_query_string)
        self.assertEqual(params, [self.patient_mrn, self.start_date, self.end_date])

    @patch("src.data_sources.mosaiq.Mosaiq.query")
    def test_get_treatment_summary_query_raises_pyodbc_error(self, mock_query):
        # Test wrapping of pyodbc.Error into MosaiqQueryError
        mock_query.side_effect = pyodbc.Error("Simulated DB Error")
        with self.assertRaises(MosaiqQueryError) as context:
            self.mosaiq.get_treatment_summary_report(self.patient_mrn, self.db_config)
        self.assertIn("Simulated DB Error", str(context.exception))

    @patch("src.data_sources.mosaiq.Mosaiq.query")
    def test_get_treatment_summary_query_raises_mosaiq_error(self, mock_query):
        # Test direct MosaiqQueryError propagation
        mock_query.side_effect = MosaiqQueryError("Simulated Mosaiq Query Error")
        with self.assertRaises(MosaiqQueryError) as context:
            self.mosaiq.get_treatment_summary_report(self.patient_mrn, self.db_config)
        self.assertIn("Simulated Mosaiq Query Error", str(context.exception))

    @patch(
        "src.data_sources.mosaiq.logger"
    )  # Patch logger in the module where it's used
    @patch("src.data_sources.mosaiq.Mosaiq.query")
    def test_get_treatment_summary_logging_obfuscation(self, mock_query, mock_logger):
        mock_query.return_value = [] 
        self.mosaiq.get_treatment_summary_report(self.patient_mrn, self.db_config)
        
        # Simplified logging check: ensure no sensitive data is directly in the log string
        # by checking call_args of the mocked logger.
        # This is less about *what* is logged and more about *how*.
        for call_args in mock_logger.info.call_args_list:
            log_message = call_args[0][0] # First argument of the call
            self.assertNotIn(self.patient_mrn, log_message, "Patient MRN should not be directly in log messages.")
            # Add similar checks for other sensitive params if applicable

    @patch("src.data_sources.mosaiq.AE")
    def test_transfer_cstore_success_and_returns_true(self, mock_ae_class):
        # self.start_mock_scp() # Already called in setUp
        mock_ae_instance = MagicMock()
        mock_assoc = MagicMock()
        mock_assoc.is_established = True
        mock_assoc.accepted_contexts = [True] # Simulate accepted context
        mock_status = MagicMock()
        mock_status.Status = 0x0000  # Success
        mock_assoc.send_c_store.return_value = mock_status
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance

        test_ds = Dataset()
        test_ds.PatientID = "TransferSuccessPID"

        with patch.object(
            self.mosaiq,
            "_prepare_rt_record_for_transfer", # Spy on this to ensure it's called
            wraps=self.mosaiq._prepare_rt_record_for_transfer,
        ) as spy_prepare_record:
            result = self.mosaiq.transfer(test_ds, self.store_scp)

            self.assertTrue(result)
            spy_prepare_record.assert_called_once_with(test_ds)
            mock_ae_instance.associate.assert_called_once()
            mock_assoc.send_c_store.assert_called_once_with(test_ds)
            self.assertTrue(hasattr(test_ds, "SOPInstanceUID"))
            self.assertTrue(hasattr(test_ds, "file_meta"))

    @patch("src.data_sources.mosaiq.AE")
    def test_transfer_cstore_failure_status_returns_false(self, mock_ae_class):
        # self.start_mock_scp() # Already called in setUp
        mock_ae_instance = MagicMock()
        mock_assoc = MagicMock()
        mock_assoc.is_established = True
        mock_assoc.accepted_contexts = [True]
        mock_status = MagicMock()
        mock_status.Status = 0xA700  # Failure status (e.g., Out of Resources)
        mock_status.ErrorComment = "SCP out of space" # Example error comment
        mock_assoc.send_c_store.return_value = mock_status
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance

        test_ds = Dataset()
        test_ds.PatientID = "TransferFailStatusPID"
        result = self.mosaiq.transfer(test_ds, self.store_scp)
        self.assertFalse(result)

    @patch("src.data_sources.mosaiq.AE")
    def test_transfer_association_failure_returns_false(self, mock_ae_class):
        # self.start_mock_scp() # Already called in setUp
        mock_ae_instance = MagicMock()
        mock_assoc = MagicMock()
        mock_assoc.is_established = False
        # Simulate acceptor details for logging, matching Mosaiq.transfer's access pattern
        mock_assoc.acceptor = MagicMock() 
        mock_assoc.acceptor.primitive = MagicMock()
        mock_assoc.acceptor.primitive.result_str = "Test Reject"
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance
        
        test_ds = Dataset()
        test_ds.PatientID = "TransferAssocFailPID"
        result = self.mosaiq.transfer(test_ds, self.store_scp)
        self.assertFalse(result)

    @patch("src.data_sources.mosaiq.AE")
    def test_transfer_no_accepted_contexts_returns_false(self, mock_ae_class):
        # self.start_mock_scp() # Already called in setUp
        mock_ae_instance = MagicMock()
        mock_assoc = MagicMock()
        mock_assoc.is_established = True
        mock_assoc.accepted_contexts = [] # No accepted presentation contexts
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance

        test_ds = Dataset()
        test_ds.PatientID = "TransferNoContextPID"
        result = self.mosaiq.transfer(test_ds, self.store_scp)
        self.assertFalse(result)
        mock_assoc.send_c_store.assert_not_called() # Should not attempt C-STORE

    @patch("src.data_sources.mosaiq.AE")
    def test_transfer_send_c_store_raises_exception_returns_false(self, mock_ae_class):
        # self.start_mock_scp() # Already called in setUp
        mock_ae_instance = MagicMock()
        mock_assoc = MagicMock()
        mock_assoc.is_established = True
        mock_assoc.accepted_contexts = [True]
        mock_assoc.send_c_store.side_effect = RuntimeError("Network glitch during C-STORE")
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance

        test_ds = Dataset()
        test_ds.PatientID = "TransferExceptionPID"
        result = self.mosaiq.transfer(test_ds, self.store_scp)
        self.assertFalse(result)

    def test_prepare_rt_record_for_transfer_new_dataset(self):
        ds = Dataset()
        self.mosaiq._prepare_rt_record_for_transfer(ds)

        self.assertEqual(ds.SOPClassUID, RTBeamsTreatmentRecordStorage)
        self.assertTrue(hasattr(ds, "SOPInstanceUID"))
        self.assertTrue(ds.SOPInstanceUID)  # Ensure it's not empty

        self.assertTrue(hasattr(ds, "file_meta"))
        fm = ds.file_meta
        self.assertEqual(fm.FileMetaInformationVersion, b"\x00\x01")
        self.assertEqual(fm.MediaStorageSOPClassUID, ds.SOPClassUID)
        self.assertEqual(fm.MediaStorageSOPInstanceUID, ds.SOPInstanceUID)
        self.assertEqual(fm.TransferSyntaxUID, ExplicitVRLittleEndian)
        self.assertTrue(
            fm.ImplementationClassUID.startswith("1.2.826.0.1.3680043.9.7156.1.99.") # Mosaiq's specific prefix
        )
        self.assertEqual(fm.ImplementationVersionName, "RadOncBackupSystem_Mosaiq_1.1") # Updated version

        self.assertTrue(ds.is_little_endian)
        self.assertFalse(ds.is_implicit_VR)

    def test_prepare_rt_record_for_transfer_existing_sop_instance_uid(self):
        ds = Dataset()
        existing_sop_uid = generate_uid()
        ds.SOPInstanceUID = existing_sop_uid

        self.mosaiq._prepare_rt_record_for_transfer(ds)

        self.assertEqual(ds.SOPInstanceUID, existing_sop_uid)  # Should not change
        self.assertTrue(hasattr(ds, "file_meta"))  # file_meta should still be created

    def test_prepare_rt_record_for_transfer_existing_file_meta(self):
        ds = Dataset()
        ds.file_meta = FileMetaDataset()
        ds.file_meta.TransferSyntaxUID = (
            "1.2.840.10008.1.2"  # Implicit VR Little Endian
        )

        self.mosaiq._prepare_rt_record_for_transfer(ds)

        # TransferSyntaxUID should be overwritten to ExplicitVRLittleEndian
        self.assertEqual(ds.file_meta.TransferSyntaxUID, ExplicitVRLittleEndian)
        # Other file meta attributes should be populated
        self.assertTrue(hasattr(ds.file_meta, "MediaStorageSOPClassUID"))


if __name__ == "__main__":
    unittest.main()
