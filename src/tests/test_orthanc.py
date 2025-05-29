import unittest
from unittest.mock import patch, MagicMock, call
from argparse import Namespace
import os # For os.path.join in verify test

import pydicom
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import generate_uid, ImplicitVRLittleEndian, CTImageStorage
from pydicom.errors import InvalidDicomError # For testing invalid DICOM data
import io

from src.backup_systems.orthanc import Orthanc
from src.cli.dicom_utils import DicomOperationError, DicomConnectionError, InvalidInputError # Assuming path

# PYDICOM_IMPLEMENTATION_UID is not directly available under pydicom.uid in all versions
# Fallback or define if not found.
try:
    PYDICOM_IMPLEMENTATION_UID = pydicom.uid.PYDICOM_IMPLEMENTATION_UID
except AttributeError:
    PYDICOM_IMPLEMENTATION_UID = generate_uid(prefix='1.2.826.0.1.3680043.9.3811.')


class TestOrthanc(unittest.TestCase):
    def _create_minimal_dicom_bytes(self, sop_instance_uid=None, content_char='a'):
        # Create a minimal DICOM file for testing
        file_meta = FileMetaDataset()
        file_meta.MediaStorageSOPClassUID = CTImageStorage 
        file_meta.MediaStorageSOPInstanceUID = sop_instance_uid or generate_uid()
        file_meta.TransferSyntaxUID = ImplicitVRLittleEndian
        file_meta.ImplementationClassUID = PYDICOM_IMPLEMENTATION_UID
        file_meta.ImplementationVersionName = "PYDICOM 1.0" 

        ds = Dataset()
        ds.file_meta = file_meta
        ds.is_little_endian = True
        ds.is_implicit_VR = True
        
        ds.PatientID = "TestPatientID"
        ds.SOPClassUID = file_meta.MediaStorageSOPClassUID
        ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
        ds.Modality = "CT"
        ds.PatientName = f"TestName_{content_char}"
        ds.Rows = 1
        ds.Columns = 1
        ds.BitsAllocated = 8
        ds.BitsStored = 8
        ds.HighBit = 7
        ds.PixelRepresentation = 0
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.PixelData = b'\x00' 

        with io.BytesIO() as bio:
            pydicom.dcmwrite(bio, ds, write_like_original=False)
            return bio.getvalue()

    def setUp(self):
        self.orthanc = Orthanc(
            calling_aet="TEST_SCU_ORTHANC", 
            peer_aet="PEER_AET_ORTHANC", 
            peer_host="dicom.peer.host", 
            peer_port=11112
        )
        self.sample_dicom_sop_uid = "1.2.3.4.5.6.777"
        self.sample_dicom_bytes = self._create_minimal_dicom_bytes(sop_instance_uid=self.sample_dicom_sop_uid)

    @patch('src.backup_systems.orthanc.dicom_utils._handle_find_scu')
    def test_store_verifies_existence_success(self, mock_handle_find_scu):
        mock_handle_find_scu.return_value = None # Simulate successful completion
        result = self.orthanc.store(sop_instance_uid=self.sample_dicom_sop_uid)
        self.assertTrue(result)
        mock_handle_find_scu.assert_called_once()
        
        # Check args passed to _handle_find_scu
        call_args = mock_handle_find_scu.call_args[0][0]
        self.assertIsInstance(call_args, Namespace)
        self.assertEqual(call_args.aet, self.orthanc.calling_aet)
        self.assertEqual(call_args.aec, self.orthanc.peer_aet)
        self.assertEqual(call_args.host, self.orthanc.peer_host)
        self.assertEqual(call_args.port, self.orthanc.peer_port)
        self.assertEqual(call_args.query_level, "IMAGE")
        self.assertEqual(call_args.sop_instance_uid, self.sample_dicom_sop_uid)

    @patch('src.backup_systems.orthanc.dicom_utils._handle_find_scu')
    def test_store_instance_not_found(self, mock_handle_find_scu):
        mock_handle_find_scu.side_effect = DicomOperationError("Instance not found")
        result = self.orthanc.store(sop_instance_uid="non.existent.uid")
        self.assertFalse(result)

    @patch('src.backup_systems.orthanc.dicom_utils._handle_find_scu')
    def test_store_dicom_connection_error(self, mock_handle_find_scu):
        mock_handle_find_scu.side_effect = DicomConnectionError("Connection failed")
        result = self.orthanc.store(sop_instance_uid="any.uid")
        self.assertFalse(result)

    @patch('src.backup_systems.orthanc.dicom_utils._handle_find_scu')
    def test_store_with_retries(self, mock_handle_find_scu):
        mock_handle_find_scu.side_effect = [DicomOperationError("Attempt 1 failed"), None] # Fail once, then succeed
        result = self.orthanc.store(sop_instance_uid=self.sample_dicom_sop_uid, retries=1)
        self.assertTrue(result)
        self.assertEqual(mock_handle_find_scu.call_count, 2)

    @patch('src.backup_systems.orthanc.shutil.rmtree')
    @patch('src.backup_systems.orthanc.tempfile.mkdtemp')
    @patch('pydicom.dcmread') # Patch dcmread used by orthanc.py for reading retrieved file
    @patch('src.backup_systems.orthanc.dicom_utils._handle_get_scu')
    @patch('src.backup_systems.orthanc.dicom_utils._handle_find_scu')
    def test_verify_success(self, mock_handle_find_scu, mock_handle_get_scu, mock_dcmread_verify, mock_mkdtemp, mock_rmtree):
        mock_handle_find_scu.return_value = None 
        mock_mkdtemp.return_value = "/mock/temp/dir"
        mock_handle_get_scu.return_value = None 
        
        original_ds = pydicom.dcmread(io.BytesIO(self.sample_dicom_bytes), force=True)
        mock_dcmread_verify.return_value = original_ds 
        
        result = self.orthanc.verify(self.sample_dicom_bytes)
        self.assertTrue(result)
        
        mock_handle_find_scu.assert_called_once()
        mock_mkdtemp.assert_called_once()
        mock_handle_get_scu.assert_called_once()
        get_call_args = mock_handle_get_scu.call_args[0][0]
        self.assertEqual(get_call_args.out_dir, "/mock/temp/dir")
        self.assertEqual(get_call_args.aet, self.orthanc.calling_aet)
        self.assertEqual(get_call_args.aec, self.orthanc.peer_aet)

        mock_dcmread_verify.assert_called_once_with(os.path.join("/mock/temp/dir", self.sample_dicom_sop_uid + ".dcm"), force=True)
        mock_rmtree.assert_called_once_with("/mock/temp/dir")

    @patch('src.backup_systems.orthanc.shutil.rmtree')
    @patch('src.backup_systems.orthanc.tempfile.mkdtemp')
    @patch('src.backup_systems.orthanc.dicom_utils._handle_get_scu')
    @patch('src.backup_systems.orthanc.dicom_utils._handle_find_scu')
    def test_verify_cfind_fails(self, mock_handle_find_scu, mock_handle_get_scu, mock_mkdtemp, mock_rmtree):
        mock_handle_find_scu.side_effect = DicomOperationError("Not found")
        result = self.orthanc.verify(self.sample_dicom_bytes)
        self.assertFalse(result)
        mock_handle_get_scu.assert_not_called()
        mock_mkdtemp.assert_not_called() # mkdtemp should not be called if find fails
        mock_rmtree.assert_not_called() # rmtree should not be called if temp dir not created

    @patch('src.backup_systems.orthanc.shutil.rmtree')
    @patch('src.backup_systems.orthanc.tempfile.mkdtemp')
    @patch('pydicom.dcmread')
    @patch('src.backup_systems.orthanc.dicom_utils._handle_get_scu')
    @patch('src.backup_systems.orthanc.dicom_utils._handle_find_scu')
    def test_verify_cget_fails(self, mock_handle_find_scu, mock_handle_get_scu, mock_dcmread_verify, mock_mkdtemp, mock_rmtree):
        mock_handle_find_scu.return_value = None
        mock_mkdtemp.return_value = "/mock/temp/dir"
        mock_handle_get_scu.side_effect = DicomOperationError("C-GET failed")
        
        result = self.orthanc.verify(self.sample_dicom_bytes)
        self.assertFalse(result)
        mock_rmtree.assert_called_once_with("/mock/temp/dir") # Cleanup should still happen

    @patch('src.backup_systems.orthanc.shutil.rmtree')
    @patch('src.backup_systems.orthanc.tempfile.mkdtemp')
    @patch('pydicom.dcmread')
    @patch('src.backup_systems.orthanc.dicom_utils._handle_get_scu')
    @patch('src.backup_systems.orthanc.dicom_utils._handle_find_scu')
    def test_verify_data_mismatch(self, mock_handle_find_scu, mock_handle_get_scu, mock_dcmread_verify, mock_mkdtemp, mock_rmtree):
        mock_handle_find_scu.return_value = None
        mock_mkdtemp.return_value = "/mock/temp/dir"
        mock_handle_get_scu.return_value = None
        
        mismatch_bytes = self._create_minimal_dicom_bytes(sop_instance_uid=self.sample_dicom_sop_uid, content_char='Y')
        mismatch_ds = pydicom.dcmread(io.BytesIO(mismatch_bytes), force=True)
        mock_dcmread_verify.return_value = mismatch_ds
        
        result = self.orthanc.verify(self.sample_dicom_bytes)
        self.assertFalse(result)
        mock_rmtree.assert_called_once_with("/mock/temp/dir")

    # Test for when pydicom.dcmread on original_data fails
    @patch('pydicom.dcmread', side_effect=InvalidDicomError("Invalid original data"))
    def test_verify_parse_original_data_fails(self, mock_dcmread_original_fail):
        # This patch will affect the dcmread call *inside* verify used for original_data
        with self.assertRaises(InvalidDicomError):
             self.orthanc.verify(b"invalid dicom data")
        # Note: If verify caught this and returned False, the test would be different.
        # Current orthanc.py re-raises InvalidDicomError.

    @patch('src.backup_systems.orthanc.shutil.rmtree')
    @patch('src.backup_systems.orthanc.tempfile.mkdtemp')
    @patch('pydicom.dcmread')
    @patch('src.backup_systems.orthanc.dicom_utils._handle_get_scu')
    @patch('src.backup_systems.orthanc.dicom_utils._handle_find_scu')
    def test_verify_retries_cfind(self, mock_handle_find_scu, mock_handle_get_scu, mock_dcmread_verify, mock_mkdtemp, mock_rmtree):
        mock_handle_find_scu.side_effect = [DicomOperationError("Find Attempt 1 failed"), None]
        mock_mkdtemp.return_value = "/mock/temp/dir"
        mock_handle_get_scu.return_value = None
        original_ds = pydicom.dcmread(io.BytesIO(self.sample_dicom_bytes), force=True)
        mock_dcmread_verify.return_value = original_ds

        result = self.orthanc.verify(self.sample_dicom_bytes, retries=1)
        self.assertTrue(result)
        self.assertEqual(mock_handle_find_scu.call_count, 2)
        mock_handle_get_scu.assert_called_once() # Should only be called after successful find
        mock_rmtree.assert_called_once_with("/mock/temp/dir")

    @patch('src.backup_systems.orthanc.shutil.rmtree')
    @patch('src.backup_systems.orthanc.tempfile.mkdtemp')
    @patch('pydicom.dcmread')
    @patch('src.backup_systems.orthanc.dicom_utils._handle_get_scu')
    @patch('src.backup_systems.orthanc.dicom_utils._handle_find_scu')
    def test_verify_retries_cget(self, mock_handle_find_scu, mock_handle_get_scu, mock_dcmread_verify, mock_mkdtemp, mock_rmtree):
        mock_handle_find_scu.return_value = None # C-FIND succeeds immediately
        mock_mkdtemp.return_value = "/mock/temp/dir"
        mock_handle_get_scu.side_effect = [DicomOperationError("Get Attempt 1 failed"), None]
        original_ds = pydicom.dcmread(io.BytesIO(self.sample_dicom_bytes), force=True)
        mock_dcmread_verify.return_value = original_ds

        result = self.orthanc.verify(self.sample_dicom_bytes, retries=1)
        self.assertTrue(result)
        mock_handle_find_scu.assert_called_once()
        self.assertEqual(mock_handle_get_scu.call_count, 2)
        mock_rmtree.assert_called_once_with("/mock/temp/dir")


if __name__ == '__main__':
    unittest.main()
