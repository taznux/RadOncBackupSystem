import unittest
from unittest.mock import patch, MagicMock, call, ANY
import sys
from io import StringIO
import os

# To allow importing from src.cli
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from pydicom.dataset import Dataset
from pynetdicom.status import Status # For creating status objects
from pynetdicom.sop_class import PatientRootQueryRetrieveInformationModelMove, StudyRootQueryRetrieveInformationModelMove

# Import the main function from the script to be tested
from src.cli.dicom_utils import main as dicom_utils_main, handle_store_response, handle_move_response, handle_find_response

# Mock pynetdicom AE and Association
# It's often easier to patch 'pynetdicom.AE' in the module where it's USED.
# So, we'll patch 'src.cli.dicom_utils.AE'
@patch('src.cli.dicom_utils.AE')
class TestDicomUtilsEcho(unittest.TestCase):

    def setUp(self):
        self.held_stdout = sys.stdout
        self.captured_output = StringIO()
        sys.stdout = self.captured_output # Redirect stdout

        # Common args for all tests
        self.common_cli_args = ['--aec', 'TEST_SCP', '--host', 'localhost', '--port', '11112']

    def tearDown(self):
        sys.stdout = self.held_stdout # Restore stdout
        patch.stopall() # Ensure all patches are stopped

    def test_echo_success(self, mock_ae_class):
        """Test C-ECHO successful operation."""
        mock_assoc = MagicMock()
        mock_assoc.is_established = True
        mock_assoc.send_c_echo.return_value = MagicMock(Status=0x0000) # Success status

        mock_ae_instance = MagicMock()
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance

        args = ['echo'] + self.common_cli_args
        with patch.object(sys, 'argv', ['dicom_utils.py'] + args):
            dicom_utils_main()

        output = self.captured_output.getvalue()
        self.assertIn("Performing C-ECHO to TEST_SCP at localhost:11112", output)
        self.assertIn("Association established.", output)
        self.assertIn("C-ECHO status: 0x0000 (Success)", output) # Check for success message
        self.assertIn("Association released.", output)
        mock_ae_instance.add_requested_context.assert_called_once() # VerificationSOPClass
        mock_ae_instance.associate.assert_called_once_with('localhost', 11112, ae_title='TEST_SCP')
        mock_assoc.send_c_echo.assert_called_once()
        mock_assoc.release.assert_called_once()

    def test_echo_association_failed(self, mock_ae_class):
        """Test C-ECHO when association fails."""
        mock_assoc = MagicMock()
        mock_assoc.is_established = False
        # Simulate acceptor details for error message
        mock_assoc.acceptor = MagicMock()
        mock_assoc.acceptor.primitive = MagicMock()
        mock_assoc.acceptor.primitive.result_str = "Connection refused"


        mock_ae_instance = MagicMock()
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance

        args = ['echo'] + self.common_cli_args
        with patch.object(sys, 'argv', ['dicom_utils.py'] + args):
            dicom_utils_main()
        
        output = self.captured_output.getvalue()
        self.assertIn("Association failed for C-ECHO: Connection refused", output)
        mock_ae_instance.associate.assert_called_once()
        mock_assoc.send_c_echo.assert_not_called() # Should not be called if assoc fails

    def test_echo_scp_failure_status(self, mock_ae_class):
        """Test C-ECHO when SCP returns a failure status."""
        mock_assoc = MagicMock()
        mock_assoc.is_established = True
        # Example failure status (e.g., Refused: SOP Class Not Supported)
        failure_status = MagicMock(Status=0x0122, StatusDescription="SOP Class Not Supported")
        mock_assoc.send_c_echo.return_value = failure_status

        mock_ae_instance = MagicMock()
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance

        args = ['echo'] + self.common_cli_args
        with patch.object(sys, 'argv', ['dicom_utils.py'] + args):
            dicom_utils_main()

        output = self.captured_output.getvalue()
        self.assertIn("C-ECHO status: 0x0122 (SOP Class Not Supported)", output)
        mock_assoc.send_c_echo.assert_called_once()

    def test_echo_no_response_from_scp(self, mock_ae_class):
        """Test C-ECHO when SCP provides no response (send_c_echo returns None)."""
        mock_assoc = MagicMock()
        mock_assoc.is_established = True
        mock_assoc.send_c_echo.return_value = None # Simulate no response

        mock_ae_instance = MagicMock()
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance
        
        args = ['echo'] + self.common_cli_args
        with patch.object(sys, 'argv', ['dicom_utils.py'] + args):
            dicom_utils_main()

        output = self.captured_output.getvalue()
        self.assertIn("C-ECHO failed: No response from SCP.", output)

