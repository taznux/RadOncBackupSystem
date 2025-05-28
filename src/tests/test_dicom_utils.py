import unittest
from unittest.mock import patch, MagicMock, ANY # call removed as it was unused
import sys
from io import StringIO
import os
import argparse  # For creating Namespace objects for helpers

# Adjust path to import from src
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from pydicom.dataset import Dataset
from pynetdicom import AE  # For type hinting in tests if needed
from pynetdicom.association import Association  # For type hinting
from pynetdicom.sop_class import (
    VerificationSOPClass,
    PatientRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelFind,
    PatientRootQueryRetrieveInformationModelMove,
    StudyRootQueryRetrieveInformationModelMove,
    RTPlanStorage,  # Example for _get_storage_contexts
)
from pynetdicom.status import (
    Status as PynetdicomStatus,
)  # To avoid conflict with local Status

# Import the main function and helpers from the script to be tested
from src.cli.dicom_utils import (
    main as dicom_utils_main,
    _handle_echo_scu,  # Testing main handlers that call helpers
    _handle_find_scu,
    _handle_move_scu,
    _handle_store_scu,
    _on_find_response,  # Response handlers are now directly testable
    _on_move_response,
    _on_store_response,
    _establish_association,  # Test helper functions directly
    _build_find_query_dataset,
    _get_find_model,
    _build_move_identifier_dataset,
    _get_move_model,
    _get_dicom_files_from_path,
    _get_storage_contexts,
    DicomConnectionError,  # Custom exceptions
    DicomOperationError,
    InvalidInputError,
    # DicomUtilsError, # Base class, not directly asserted in these tests
)
from pynetdicom import evt  # For event objects


# Patching strategy:
# - For CLI tests (calling dicom_utils_main): patch helpers like _establish_association
# - For direct tests of SCU handlers (_handle_echo_scu, etc.): patch _establish_association
# - For direct tests of helper functions: patch underlying pynetdicom.AE or os functions
@patch("src.cli.dicom_utils._establish_association")
class TestDicomUtilsEchoSCUHandler(unittest.TestCase):
    def setUp(self):
        self.args = argparse.Namespace(
            aet="ECHO_SCU",
            aec="ECHO_SCP",
            host="echohost",
            port=104,
            verbose=False,
        )
        self.mock_stdout = patch("sys.stdout", new_callable=StringIO).start()
        self.addCleanup(patch.stopall) # Replaces self.tearDown for stopping patches

    def test_handle_echo_scu_success(self, mock_establish_association):
        mock_assoc = MagicMock(spec=Association)
        mock_assoc.send_c_echo.return_value = MagicMock(
            Status=PynetdicomStatus.Success
        )
        mock_establish_association.return_value = mock_assoc

        _handle_echo_scu(self.args)

        mock_establish_association.assert_called_once_with(
            self.args.aet,
            self.args.aec,
            self.args.host,
            self.args.port,
            [VerificationSOPClass],
        )
        mock_assoc.send_c_echo.assert_called_once()
        mock_assoc.release.assert_called_once()
        self.assertIn("C-ECHO status: 0x0000 (Success)", self.mock_stdout.getvalue())

    def test_handle_echo_scu_association_fails(self, mock_establish_association):
        mock_establish_association.side_effect = DicomConnectionError(
            "Connection failed"
        )

        with self.assertRaises(DicomConnectionError): # Expect error to be re-raised
            _handle_echo_scu(self.args)
        
        self.assertIn(
            "C-ECHO operation failed: Connection failed", self.mock_stdout.getvalue()
        )
        mock_establish_association.assert_called_once()
        # Ensure send_c_echo is not called if association failed before that
        # Accessing return_value of a mock that raised an exception needs careful handling
        if mock_establish_association.return_value.is_established: # Should not be true
             mock_establish_association.return_value.send_c_echo.assert_not_called()


    def test_handle_echo_scu_echo_failure_status(self, mock_establish_association):
        mock_assoc = MagicMock(spec=Association)
        mock_assoc.send_c_echo.return_value = MagicMock(
            Status=0x0122
        )  # e.g. SOP Class Not Supported
        mock_establish_association.return_value = mock_assoc

        with self.assertRaises(DicomOperationError):
            _handle_echo_scu(self.args)

        self.assertIn(
            "C-ECHO failed with status 0x122", self.mock_stdout.getvalue()
        )
        mock_assoc.release.assert_called_once()

    def test_handle_echo_scu_no_response(self, mock_establish_association):
        mock_assoc = MagicMock(spec=Association)
        mock_assoc.send_c_echo.return_value = None  # No status object returned
        mock_establish_association.return_value = mock_assoc

        with self.assertRaises(DicomOperationError):
             _handle_echo_scu(self.args)

        self.assertIn(
            "C-ECHO failed: No response status from SCP", self.mock_stdout.getvalue()
        )
        mock_assoc.release.assert_called_once()


