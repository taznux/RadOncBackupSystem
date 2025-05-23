from . import BackupSystem
import requests
import pydicom # For parsing SOPInstanceUID
import io # For creating a file-like object from bytes

ORTHANC_URL = "http://localhost:8042" # Base URL for Orthanc

class Orthanc(BackupSystem):
    def store(self, data: bytes, retries: int = 1) -> bool:
        """
        Stores DICOM data to Orthanc.
        :param data: Raw DICOM data as bytes.
        :param retries: Number of times to retry on failure.
        :return: True if successful, False otherwise.
        """
        url = f"{ORTHANC_URL}/instances"
        headers = {"Content-Type": "application/dicom"}
        
        for attempt in range(retries + 1):
            try:
                response = requests.post(url, headers=headers, data=data, timeout=10) # Added timeout
                if response.status_code == 200:
                    print("Data stored successfully in Orthanc.")
                    # Optionally, we can extract Orthanc ID from response.json()['ID'] if needed later
                    return True
                else:
                    print(f"Failed to store data in Orthanc. Status: {response.status_code}, Response: {response.text}")
            except requests.exceptions.RequestException as e:
                print(f"Error storing data in Orthanc (attempt {attempt + 1}/{retries + 1}): {e}")
            
            if attempt < retries:
                print("Retrying...")
        return False

    def verify(self, original_data: bytes, retries: int = 1) -> bool:
        """
        Verifies if the DICOM data previously sent via store exists in Orthanc and matches the original.
        :param original_data: Raw original DICOM data as bytes.
        :param retries: Number of times to retry Orthanc queries on failure.
        :return: True if verification is successful, False otherwise.
        """
        try:
            # 1. Parse SOPInstanceUID from the original data
            dicom_file = pydicom.dcmread(io.BytesIO(original_data), stop_before_pixels=True)
            sop_instance_uid = dicom_file.SOPInstanceUID
            if not sop_instance_uid:
                print("Could not parse SOPInstanceUID from original data.")
                return False
            print(f"Verifying SOPInstanceUID: {sop_instance_uid}")
        except Exception as e:
            print(f"Error parsing original DICOM data: {e}")
            return False

        # 2. Find Instance in Orthanc using its SOPInstanceUID
        find_url = f"{ORTHANC_URL}/tools/find"
        find_payload = {
            "Level": "Instance",
            "Query": {"SOPInstanceUID": str(sop_instance_uid)}
        }
        
        orthanc_instance_id = None
        for attempt in range(retries + 1):
            try:
                response = requests.post(find_url, json=find_payload, timeout=10)
                if response.status_code == 200:
                    results = response.json()
                    if results and len(results) > 0:
                        orthanc_instance_id = results[0] # Assuming the first result is the one
                        print(f"Instance found in Orthanc with ID: {orthanc_instance_id}")
                        break # Found, exit retry loop
                    else:
                        print(f"Instance with SOPInstanceUID {sop_instance_uid} not found in Orthanc.")
                        # If not found on first try, no need to retry unless transient issue expected
                        return False 
                else:
                    print(f"Failed to query Orthanc for instance. Status: {response.status_code}, Response: {response.text}")
            except requests.exceptions.RequestException as e:
                print(f"Error querying Orthanc (attempt {attempt + 1}/{retries + 1}): {e}")

            if attempt < retries:
                print("Retrying query...")
            elif orthanc_instance_id is None: # Failed all retries
                print(f"Failed to find instance {sop_instance_uid} in Orthanc after {retries + 1} attempts.")
                return False
        
        if not orthanc_instance_id: # Should be caught above, but as a safeguard
             return False

        # 3. Retrieve the instance file from Orthanc
        instance_file_url = f"{ORTHANC_URL}/instances/{orthanc_instance_id}/file"
        retrieved_data = None
        for attempt in range(retries + 1):
            try:
                response = requests.get(instance_file_url, timeout=10)
                if response.status_code == 200:
                    retrieved_data = response.content
                    print("Instance data retrieved from Orthanc.")
                    break # Retrieved, exit retry loop
                else:
                    print(f"Failed to retrieve instance file from Orthanc. Status: {response.status_code}, Response: {response.text}")
            except requests.exceptions.RequestException as e:
                print(f"Error retrieving instance from Orthanc (attempt {attempt + 1}/{retries + 1}): {e}")
            
            if attempt < retries:
                print("Retrying retrieval...")
            elif retrieved_data is None: # Failed all retries
                print(f"Failed to retrieve instance {orthanc_instance_id} file from Orthanc after {retries + 1} attempts.")
                return False
        
        if retrieved_data is None: # Should be caught above
            return False

        # 4. Compare the retrieved data with the original data
        if original_data == retrieved_data:
            print(f"Verification successful: Retrieved data matches original data for SOPInstanceUID {sop_instance_uid}.")
            return True
        else:
            # Optional: more detailed comparison if needed, e.g. by parsing both and comparing specific tags
            print(f"Verification failed: Retrieved data does not match original data for SOPInstanceUID {sop_instance_uid}.")
            # For debugging, you could save both files or log lengths/hashes
            # print(f"Original data length: {len(original_data)}, Retrieved data length: {len(retrieved_data)}")
            return False