# Separate class for C-FIND tests for clarity
@patch('src.cli.dicom_utils.AE')
class TestDicomUtilsFind(unittest.TestCase):

    def setUp(self):
        self.held_stdout = sys.stdout
        self.captured_output = StringIO()
        sys.stdout = self.captured_output

        self.common_cli_args = ['--aec', 'FIND_SCP', '--host', 'findhost', '--port', '11113']

    def tearDown(self):
        sys.stdout = self.held_stdout
        patch.stopall()

    def test_find_success_with_results(self, mock_ae_class):
        """Test C-FIND successful operation with results."""
        mock_assoc = MagicMock()
        mock_assoc.is_established = True
        
        ds1 = Dataset()
        ds1.PatientName = "DOE^JOHN"
        ds1.PatientID = "12345"
        ds1.QueryRetrieveLevel = "PATIENT"

        ds2 = Dataset()
        ds2.PatientName = "ROE^JANE"
        ds2.PatientID = "67890"
        ds2.QueryRetrieveLevel = "PATIENT"

        pending_status = MagicMock(Status=0xFF00) 
        success_status = MagicMock(Status=0x0000) 
        
        mock_assoc.send_c_find.return_value = [
            (pending_status, ds1),
            (pending_status, ds2),
            (success_status, None) 
        ]

        mock_ae_instance = MagicMock()
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance

        args = ['find', '--patient-id', '*', '--query-level', 'PATIENT'] + self.common_cli_args 
        with patch.object(sys, 'argv', ['dicom_utils.py'] + args):
            dicom_utils_main()
        
        output = self.captured_output.getvalue()
        self.assertIn("Performing C-FIND to FIND_SCP at findhost:11113", output)
        self.assertIn("Association established for C-FIND.", output)
        self.assertIn("PatientName: DOE^JOHN", output)
        self.assertIn("PatientID: 12345", output)
        self.assertIn("PatientName: ROE^JANE", output)
        self.assertIn("PatientID: 67890", output)
        self.assertIn("C-FIND RSP from FIND_SCP: Success - Final result.", output)
        self.assertIn("Association released.", output)
        
        mock_ae_instance.associate.assert_called_once()
        mock_assoc.send_c_find.assert_called_once()
        sent_ds = mock_assoc.send_c_find.call_args[0][0]
        self.assertEqual(sent_ds.PatientID, '*')
        self.assertEqual(sent_ds.QueryRetrieveLevel, 'PATIENT')
        self.assertIn("Pending - Found identifier:", output)


    def test_find_no_results(self, mock_ae_class):
        mock_assoc = MagicMock()
        mock_assoc.is_established = True
        success_status = MagicMock(Status=0x0000)
        mock_assoc.send_c_find.return_value = [(success_status, None)] 

        mock_ae_instance = MagicMock()
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance

        args = ['find', '--patient-id', 'NONEXISTENT'] + self.common_cli_args
        with patch.object(sys, 'argv', ['dicom_utils.py'] + args):
            dicom_utils_main()

        output = self.captured_output.getvalue()
        self.assertIn("C-FIND RSP from FIND_SCP: Success - Final result.", output)
        self.assertNotIn("Pending - Found identifier:", output)

    def test_find_failure_status(self, mock_ae_class):
        mock_assoc = MagicMock()
        mock_assoc.is_established = True
        failure_status = MagicMock(Status=0xA700, StatusDescription="Unable to process")
        error_ds = Dataset()
        error_ds.ErrorComment = "Something went wrong"
        mock_assoc.send_c_find.return_value = [(failure_status, error_ds)]

        mock_ae_instance = MagicMock()
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance

        args = ['find', '--patient-id', 'ANY'] + self.common_cli_args
        with patch.object(sys, 'argv', ['dicom_utils.py'] + args):
            dicom_utils_main()
        
        output = self.captured_output.getvalue()
        self.assertIn("C-FIND RSP from FIND_SCP: Error - Status 0xA700 (Unable to process)", output)
        self.assertIn("Error Comment: Something went wrong", output)