@patch("src.cli.dicom_utils._establish_association")
class TestDicomUtilsFindSCUHandler(unittest.TestCase):
    def setUp(self):
        self.args = argparse.Namespace(
            aet="FIND_SCU",
            aec="FIND_SCP",
            host="findhost",
            port=105,
            query_level="STUDY",
            patient_id="*",
            study_uid="",
            series_uid="",
            modality=None,
            verbose=False,
        )
        self.mock_stdout = patch("sys.stdout", new_callable=StringIO).start()
        self.addCleanup(patch.stopall)


    @patch("src.cli.dicom_utils._on_find_response")  # Mock the callback
    def test_handle_find_scu_success(
        self, mock_on_find_response, mock_establish_association
    ):
        mock_assoc = MagicMock(spec=Association)
        mock_establish_association.return_value = mock_assoc

        ds1 = Dataset()
        ds1.PatientID = "123"
        status_pending = MagicMock(Status=PynetdicomStatus.Pending)
        status_success = MagicMock(Status=PynetdicomStatus.Success)
        mock_assoc.send_c_find.return_value = iter(
            [(status_pending, ds1), (status_success, None)]
        )

        mock_on_find_response.side_effect = (
            lambda s, i, aet: s.Status != PynetdicomStatus.Success
        )

        _handle_find_scu(self.args)

        mock_establish_association.assert_called_once()
        mock_assoc.send_c_find.assert_called_once()
        self.assertEqual(mock_on_find_response.call_count, 2)
        mock_assoc.release.assert_called_once()
        self.assertIn("Association released.", self.mock_stdout.getvalue())
    
    @patch("src.cli.dicom_utils._on_find_response")
    def test_handle_find_scu_failure_in_response(self, mock_on_find_response, mock_establish_association):
        mock_assoc = MagicMock(spec=Association)
        mock_establish_association.return_value = mock_assoc

        status_failure = MagicMock(Status=PynetdicomStatus.AE_TITLE_NOT_RECOGNIZED)
        mock_assoc.send_c_find.return_value = iter([(status_failure, None)])
        
        # _on_find_response returns False on failure, which should trigger DicomOperationError
        mock_on_find_response.return_value = False 

        with self.assertRaises(DicomOperationError):
            _handle_find_scu(self.args)
        
        mock_on_find_response.assert_called_once_with(status_failure, None, self.args.aec)
        self.assertIn("C-FIND operation failed", self.mock_stdout.getvalue())


@patch("src.cli.dicom_utils._establish_association")
class TestDicomUtilsMoveSCUHandler(unittest.TestCase):
    def setUp(self):
        self.args = argparse.Namespace(
            aet="MOVE_SCU",
            aec="MOVE_SCP",
            host="movehost",
            port=106,
            move_dest_aet="DEST_AET",
            query_level="STUDY",
            patient_id="PAT01",
            study_uid="1.2.3",
            series_uid="",
            verbose=False,
        )
        self.mock_stdout = patch("sys.stdout", new_callable=StringIO).start()
        self.addCleanup(patch.stopall)


    @patch("src.cli.dicom_utils._on_move_response")
    def test_handle_move_scu_success(
        self, mock_on_move_response_final, mock_establish_association
    ):
        mock_assoc = MagicMock(spec=Association)
        mock_establish_association.return_value = mock_assoc

        status_success = MagicMock(Status=PynetdicomStatus.Success)
        mock_assoc.send_c_move.return_value = iter([(status_success, None)])

        _handle_move_scu(self.args)

        mock_establish_association.assert_called_once()
        mock_assoc.send_c_move.assert_called_once()
        mock_on_move_response_final.assert_called_once()
        self.assertEqual(
            mock_on_move_response_final.call_args[0][0].status.Status,
            PynetdicomStatus.Success,
        )
        mock_assoc.release.assert_called_once()
        self.assertIn("Association released.", self.mock_stdout.getvalue())

    def test_handle_move_scu_image_level_error(self, mock_establish_association):
        self.args.query_level = "IMAGE"
        # This should log an error but not raise an exception that stops main() with error code
        # as per current _handle_move_scu implementation.
        _handle_move_scu(self.args) 
        self.assertIn(
            "C-MOVE at IMAGE level is not typically supported",
            self.mock_stdout.getvalue(),
        )
        mock_establish_association.assert_not_called()


