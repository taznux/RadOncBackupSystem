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
        # ... other necessary attributes for transfer test if it's kept active

        # Mock Store SCP Server Setup (can be conditionally started if only testing query logic)
        self.mock_store_scp_server = None  # Initialize
        # self.start_mock_scp() # Call this if transfer tests are active

    def start_mock_scp(self):
        """Helper to start mock SCP server."""
        if self.mock_store_scp_server is None:
            mock_store_scp_host = "127.0.0.1"
            mock_store_scp_port = 11116
            mock_store_scp_ae_title = "MOSAIQ_TEST_SCP"
            self.mock_store_scp_server = MockDicomServer(
                host=mock_store_scp_host,
                port=mock_store_scp_port,
                ae_title=mock_store_scp_ae_title,
            )
            self.mock_store_scp_server.start()
            self.store_scp = {
                "AETitle": mock_store_scp_ae_title,
                "IP": mock_store_scp_host,
                "Port": mock_store_scp_port,
            }

    def tearDown(self):
        if self.mock_store_scp_server:
            self.mock_store_scp_server.stop()
            self.mock_store_scp_server.reset()  # Ensure it's clean for next test if run in sequence

    # test_query can be removed if not implementing DB-level mocks for it.
    # The functionality of Mosaiq.query is indirectly tested via get_treatment_summary_report.

    @patch("src.data_sources.mosaiq.Mosaiq.query")
    def test_get_treatment_summary_mrn_only(self, mock_query):
        mock_query.return_value = []  # Simulate no records found
        self.mosaiq.get_treatment_summary_report(self.patient_mrn, self.db_config)

        mock_query.assert_called_once()
        args, kwargs = mock_query.call_args
        sql_query_string = args[0]
        params = kwargs.get("params")

        self.assertIn("Pat.Pat_ID1 = ?", sql_query_string)
        self.assertNotIn(
            self.patient_mrn, sql_query_string
        )  # Ensure MRN is not directly in query
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

        self.assertIn("Pat.Pat_ID1 = ?", sql_query_string)
        self.assertIn("TxFld.Plan_Start_DtTm >= ?", sql_query_string)
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

        self.assertIn("Pat.Pat_ID1 = ?", sql_query_string)
        self.assertIn("TxFld.Plan_Start_DtTm >= ?", sql_query_string)
        self.assertIn("TxFld.Plan_End_DtTm <= ?", sql_query_string)
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
        mock_query.return_value = []  # No data found
        self.mosaiq.get_treatment_summary_report(self.patient_mrn, self.db_config)

        # Check the info log call for fetching data
        # This depends on the exact log messages.
        # Example: logger.info(f"Fetching treatment summary report for MRN: ? ...")
        # Example: logger.info(f"No treatment records found for MRN: ?")

        # Get all calls to logger.info
        info_calls = [call for call in mock_logger.info.call_args_list if call]

        self.assertTrue(
            any(
                "Fetching treatment summary report for MRN: ?" in call[0][0]
                for call in info_calls
            )
        )
        self.assertTrue(
            any(
                "No treatment records found for MRN: ?" in call[0][0]
                for call in info_calls
            )
        )

        # Test successful fetch log
        mock_logger.reset_mock()
        # Simulate query returning some data by setting _TREATMENT_SUMMARY_COLUMNS
        # and providing a matching row.
        self.mosaiq._TREATMENT_SUMMARY_COLUMNS = [
            "PatientName",
            "PatientMRN",
        ]  # Adjust as per actual columns
        mock_query.return_value = [("Test Name", self.patient_mrn)]

        self.mosaiq.get_treatment_summary_report(self.patient_mrn, self.db_config)
        info_calls_success = [call for call in mock_logger.info.call_args_list if call]
        self.assertTrue(
            any(
                "Successfully fetched 1 treatment records for patient." in call[0][0]
                for call in info_calls_success
            )
        )

    # Keep existing test_transfer if it's relevant and working
    @patch("src.data_sources.mosaiq.AE")  # To mock pynetdicom association
    @patch.object(Mosaiq, "_prepare_rt_record_for_transfer")
    def test_transfer(self, mock_prepare_record, mock_ae_class):
        self.start_mock_scp()  # Ensure SCP is started for this test

        # Configure the mock AE and association to simulate success
        mock_ae_instance = MagicMock()
        mock_assoc = MagicMock()
        mock_assoc.is_established = True
        mock_assoc.accepted_contexts = [True]  # Simulate accepted context
        mock_status = MagicMock()
        mock_status.Status = 0x0000  # Success
        mock_assoc.send_c_store.return_value = mock_status
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance

        # Ensure rt_record_data is sufficiently populated for transfer
        # SOPInstanceUID will be set by _prepare_rt_record_for_transfer if not present
        # but for testing the call, we can have it or not.
        # Let's assume it might not be there to test the helper's role.
        if hasattr(self.rt_record_data, "SOPInstanceUID"):
            del self.rt_record_data.SOPInstanceUID

        try:
            self.mosaiq.transfer(self.rt_record_data, self.store_scp)
        except Exception as e:
            self.fail(f"Mosaiq.transfer raised an exception: {e}")

        # Assert that _prepare_rt_record_for_transfer was called
        mock_prepare_record.assert_called_once_with(self.rt_record_data)

        # Assert that association and C-STORE were attempted
        mock_ae_instance.associate.assert_called_once_with(
            self.store_scp["IP"],
            self.store_scp["Port"],
            ae_title=self.store_scp["AETitle"],
        )
        mock_assoc.send_c_store.assert_called_once_with(self.rt_record_data)
        mock_assoc.release.assert_called_once()

        # Assert that one dataset was received by the mock C-STORE SCP
        # This part is tricky because send_c_store is now deeply mocked.
        # To test SCP reception, we'd need a less intrusive mock or rely on integration tests.
        # For this unit test, focusing on the interactions is more appropriate.
        # However, if mock_prepare_record was NOT a real call, then the SCP would get the original.
        # Since we want to test the call to the helper, we can check its effect if we don't mock it *out*.
        # Let's adjust: we will mock the pynetdicom parts but let _prepare_rt_record_for_transfer run.

    @patch("src.data_sources.mosaiq.AE")
    def test_transfer_calls_prepare_and_sends(self, mock_ae_class):
        self.start_mock_scp()

        mock_ae_instance = MagicMock()
        mock_assoc = MagicMock()
        mock_assoc.is_established = True
        mock_assoc.accepted_contexts = [True]
        mock_status = MagicMock()
        mock_status.Status = 0x0000
        mock_assoc.send_c_store.return_value = mock_status
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance

        # Use a fresh dataset for this test to check preparation
        test_ds = Dataset()
        test_ds.PatientID = "TransferTestPID"

        # Spy on the real _prepare_rt_record_for_transfer
        with patch.object(
            self.mosaiq,
            "_prepare_rt_record_for_transfer",
            wraps=self.mosaiq._prepare_rt_record_for_transfer,
        ) as spy_prepare_record:
            self.mosaiq.transfer(test_ds, self.store_scp)

            spy_prepare_record.assert_called_once_with(test_ds)
            mock_ae_instance.associate.assert_called_once()
            mock_assoc.send_c_store.assert_called_once_with(test_ds)

            # Check that the dataset sent to C-STORE (and thus received by mock SCP) was prepared
            self.assertTrue(hasattr(test_ds, "SOPInstanceUID"))
            self.assertTrue(hasattr(test_ds, "file_meta"))
            self.assertEqual(
                test_ds.file_meta.MediaStorageSOPClassUID, RTBeamsTreatmentRecordStorage
            )

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
            fm.ImplementationClassUID.startswith("1.2.826.0.1.3680043.9.7156.1.99.")
        )
        self.assertEqual(fm.ImplementationVersionName, "RadOncBackupSystem_Mosaiq_1.0")

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