@patch('src.cli.dicom_utils.AE')
class TestDicomUtilsMove(unittest.TestCase):
    def setUp(self):
        self.held_stdout = sys.stdout
        self.captured_output = StringIO()
        sys.stdout = self.captured_output
        self.common_cli_args = ['--aec', 'MOVE_SCP', '--host', 'movehost', '--port', '11114', '--move-dest-aet', 'DEST_AET']

    def tearDown(self):
        sys.stdout = self.held_stdout
        patch.stopall()

    def test_move_success(self, mock_ae_class):
        mock_assoc = MagicMock()
        mock_assoc.is_established = True
        
        success_status = MagicMock(Status=0x0000, StatusDescription="Success")
        mock_assoc.send_c_move.return_value = [(success_status, None)]

        mock_ae_instance = MagicMock()
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance

        args = ['move', '--patient-id', 'PATMOVE001', '--query-level', 'PATIENT'] + self.common_cli_args
        with patch.object(sys, 'argv', ['dicom_utils.py'] + args):
            dicom_utils_main()

        output = self.captured_output.getvalue()
        self.assertIn("Performing C-MOVE to MOVE_SCP at movehost:11114, destination AET: DEST_AET", output)
        self.assertIn("Association established for C-MOVE. Destination: DEST_AET", output)
        self.assertIn("C-MOVE final response status: 0x0000 (Success)", output)
        self.assertIn("Association released.", output)

        mock_ae_instance.associate.assert_called_once_with('movehost', 11114, ae_title='MOVE_SCP', evt_handlers=ANY)
        mock_assoc.send_c_move.assert_called_once()
        sent_ds = mock_assoc.send_c_move.call_args[0][0]
        self.assertEqual(sent_ds.PatientID, 'PATMOVE001')
        self.assertEqual(sent_ds.QueryRetrieveLevel, 'PATIENT')
        self.assertEqual(mock_assoc.send_c_move.call_args[0][1], 'DEST_AET') # Move destination AET
        self.assertEqual(mock_assoc.send_c_move.call_args[0][2].UID, PatientRootQueryRetrieveInformationModelMove.UID)

    def test_move_failure_status_in_final_response(self, mock_ae_class):
        mock_assoc = MagicMock()
        mock_assoc.is_established = True
        
        failure_status = MagicMock(Status=0xA801, StatusDescription="Move Destination Unknown")
        error_identifier = Dataset()
        error_identifier.ErrorComment = "The AET 'DEST_AET_BAD' is not recognized."
        mock_assoc.send_c_move.return_value = [(failure_status, error_identifier)]

        mock_ae_instance = MagicMock()
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance

        args = ['move', '--study-uid', 'STUDYMOVE002', '--query-level', 'STUDY'] + self.common_cli_args
        with patch.object(sys, 'argv', ['dicom_utils.py'] + args):
            dicom_utils_main()

        output = self.captured_output.getvalue()
        self.assertIn("C-MOVE final response status: 0xA801 (Move Destination Unknown)", output)
        self.assertIn("Error Comment: The AET 'DEST_AET_BAD' is not recognized.", output)
        sent_ds = mock_assoc.send_c_move.call_args[0][0]
        self.assertEqual(sent_ds.StudyInstanceUID, 'STUDYMOVE002')
        self.assertEqual(sent_ds.QueryRetrieveLevel, 'STUDY')
        self.assertEqual(mock_assoc.send_c_move.call_args[0][2].UID, StudyRootQueryRetrieveInformationModelMove.UID)


    def test_move_image_level_not_supported_message(self, mock_ae_class):
        args = ['move', '--query-level', 'IMAGE', '--patient-id', 'PATIMG001'] + self.common_cli_args
        with patch.object(sys, 'argv', ['dicom_utils.py'] + args):
            dicom_utils_main()
        
        output = self.captured_output.getvalue()
        self.assertIn("C-MOVE at IMAGE level is not typically supported directly.", output)
        mock_ae_class.return_value.associate.assert_not_called()