@patch("src.cli.dicom_utils._get_dicom_files_from_path")
@patch("src.cli.dicom_utils._establish_association")
class TestDicomUtilsStoreSCUHandler(unittest.TestCase):
    def setUp(self):
        self.args = argparse.Namespace(
            aet="STORE_SCU",
            aec="STORE_SCP",
            host="storehost",
            port=107,
            filepath="/dummy/path",
            verbose=False,
        )
        self.mock_stdout = patch("sys.stdout", new_callable=StringIO).start()
        self.mock_dcmread = patch("src.cli.dicom_utils.dcmread").start()
        self.addCleanup(patch.stopall)


    def test_handle_store_scu_success_single_file(
        self, mock_establish_association, mock_get_files
    ):
        mock_assoc = MagicMock(spec=Association)
        # Simulate accepted contexts for the SOP Class warning check
        accepted_ctx = MagicMock()
        accepted_ctx.abstract_syntax = "1.2.3.CTUID"
        mock_assoc.accepted_contexts = [accepted_ctx]
        mock_establish_association.return_value = mock_assoc

        dicom_file_path = "/dummy/ct.dcm"
        mock_get_files.return_value = [dicom_file_path]

        mock_ds = Dataset()
        mock_ds.SOPClassUID = "1.2.3.CTUID"  # Matches accepted context
        self.mock_dcmread.return_value = mock_ds

        mock_assoc.send_c_store.return_value = MagicMock(
            Status=PynetdicomStatus.Success
        )

        _handle_store_scu(self.args)

        mock_get_files.assert_called_once_with(self.args.filepath)
        mock_establish_association.assert_called_once()
        self.mock_dcmread.assert_called_once_with(dicom_file_path)
        mock_assoc.send_c_store.assert_called_once_with(mock_ds)
        mock_assoc.release.assert_called_once()
        self.assertIn(
            "Finished sending files. 1/1 requests were processed",
            self.mock_stdout.getvalue(),
        )

    def test_handle_store_scu_invalid_path(
        self, mock_establish_association, mock_get_files
    ):
        mock_get_files.side_effect = InvalidInputError("Path not found")
        with self.assertRaises(InvalidInputError): # Expect error to be re-raised
            _handle_store_scu(self.args)
        self.assertIn(
            "C-STORE setup failed: Path not found", self.mock_stdout.getvalue()
        )
        mock_establish_association.assert_not_called()

    def test_handle_store_scu_dimse_error(self, mock_establish_association, mock_get_files):
        mock_assoc = MagicMock(spec=Association)
        mock_assoc.accepted_contexts = [MagicMock(abstract_syntax='1.2.3.CTUID')]
        mock_establish_association.return_value = mock_assoc
        
        dicom_file_path = "/dummy/ct_fail.dcm"
        mock_get_files.return_value = [dicom_file_path]
        
        mock_ds = Dataset()
        mock_ds.SOPClassUID = "1.2.3.CTUID"
        self.mock_dcmread.return_value = mock_ds
        
        # Simulate DIMSE service failure
        mock_assoc.send_c_store.return_value = MagicMock(Status=0xA700) # Example error

        with self.assertRaises(DicomOperationError):
            _handle_store_scu(self.args)
        
        self.assertIn("0/1 requests were processed", self.mock_stdout.getvalue()) # files_sent_successfully should be 0
        self.assertIn("1 had DIMSE errors", self.mock_stdout.getvalue())


