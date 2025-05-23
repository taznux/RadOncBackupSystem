"""
Orthanc Backup System Interface.

This module provides the `Orthanc` class, an implementation of the `BackupSystem`
interface, for interacting with an Orthanc DICOM server. It allows storing
and verifying DICOM instances using Orthanc's REST API.
"""
from . import BackupSystem
import requests
import pydicom # For parsing SOPInstanceUID
import io # For creating a file-like object from bytes
import logging

logger = logging.getLogger(__name__)

# TODO: ORTHANC_URL should be configurable, e.g., loaded from a config file or environment variable.
ORTHANC_URL = "http://localhost:8042" # Base URL for Orthanc

class Orthanc(BackupSystem):
    """
    Implements the BackupSystem interface for an Orthanc DICOM server.

    This class uses Orthanc's REST API to store and verify DICOM instances.
    The Orthanc server URL is currently hardcoded but should be made configurable.
    """

    def __init__(self, orthanc_url: str = None):
        """
        Initializes the Orthanc backup system interface.

        :param orthanc_url: The base URL of the Orthanc server. 
                            If None, defaults to the global `ORTHANC_URL`.
        :type orthanc_url: str, optional
        """
        super().__init__()
        self.orthanc_url = orthanc_url if orthanc_url is not None else ORTHANC_URL
        logger.debug(f"Orthanc BackupSystem initialized with URL: {self.orthanc_url}")


    def store(self, data: bytes, retries: int = 1) -> bool:
        """
        Stores DICOM data (instance) to Orthanc via its REST API.

        :param data: Raw DICOM data as bytes.
        :type data: bytes
        :param retries: Number of times to retry on failure. Defaults to 1.
        :type retries: int, optional
        :return: True if storage was successful, False otherwise.
        :rtype: bool
        :raises requests.exceptions.RequestException: For network or HTTP errors if retries are exhausted.
        """
        url = f"{self.orthanc_url}/instances"
        headers = {"Content-Type": "application/dicom"}
        
        for attempt in range(retries + 1):
            try:
                response = requests.post(url, headers=headers, data=data, timeout=10)
                response.raise_for_status() # Raises HTTPError for bad responses (4XX or 5XX)
                
                logger.info(f"Data stored successfully in Orthanc. Orthanc response: {response.json()}")
                # Optionally, extract and log Orthanc ID: response.json().get('ID')
                return True
            except requests.exceptions.HTTPError as e:
                logger.error(f"Failed to store data in Orthanc (attempt {attempt + 1}/{retries + 1}). Status: {e.response.status_code}, Response: {e.response.text}")
            except requests.exceptions.RequestException as e:
                logger.error(f"Error storing data in Orthanc (attempt {attempt + 1}/{retries + 1}): {e}", exc_info=True)
            
            if attempt < retries:
                logger.info("Retrying storage operation...")
        
        logger.error("All retry attempts to store data in Orthanc failed.")
        return False

    def verify(self, original_data: bytes, retries: int = 1) -> bool:
        """
        Verifies if DICOM data exists in Orthanc and matches the original.

        Verification involves:
        1. Parsing SOPInstanceUID from `original_data`.
        2. Using Orthanc's `/tools/find` to locate the Orthanc ID of the instance.
        3. Retrieving the instance file from Orthanc using its Orthanc ID.
        4. Performing a byte-by-byte comparison between `original_data` and retrieved data.

        :param original_data: Raw original DICOM data as bytes.
        :type original_data: bytes
        :param retries: Number of times to retry Orthanc queries/retrievals on failure. Defaults to 1.
        :type retries: int, optional
        :return: True if verification is successful (found, retrieved, and matches), False otherwise.
        :rtype: bool
        :raises pydicom.errors.InvalidDicomError: If `original_data` is not valid DICOM.
        :raises requests.exceptions.RequestException: For network or HTTP errors if retries are exhausted.
        """
        try:
            # 1. Parse SOPInstanceUID from the original data
            # Using stop_before_pixels=True as pixel data isn't needed for UID extraction
            # and might not be present in all DICOM data being verified (e.g., RTSTRUCT).
            dicom_file = pydicom.dcmread(io.BytesIO(original_data), stop_before_pixels=True, force=True)
            sop_instance_uid = dicom_file.SOPInstanceUID
            if not sop_instance_uid:
                logger.error("Could not parse SOPInstanceUID from original data.")
                return False
            logger.info(f"Verifying SOPInstanceUID: {sop_instance_uid}")
        except Exception as e: # Could be pydicom.errors.InvalidDicomError or others
            logger.error(f"Error parsing original DICOM data to get SOPInstanceUID: {e}", exc_info=True)
            raise # Re-raise parsing errors as it indicates bad input data.

        # 2. Find Instance in Orthanc using its SOPInstanceUID
        find_url = f"{self.orthanc_url}/tools/find"
        find_payload = {
            "Level": "Instance",
            "Expand": True, # Requesting more details, like ParentStudy, ParentSeries
            "Query": {"SOPInstanceUID": str(sop_instance_uid)}
        }
        
        orthanc_instance_id = None
        orthanc_instance_details = None
        for attempt in range(retries + 1):
            try:
                response = requests.post(find_url, json=find_payload, timeout=10)
                response.raise_for_status() # Check for HTTP errors
                results = response.json()
                if results and len(results) > 0:
                    # Assuming the first result is the one. Orthanc's /tools/find should return one if UID is unique.
                    orthanc_instance_details = results[0] 
                    orthanc_instance_id = orthanc_instance_details.get('ID')
                    if orthanc_instance_id:
                        logger.info(f"Instance {sop_instance_uid} found in Orthanc with ID: {orthanc_instance_id}.")
                        break 
                    else: # Should not happen if results[0] is valid
                        logger.error("Found instance in Orthanc but it has no ID in the response.") 
                else:
                    logger.warning(f"Instance with SOPInstanceUID {sop_instance_uid} not found in Orthanc via /tools/find.")
                    return False # Not found, no need to retry unless transient issue is expected for /tools/find
            except requests.exceptions.HTTPError as e:
                logger.error(f"Failed to query Orthanc for instance {sop_instance_uid} (attempt {attempt + 1}/{retries + 1}). Status: {e.response.status_code}, Response: {e.response.text}")
            except requests.exceptions.RequestException as e:
                logger.error(f"Error querying Orthanc for instance {sop_instance_uid} (attempt {attempt + 1}/{retries + 1}): {e}", exc_info=True)

            if attempt < retries:
                logger.info(f"Retrying Orthanc query for {sop_instance_uid}...")
            elif orthanc_instance_id is None: 
                logger.error(f"Failed to find instance {sop_instance_uid} in Orthanc after {retries + 1} attempts.")
                return False
        
        if not orthanc_instance_id: # Safeguard, should be caught by return False above
             logger.error(f"Instance ID for {sop_instance_uid} not obtained from Orthanc. Verification cannot proceed.")
             return False

        # 3. Retrieve the instance file from Orthanc
        instance_file_url = f"{self.orthanc_url}/instances/{orthanc_instance_id}/file"
        retrieved_data = None
        for attempt in range(retries + 1):
            try:
                response = requests.get(instance_file_url, timeout=30) # Increased timeout for potentially large files
                response.raise_for_status()
                retrieved_data = response.content
                logger.info(f"Instance data for {sop_instance_uid} (Orthanc ID: {orthanc_instance_id}) retrieved from Orthanc.")
                break 
            except requests.exceptions.HTTPError as e:
                logger.error(f"Failed to retrieve instance file {orthanc_instance_id} from Orthanc (attempt {attempt + 1}/{retries + 1}). Status: {e.response.status_code}, Response: {e.response.text}")
            except requests.exceptions.RequestException as e:
                logger.error(f"Error retrieving instance file {orthanc_instance_id} from Orthanc (attempt {attempt + 1}/{retries + 1}): {e}", exc_info=True)
            
            if attempt < retries:
                logger.info(f"Retrying Orthanc file retrieval for {orthanc_instance_id}...")
            elif retrieved_data is None:
                logger.error(f"Failed to retrieve instance file {orthanc_instance_id} from Orthanc after {retries + 1} attempts.")
                return False
        
        if retrieved_data is None: # Safeguard
            logger.error(f"Retrieved data is None for {orthanc_instance_id}. Verification cannot proceed.")
            return False

        # 4. Compare the retrieved data with the original data
        if original_data == retrieved_data:
            logger.info(f"Verification successful: Retrieved data matches original data for SOPInstanceUID {sop_instance_uid}.")
            return True
        else:
            logger.warning(f"Verification failed: Retrieved data does not match original data for SOPInstanceUID {sop_instance_uid}.")
            # For debugging, log lengths or hashes if mismatch occurs
            logger.debug(f"Original data length: {len(original_data)}, Retrieved data length: {len(retrieved_data)}")
            # Consider saving both files for manual inspection if needed for deep debugging.
            return False