@patch('src.cli.dicom_utils.dcmread') 
@patch('src.cli.dicom_utils.os.path.isfile')
@patch('src.cli.dicom_utils.os.path.isdir')
@patch('src.cli.dicom_utils.os.walk')
@patch('src.cli.dicom_utils.AE') 
class TestDicomUtilsStore(unittest.TestCase):
    def setUp(self):
        self.held_stdout = sys.stdout
        self.captured_output = StringIO()
        sys.stdout = self.captured_output
        self.common_cli_args = ['--aec', 'STORE_SCP', '--host', 'storehost', '--port', '11115']

    def tearDown(self):
        sys.stdout = self.held_stdout
        patch.stopall()

    def test_store_single_file_success(self, mock_ae_class, mock_os_walk, mock_os_isdir, mock_os_isfile, mock_dcmread):
        mock_os_isfile.return_value = True
        mock_os_isdir.return_value = False
        
        dicom_ds = Dataset()
        dicom_ds.SOPClassUID = '1.2.840.10008.5.1.4.1.1.2' 
        dicom_ds.SOPInstanceUID = '1.2.3.CT.1'
        mock_dcmread.return_value = dicom_ds

        mock_assoc = MagicMock()
        mock_assoc.is_established = True
        mock_assoc.send_c_store.return_value = MagicMock(Status=0x0000) 

        mock_ae_instance = MagicMock()
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance
        
        filepath = "/path/to/dicom_file.dcm"
        args = ['store', '--filepath', filepath] + self.common_cli_args
        with patch.object(sys, 'argv', ['dicom_utils.py'] + args), \
             patch('src.cli.dicom_utils.os.path.exists') as mock_exists:
            mock_exists.return_value = True 
            dicom_utils_main()

        output = self.captured_output.getvalue()
        self.assertIn(f"Performing C-STORE to STORE_SCP at storehost:11115", output)
        self.assertIn(f"Found 1 DICOM file(s) to send.", output)
        self.assertIn(f"Attempting to send: {filepath}", output)
        self.assertIn("Association established for C-STORE.", output)
        self.assertIn("Association released.", output)

        mock_dcmread.assert_called_once_with(filepath)
        mock_ae_instance.associate.assert_called_once_with('storehost', 11115, ae_title='STORE_SCP', evt_handlers=ANY)
        mock_assoc.send_c_store.assert_called_once_with(dicom_ds)

    def test_store_directory_with_one_dicom_one_non_dicom(self, mock_ae_class, mock_os_walk, mock_os_isdir, mock_os_isfile, mock_dcmread):
        mock_os_isdir.return_value = True # For the initial path check being a directory
        mock_os_isfile.return_value = False # Ensure it's not treated as a file initially

        dir_path = "/path/to/dicom_dir/"
        dicom_file_name = "ctimage.dcm"
        non_dicom_file_name = "notes.txt"
        # os.walk yields (dirpath, dirnames, filenames)
        mock_os_walk.return_value = [
            (dir_path, [], [dicom_file_name, non_dicom_file_name]) 
        ]
        
        ct_ds = Dataset()
        ct_ds.SOPClassUID = '1.2.840.10008.5.1.4.1.1.2' 
        ct_ds.SOPInstanceUID = '1.2.3.CT.DIR.1'
        
        def dcmread_side_effect(path_to_read, stop_before_pixels=None):
            if path_to_read == os.path.join(dir_path, dicom_file_name):
                return ct_ds
            elif path_to_read == os.path.join(dir_path, non_dicom_file_name):
                # Simulate pydicom's InvalidDicomError or similar
                raise Exception("Invalid DICOM file") 
            return None # Should not happen with this test's logic
        mock_dcmread.side_effect = dcmread_side_effect
        
        mock_assoc = MagicMock()
        mock_assoc.is_established = True
        mock_assoc.send_c_store.return_value = MagicMock(Status=0x0000)
        mock_ae_instance = MagicMock()
        mock_ae_instance.associate.return_value = mock_assoc
        mock_ae_class.return_value = mock_ae_instance

        args = ['store', '--filepath', dir_path] + self.common_cli_args
        with patch.object(sys, 'argv', ['dicom_utils.py'] + args), \
             patch('src.cli.dicom_utils.os.path.exists') as mock_exists:
            mock_exists.return_value = True
            dicom_utils_main()

        output = self.captured_output.getvalue()
        self.assertIn(f"Found 1 DICOM file(s) to send.", output) 
        self.assertIn(f"Attempting to send: {os.path.join(dir_path, dicom_file_name)}", output)
        # Check for debug log for skipping non-DICOM
        # To capture logger output, logger itself needs to be patched or configured for tests.
        # For now, checking that only one send_c_store was called.
        # self.assertIn(f"Skipping non-DICOM file: {os.path.join(dir_path, non_dicom_file_name)}", output) # This is a debug log
        
        # Check that dcmread was called for both files
        expected_dcmread_calls = [
            call(os.path.join(dir_path, dicom_file_name), stop_before_pixels=True),
            call(os.path.join(dir_path, non_dicom_file_name), stop_before_pixels=True)
        ]
        mock_dcmread.assert_has_calls(expected_dcmread_calls, any_order=True)
        mock_assoc.send_c_store.assert_called_once_with(ct_ds) # Only the valid DICOM DS


    def test_store_file_not_found(self, mock_ae_class, mock_os_walk, mock_os_isdir, mock_os_isfile, mock_dcmread):
        filepath = "/path/to/non_existent.dcm"
        args = ['store', '--filepath', filepath] + self.common_cli_args
        with patch.object(sys, 'argv', ['dicom_utils.py'] + args), \
             patch('src.cli.dicom_utils.os.path.exists') as mock_exists:
            mock_exists.return_value = False 
            dicom_utils_main()
        
        output = self.captured_output.getvalue()
        self.assertIn(f"File or directory not found: {filepath}", output)
        mock_ae_class.return_value.associate.assert_not_called()