# --- Tests for Helper Functions ---
class TestDicomUtilsHelpers(unittest.TestCase):
    def setUp(self):
        self.mock_stdout = patch("sys.stdout", new_callable=StringIO).start()
        self.addCleanup(patch.stopall)


    @patch("src.cli.dicom_utils.AE")
    def test_establish_association_success(self, mock_ae_class):
        mock_ae_inst = MagicMock(spec=AE)
        mock_assoc = MagicMock(spec=Association)
        mock_assoc.is_established = True
        mock_ae_inst.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_inst

        assoc = _establish_association(
            "CALLAET", "CALLEDAET", "host", 104, [VerificationSOPClass]
        )
        self.assertTrue(assoc.is_established)
        mock_ae_inst.add_requested_context.assert_called_once_with(
            VerificationSOPClass
        )
        mock_ae_inst.associate.assert_called_once_with(
            "host", 104, ae_title="CALLEDAET", evt_handlers=None
        )

    @patch("src.cli.dicom_utils.AE")
    def test_establish_association_failure_rejected(self, mock_ae_class):
        mock_ae_inst = MagicMock(spec=AE)
        mock_assoc = MagicMock(spec=Association)
        mock_assoc.is_established = False
        # Simulate pynetdicom structure for acceptor details
        mock_assoc.acceptor = MagicMock()
        mock_assoc.acceptor.primitive = MagicMock()
        mock_assoc.acceptor.primitive.result_str = "Connection Refused"
        mock_ae_inst.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_inst

        with self.assertRaises(DicomConnectionError) as ctx:
            _establish_association(
                "CALLAET", "CALLEDAET", "host", 104, [VerificationSOPClass]
            )
        self.assertIn("Association rejected", str(ctx.exception))

    @patch("src.cli.dicom_utils.AE")
    def test_establish_association_network_error(self, mock_ae_class):
        mock_ae_inst = MagicMock(spec=AE)
        mock_ae_inst.associate.side_effect = OSError(
            "Network unreachable"
        )  # Simulate socket error etc.
        mock_ae_class.return_value = mock_ae_inst

        with self.assertRaises(DicomConnectionError) as ctx:
            _establish_association(
                "CALLAET", "CALLEDAET", "host", 104, [VerificationSOPClass]
            )
        self.assertIn("Association failed: Network unreachable", str(ctx.exception))

    def test_build_find_query_dataset(self):
        args = argparse.Namespace(
            query_level="STUDY",
            patient_id="PAT01",
            study_uid="1.2.3",
            series_uid=None,
            modality="CT",
        )
        ds = _build_find_query_dataset(args)
        self.assertEqual(ds.QueryRetrieveLevel, "STUDY")
        self.assertEqual(ds.PatientID, "PAT01")
        self.assertEqual(ds.StudyInstanceUID, "1.2.3")
        self.assertEqual(ds.SeriesInstanceUID, "")  # Should be empty if None in args
        self.assertEqual(ds.Modality, "CT")
        self.assertEqual(ds.PatientName, "*")  # Check default requested keys

    def test_get_find_model(self):
        self.assertEqual(
            _get_find_model("PATIENT").UID,
            PatientRootQueryRetrieveInformationModelFind.UID,
        )
        self.assertEqual(
            _get_find_model("STUDY").UID, StudyRootQueryRetrieveInformationModelFind.UID
        )

    def test_build_move_identifier_dataset(self):
        args = argparse.Namespace(
            query_level="SERIES",
            patient_id="PAT02",
            study_uid="1.2.4",
            series_uid="1.2.4.5",
        )
        ds = _build_move_identifier_dataset(args)
        self.assertEqual(ds.QueryRetrieveLevel, "SERIES")
        self.assertEqual(ds.PatientID, "PAT02")
        self.assertEqual(ds.StudyInstanceUID, "1.2.4")
        self.assertEqual(ds.SeriesInstanceUID, "1.2.4.5")

    def test_get_move_model(self):
        self.assertEqual(
            _get_move_model("PATIENT").UID,
            PatientRootQueryRetrieveInformationModelMove.UID,
        )
        self.assertEqual(
            _get_move_model("STUDY").UID, StudyRootQueryRetrieveInformationModelMove.UID
        )

    @patch("src.cli.dicom_utils.os.path.exists")
    @patch("src.cli.dicom_utils.os.path.isfile")
    @patch("src.cli.dicom_utils.dcmread")
    def test_get_dicom_files_from_path_single_file_success(
        self, mock_dcmread, mock_isfile, mock_exists
    ):
        mock_exists.return_value = True
        mock_isfile.return_value = True
        mock_dcmread.return_value = Dataset()  # Simulate successful read

        files = _get_dicom_files_from_path("/dummy/file.dcm")
        self.assertEqual(files, ["/dummy/file.dcm"])
        mock_dcmread.assert_called_once_with(
            "/dummy/file.dcm", stop_before_pixels=True
        )

    @patch("src.cli.dicom_utils.os.path.exists")
    def test_get_dicom_files_from_path_file_not_exists(self, mock_exists):
        mock_exists.return_value = False
        with self.assertRaises(InvalidInputError) as ctx:
            _get_dicom_files_from_path("/dummy/nonexistent.dcm")
        self.assertIn("File or directory not found", str(ctx.exception))

    @patch("src.cli.dicom_utils.os.path.exists")
    @patch("src.cli.dicom_utils.os.path.isfile")
    @patch("src.cli.dicom_utils.dcmread")
    def test_get_dicom_files_from_path_single_file_not_dicom(
        self, mock_dcmread, mock_isfile, mock_exists
    ):
        mock_exists.return_value = True
        mock_isfile.return_value = True
        mock_dcmread.side_effect = Exception("Not DICOM")

        with self.assertRaises(InvalidInputError) as ctx:
            _get_dicom_files_from_path("/dummy/textfile.txt")
        self.assertIn("No valid DICOM files found", str(ctx.exception))

    @patch("src.cli.dicom_utils.os.path.exists")
    @patch("src.cli.dicom_utils.os.path.isfile")
    @patch("src.cli.dicom_utils.os.path.isdir")
    @patch("src.cli.dicom_utils.os.walk")
    @patch("src.cli.dicom_utils.dcmread")
    def test_get_dicom_files_from_path_directory(
        self, mock_dcmread, mock_walk, mock_isdir, mock_isfile, mock_exists
    ):
        mock_exists.return_value = True
        mock_isfile.return_value = False  # Initial path is a dir
        mock_isdir.return_value = True

        mock_walk.return_value = [
            ("/dummy_dir", [], ["file1.dcm", "file2.txt", "file3.dcm"])
        ]

        def dcmread_side_effect(path, stop_before_pixels):
            if path.endswith(".dcm"):
                return Dataset()
            raise Exception("Not DICOM")

        mock_dcmread.side_effect = dcmread_side_effect

        files = _get_dicom_files_from_path("/dummy_dir")
        self.assertEqual(len(files), 2)
        self.assertIn("/dummy_dir/file1.dcm", files)
        self.assertIn("/dummy_dir/file3.dcm", files)

    def test_get_storage_contexts(self):
        contexts = _get_storage_contexts()
        self.assertIn(RTPlanStorage, contexts)
        self.assertIn("1.2.840.10008.5.1.4.1.1.2", contexts)


