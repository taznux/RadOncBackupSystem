import unittest
import requests_mock
import pydicom
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import generate_uid, ImplicitVRLittleEndian, CTImageStorage
import io
from src.backup_systems.orthanc import Orthanc

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
        file_meta.ImplementationVersionName = "PYDICOM 1.0" # Or your specific version

        ds = Dataset()
        ds.file_meta = file_meta
        ds.is_little_endian = True
        ds.is_implicit_VR = True
        
        ds.PatientID = "TestPatientID"
        ds.SOPClassUID = file_meta.MediaStorageSOPClassUID
        ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
        ds.Modality = "CT"
        # Use a text tag to differentiate content for verify tests
        ds.PatientName = f"TestName_{content_char}"
        # Add a simple PixelData element to make it a more complete DICOM object
        ds.Rows = 1
        ds.Columns = 1
        ds.BitsAllocated = 8
        ds.BitsStored = 8
        ds.HighBit = 7
        ds.PixelRepresentation = 0
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.PixelData = b'\x00' # Single black pixel

        # Save to bytes
        with io.BytesIO() as bio:
            pydicom.dcmwrite(bio, ds, write_like_original=False)
            return bio.getvalue()

    def setUp(self):
        self.orthanc = Orthanc() # Defaults to http://localhost:8042
        self.sample_dicom_sop_uid = "1.2.3.4.5.6.777"
        self.sample_dicom_bytes = self._create_minimal_dicom_bytes(sop_instance_uid=self.sample_dicom_sop_uid)

    @requests_mock.Mocker()
    def test_store_success(self, m):
        m.post(f"{self.orthanc.orthanc_url}/instances", 
               json={'ID': 'mock-orthanc-id', 'Path': '/some/path'}, 
               status_code=200)
        result = self.orthanc.store(self.sample_dicom_bytes)
        self.assertTrue(result)

    @requests_mock.Mocker()
    def test_store_failure_server_error(self, m):
        m.post(f"{self.orthanc.orthanc_url}/instances", status_code=500)
        result = self.orthanc.store(self.sample_dicom_bytes)
        self.assertFalse(result)

    @requests_mock.Mocker()
    def test_verify_success(self, m):
        sop_uid_for_verify = "1.2.3.4.5.888"
        original_dicom_bytes = self._create_minimal_dicom_bytes(sop_instance_uid=sop_uid_for_verify, content_char='x')
        
        expected_find_payload = {
            "Level": "Instance", "Expand": True,
            "Query": {"SOPInstanceUID": sop_uid_for_verify}
        }
        m.post(f"{self.orthanc.orthanc_url}/tools/find",
               json=expected_find_payload,  # Matches the REQUEST body
               # Defines the RESPONSE body:
               json=[{'ID': 'found-id'}], # Orthanc /tools/find returns a list
               status_code=200)
        m.get(f"{self.orthanc.orthanc_url}/instances/found-id/file", 
              content=original_dicom_bytes, 
              status_code=200)
        
        result = self.orthanc.verify(original_dicom_bytes)
        self.assertTrue(result)

    @requests_mock.Mocker()
    def test_verify_not_found(self, m):
        sop_uid_for_verify = "1.2.3.4.5.889" # Unique SOP UID for this test
        original_dicom_bytes = self._create_minimal_dicom_bytes(sop_instance_uid=sop_uid_for_verify, content_char='x')

        expected_find_payload = {
            "Level": "Instance", "Expand": True,
            "Query": {"SOPInstanceUID": sop_uid_for_verify}
        }
        m.post(f"{self.orthanc.orthanc_url}/tools/find",
               json=expected_find_payload,  # Matches the REQUEST body
               # Defines the RESPONSE body:
               json=[], # Orthanc /tools/find returns an empty list for not found
               status_code=200)        
        result = self.orthanc.verify(original_dicom_bytes)
        self.assertFalse(result)

    @requests_mock.Mocker()
    def test_verify_data_mismatch(self, m):
        sop_uid_for_verify = "1.2.3.4.5.890" # Unique SOP UID
        original_dicom_bytes = self._create_minimal_dicom_bytes(sop_instance_uid=sop_uid_for_verify, content_char='x')
        # Different content (PatientName) but same SOPInstanceUID
        mismatch_dicom_bytes = self._create_minimal_dicom_bytes(sop_instance_uid=sop_uid_for_verify, content_char='y')

        expected_find_payload = {
            "Level": "Instance", "Expand": True,
            "Query": {"SOPInstanceUID": sop_uid_for_verify}
        }
        m.post(f"{self.orthanc.orthanc_url}/tools/find",
               json=expected_find_payload,  # Matches the REQUEST body
               # Defines the RESPONSE body:
               json=[{'ID': 'found-id-for-mismatch'}],
               status_code=200)
        m.get(f"{self.orthanc.orthanc_url}/instances/found-id-for-mismatch/file", 
              content=mismatch_dicom_bytes, 
              status_code=200)
        
        result = self.orthanc.verify(original_dicom_bytes)
        self.assertFalse(result)

    @requests_mock.Mocker()
    def test_verify_orthanc_error_on_find(self, m):
        sop_uid_for_verify = "1.2.3.4.5.891"
        original_dicom_bytes = self._create_minimal_dicom_bytes(sop_instance_uid=sop_uid_for_verify, content_char='x')

        expected_find_payload = {
            "Level": "Instance", "Expand": True,
            "Query": {"SOPInstanceUID": sop_uid_for_verify}
        }
        m.post(f"{self.orthanc.orthanc_url}/tools/find",
               json=expected_find_payload,  # Matches the REQUEST body
               status_code=500            # No response body needed for mock if testing server error
               )
        
        result = self.orthanc.verify(original_dicom_bytes)
        self.assertFalse(result)

    @requests_mock.Mocker()
    def test_verify_orthanc_error_on_get_file(self, m):
        sop_uid_for_verify = "1.2.3.4.5.892"
        original_dicom_bytes = self._create_minimal_dicom_bytes(sop_instance_uid=sop_uid_for_verify, content_char='x')

        expected_find_payload = {
            "Level": "Instance", "Expand": True,
            "Query": {"SOPInstanceUID": sop_uid_for_verify}
        }
        m.post(f"{self.orthanc.orthanc_url}/tools/find",
               json=expected_find_payload,  # Matches the REQUEST body
               # Defines the RESPONSE body:
               json=[{'ID': 'found-id-for-get-error'}],
               status_code=200)
        m.get(f"{self.orthanc.orthanc_url}/instances/found-id-for-get-error/file", 
              status_code=500) # Simulate server error on file retrieval
        
        result = self.orthanc.verify(original_dicom_bytes)
        self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()