class TestDicomUtilsResponseHandlers(unittest.TestCase):
    def setUp(self):
        self.held_stdout = sys.stdout
        self.captured_output = StringIO()
        sys.stdout = self.captured_output
    
    def tearDown(self):
        sys.stdout = self.held_stdout

    def test_handle_find_response_pending(self):
        status = MagicMock(Status=0xFF00) 
        identifier = Dataset()
        identifier.PatientName = "Test^Patient"
        identifier.PatientID = "PID001"
        
        result = handle_find_response(status, identifier, "TEST_AE")
        self.assertTrue(result) 
        output = self.captured_output.getvalue()
        self.assertIn("Pending - Found identifier:", output)
        self.assertIn("PatientName: Test^Patient", output)

    def test_handle_find_response_success_no_identifier(self):
        status = MagicMock(Status=0x0000) 
        result = handle_find_response(status, None, "TEST_AE")
        self.assertFalse(result) 
        output = self.captured_output.getvalue()
        self.assertIn("Success - Final result.", output)
        self.assertNotIn("Final identifier data", output)

    def test_handle_find_response_failure_with_error_comment(self):
        status = MagicMock(Status=0xA900, StatusDescription="Some DICOM Error") 
        identifier = Dataset()
        identifier.ErrorComment = "Detailed error from SCP"
        result = handle_find_response(status, identifier, "TEST_AE")
        self.assertFalse(result) 
        output = self.captured_output.getvalue()
        self.assertIn("Error - Status 0xA900 (Some DICOM Error)", output)
        self.assertIn("Error Comment: Detailed error from SCP", output)

    def test_handle_move_response_interim(self):
        event = MagicMock()
        event.status = MagicMock(Status=0xFF00, StatusDescription="Pending") 
        event.dataset = Dataset()
        event.dataset.NumberOfRemainingSuboperations = 5
        event.dataset.NumberOfCompletedSuboperations = 2
        event.dataset.NumberOfWarningSuboperations = 0
        event.dataset.NumberOfFailedSuboperations = 0
        event.dataset.AffectedSOPInstanceUID = "1.2.3.4.5"

        handle_move_response(event) 
        output = self.captured_output.getvalue()
        self.assertIn("C-MOVE Response: Status 0xFF00 (Pending)", output)
        self.assertIn("Affected SOP Instance UID: 1.2.3.4.5", output)
        self.assertIn("Remaining Sub-operations: 5", output)
        self.assertIn("Completed Sub-operations: 2", output)

    def test_handle_store_response_success(self):
        event = MagicMock()
        event.status = MagicMock(Status=0x0000)
        event.context = MagicMock()
        event.context.dataset = Dataset()
        event.context.dataset.SOPInstanceUID = "1.2.3.STORE.SUCCESS"
        
        handle_store_response(event)
        output = self.captured_output.getvalue()
        self.assertIn("C-STORE success for SOP Instance: 1.2.3.STORE.SUCCESS", output)

    def test_handle_store_response_failure(self):
        event = MagicMock()
        event.status = MagicMock(Status=0xB000, ErrorComment="Failed to store")
        event.context = MagicMock() 
        event.context.dataset = Dataset()
        event.context.dataset.SOPInstanceUID = "1.2.3.STORE.FAIL"

        handle_store_response(event)
        output = self.captured_output.getvalue()
        self.assertIn("C-STORE failed for SOP Instance: 1.2.3.STORE.FAIL with status 0xB000", output)
        self.assertIn("Error: Failed to store", output)


if __name__ == '__main__':
    unittest.main()