class TestDicomUtilsResponseHandlersDirect(unittest.TestCase):
    def setUp(self):
        self.mock_stdout = patch("sys.stdout", new_callable=StringIO).start()
        self.addCleanup(patch.stopall)


    def test_on_find_response_pending_with_identifier(self):
        status = MagicMock(Status=PynetdicomStatus.Pending)
        identifier = Dataset()
        identifier.PatientName = "Test^Patient"
        result = _on_find_response(status, identifier, "FIND_SCP")
        self.assertTrue(result)
        self.assertIn("PatientName: Test^Patient", self.mock_stdout.getvalue())

    def test_on_find_response_success_no_identifier(self):
        status = MagicMock(Status=PynetdicomStatus.Success)
        result = _on_find_response(status, None, "FIND_SCP")
        self.assertFalse(result)
        self.assertIn(
            "C-FIND operation completed successfully", self.mock_stdout.getvalue()
        )

    def test_on_find_response_failure(self):
        status = MagicMock(
            Status=PynetdicomStatus.AE_TITLE_NOT_RECOGNIZED, ErrorComment="Unknown AET"
        )
        identifier = Dataset()
        identifier.ErrorComment = "Peer AET error"
        if not hasattr(identifier, "ErrorComment"): # Simulate if ErrorComment is on status
            status.ErrorComment = "Error from status"


        result = _on_find_response(status, identifier, "FIND_SCP")
        self.assertFalse(result)
        output = self.mock_stdout.getvalue()
        self.assertIn("Error - Status 0x10E", output)
        if hasattr(identifier, 'ErrorComment') and identifier.ErrorComment:
            self.assertIn(identifier.ErrorComment, output)
        elif hasattr(status, 'ErrorComment') and status.ErrorComment:
             self.assertIn(status.ErrorComment, output)


    def test_on_move_response(self):
        event = MagicMock(spec=evt.Event)
        event.assoc = MagicMock(spec=Association)
        event.status = MagicMock(Status=PynetdicomStatus.Pending, ErrorComment=None)
        event.dataset = Dataset()
        event.dataset.NumberOfCompletedSuboperations = 1
        _on_move_response(event)
        self.assertIn("CompletedSuboperations: 1", self.mock_stdout.getvalue())

    def test_on_store_response_success(self):
        event = MagicMock(spec=evt.Event)
        event.status = MagicMock(Status=PynetdicomStatus.Success, ErrorComment=None)
        event.context = MagicMock()
        event.context.dataset = Dataset()
        event.context.dataset.SOPInstanceUID = "1.2.3.SUCCESS"
        _on_store_response(event)
        self.assertIn(
            "C-STORE success for SOP Instance: 1.2.3.SUCCESS",
            self.mock_stdout.getvalue(),
        )

    def test_on_store_response_failure(self):
        event = MagicMock(spec=evt.Event)
        event.status = MagicMock(Status=0xA700, ErrorComment="Out of resources")
        event.context = MagicMock()
        event.context.dataset = Dataset()
        event.context.dataset.SOPInstanceUID = "1.2.3.FAIL"
        _on_store_response(event)
        output = self.mock_stdout.getvalue()
        self.assertIn("C-STORE failed for SOP Instance: 1.2.3.FAIL", output)
        self.assertIn("status 0xA700", output)
        self.assertIn("Error: Out of resources", output)


@patch("src.cli.dicom_utils._handle_echo_scu")
class TestDicomUtilsMainEcho(unittest.TestCase):
    def setUp(self):
        self.mock_stdout_main = patch("sys.stdout", new_callable=StringIO).start()
        self.mock_stderr_main = patch("sys.stderr", new_callable=StringIO).start()
        self.addCleanup(patch.stopall)


    def test_main_echo_command_calls_handler(self, mock_handle_echo_scu):
        args = [
            "dicom_utils.py",
            "echo",
            "--aec",
            "SCP",
            "--host",
            "h",
            "--port",
            "104",
        ]
        with patch.object(sys, "argv", args):
            with self.assertRaises(SystemExit) as cm: # Expect sys.exit(0) on success
                 dicom_utils_main()
            self.assertEqual(cm.exception.code, 0)

        mock_handle_echo_scu.assert_called_once()
        called_args = mock_handle_echo_scu.call_args[0][0]
        self.assertEqual(called_args.aec, "SCP")

    def test_main_missing_required_arg_exits(self, mock_handle_echo_scu):
        args = ["dicom_utils.py", "echo", "--host", "h", "--port", "104"]
        with patch.object(sys, "argv", args):
            with self.assertRaises(SystemExit) as cm:
                dicom_utils_main()
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("required: --aec", self.mock_stderr_main.getvalue())

    def test_main_handler_raises_dicom_utils_error(self, mock_handle_echo_scu):
        mock_handle_echo_scu.side_effect = DicomOperationError("Specific OP Error")
        args = [
            "dicom_utils.py",
            "echo",
            "--aec",
            "SCP",
            "--host",
            "h",
            "--port",
            "104",
        ]
        with patch.object(sys, "argv", args):
            with self.assertRaises(SystemExit) as cm:
                dicom_utils_main()
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("Error: Specific OP Error", self.mock_stderr_main.getvalue())

    def test_main_handler_raises_unexpected_error(self, mock_handle_echo_scu):
        mock_handle_echo_scu.side_effect = ValueError("Unexpected value error")
        args = [
            "dicom_utils.py",
            "echo",
            "--aec",
            "SCP",
            "--host",
            "h",
            "--port",
            "104",
        ]
        with patch.object(sys, "argv", args):
            with self.assertRaises(SystemExit) as cm:
                dicom_utils_main()
        self.assertEqual(cm.exception.code, 1)
        self.assertIn(
            "An unexpected critical error occurred: Unexpected value error",
            self.mock_stderr_main.getvalue(),
        )


if __name__ == "__main__":
    unittest.main()
