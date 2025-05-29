"""
Orthanc Backup System Interface (DICOM C-FIND/C-GET based).

This module provides the `Orthanc` class, an implementation of the `BackupSystem`
interface, for interacting with a DICOM peer (e.g., an Orthanc server configured for DICOM communication).
It uses DICOM C-FIND to verify storage (existence) and C-GET to retrieve instances for verification.
Actual storage (C-STORE or C-MOVE) to the peer is expected to be handled by other processes.
"""
import os
import tempfile
import shutil
import logging
import io # For creating a file-like object from bytes

import pydicom
from pydicom.errors import InvalidDicomError
from argparse import Namespace

from . import BackupSystem
from ..cli import dicom_utils # Assuming dicom_utils.py is in src/cli/
from ..cli.dicom_utils import DicomOperationError, DicomConnectionError, InvalidInputError # Import specific exceptions

logger = logging.getLogger(__name__)


class Orthanc(BackupSystem):
    """
    Implements the BackupSystem interface using DICOM C-FIND and C-GET.

    This class interacts with a generic DICOM peer (which could be an Orthanc server)
    to verify the existence and content of DICOM instances. It assumes that the
    actual storage of instances to the peer is handled externally (e.g., via C-MOVE
    from another system or C-STORE from the application).
    """

    def __init__(self, calling_aet: str, peer_aet: str, peer_host: str, peer_port: int):
        """
        Initializes the Orthanc backup system interface for DICOM communication.

        :param calling_aet: The Application Entity Title (AET) of this client.
        :type calling_aet: str
        :param peer_aet: The AET of the DICOM peer (e.g., Orthanc SCP).
        :type peer_aet: str
        :param peer_host: The hostname or IP address of the DICOM peer.
        :type peer_host: str
        :param peer_port: The port number of the DICOM peer.
        :type peer_port: int
        """
        super().__init__()
        self.calling_aet = calling_aet
        self.peer_aet = peer_aet
        self.peer_host = peer_host
        self.peer_port = peer_port
        logger.debug(
            f"Orthanc (DICOM) BackupSystem initialized for peer AET {self.peer_aet} "
            f"at {self.peer_host}:{self.peer_port}, calling AET: {self.calling_aet}"
        )

    def store(self, sop_instance_uid: str, retries: int = 1) -> bool:
        """
        Verifies if a DICOM instance (identified by SOPInstanceUID) exists on the
        DICOM peer using C-FIND. This method does not store data itself but checks
        if external storage (e.g., via C-MOVE or C-STORE) was successful.

        :param sop_instance_uid: The SOPInstanceUID of the DICOM instance to check.
        :type sop_instance_uid: str
        :param retries: Number of times to retry the C-FIND operation on failure. Defaults to 1.
        :type retries: int, optional
        :return: True if the instance is found on the peer, False otherwise.
        :rtype: bool
        """
        logger.info(
            f"Verifying existence of SOPInstanceUID {sop_instance_uid} on peer {self.peer_aet} "
            f"at {self.peer_host}:{self.peer_port} using C-FIND."
        )

        find_args = Namespace(
            aet=self.calling_aet,
            aec=self.peer_aet,
            host=self.peer_host,
            port=self.peer_port,
            query_level="IMAGE",
            patient_id="*", # Universal matching for patient/study/series context
            study_uid="",
            series_uid="",
            sop_instance_uid=sop_instance_uid, # Specific UID we are looking for
            modality="", # Not relevant for IMAGE level specific UID query
            verbose=False, # Or configure based on global settings
        )

        for attempt in range(retries + 1):
            try:
                # Assuming _handle_find_scu is modified to:
                # 1. Not call sys.exit()
                # 2. Raise DicomOperationError if the C-FIND completes but no matching instance is found
                #    (e.g., final status is not Success or no identifiers returned for this specific UID)
                # 3. Raise DicomConnectionError for association issues.
                # The current _handle_find_scu iterates and logs. We rely on it raising an error
                # if the final status of the C-FIND operation indicates failure or no results for the specific UID.
                # A more robust approach would be for _handle_find_scu to return a boolean or count.
                
                # Iterating through the responses to ensure the operation completes.
                # The presence of the SOPInstanceUID in a successful response would confirm existence.
                # However, _handle_find_scu itself processes responses via _on_find_response.
                # If _on_find_response, upon receiving the final 'Success' status,
                # doesn't find any matching identifier for the specific UID, it should ideally trigger
                # DicomOperationError from _handle_find_scu.
                
                # For this implementation, we assume _handle_find_scu's normal completion (no exception)
                # after querying for a specific SOPInstanceUID at IMAGE level implies it was found.
                # This is based on the note: "if _handle_find_scu completes without raising an exception ... consider it found"
                
                # We need to consume the iterator from send_c_find if _handle_find_scu is not fully processing it.
                # However, the current _handle_find_scu *does* iterate through responses.
                # The key is how it signals "not found" for a *specific* UID at IMAGE level.
                # It raises DicomOperationError if status_rsp.Status is not Success/Pending.
                # If C-FIND completes with Success but no matching UID, this logic needs adjustment
                # or _handle_find_scu needs to be smarter.
                # For now, we assume _handle_find_scu will raise error if not found.

                dicom_utils._handle_find_scu(find_args)
                logger.info(
                    f"C-FIND successful for SOPInstanceUID {sop_instance_uid}. Instance assumed to exist on peer."
                )
                return True # If _handle_find_scu completes without error, assume found.
            except DicomOperationError as e:
                # This could mean "not found" or other C-FIND operational errors.
                logger.warning(
                    f"C-FIND operation for SOPInstanceUID {sop_instance_uid} failed (attempt {attempt + 1}/{retries + 1}): {e}"
                )
            except DicomConnectionError as e:
                logger.error(
                    f"C-FIND connection error for SOPInstanceUID {sop_instance_uid} (attempt {attempt + 1}/{retries + 1}): {e}"
                )
            except InvalidInputError as e: # Should not happen with internally constructed args
                logger.error(f"Invalid input for C-FIND (attempt {attempt + 1}/{retries + 1}): {e}")
                return False # No retry for this
            except Exception as e: # Catch any other unexpected errors from dicom_utils
                logger.error(f"Unexpected error during C-FIND for {sop_instance_uid} (attempt {attempt + 1}/{retries + 1}): {e}", exc_info=True)

            if attempt < retries:
                logger.info(f"Retrying C-FIND for {sop_instance_uid}...")
        
        logger.error(
            f"All {retries + 1} C-FIND attempts failed for SOPInstanceUID {sop_instance_uid} on peer {self.peer_aet}."
        )
        return False

    def verify(self, original_data: bytes, retries: int = 1) -> bool:
        """
        Verifies if DICOM data exists on the peer and matches the original.

        Verification involves:
        1. Parsing SOPInstanceUID from `original_data`.
        2. Checking existence using C-FIND (similar to `store` method).
        3. If found, retrieving the instance using C-GET to a temporary location.
        4. Performing a byte-by-byte comparison between `original_data` and retrieved data.
        5. Cleaning up the temporary location.

        :param original_data: Raw original DICOM data as bytes.
        :type original_data: bytes
        :param retries: Number of times to retry C-FIND/C-GET operations on failure. Defaults to 1.
        :type retries: int, optional
        :return: True if verification is successful, False otherwise.
        :rtype: bool
        :raises pydicom.errors.InvalidDicomError: If `original_data` is not valid DICOM.
        """
        sop_instance_uid = ""
        try:
            dicom_file = pydicom.dcmread(io.BytesIO(original_data), stop_before_pixels=True, force=True)
            sop_instance_uid = str(dicom_file.SOPInstanceUID)
            if not sop_instance_uid:
                logger.error("Could not parse SOPInstanceUID from original data for verification.")
                return False
            logger.info(f"Verifying SOPInstanceUID: {sop_instance_uid} on peer {self.peer_aet}")
        except InvalidDicomError as e: # More specific exception
            logger.error(f"Invalid DICOM data provided for verification: {e}", exc_info=True)
            raise # Re-raise as it's an input data problem
        except Exception as e: 
            logger.error(f"Error parsing original DICOM data to get SOPInstanceUID: {e}", exc_info=True)
            # Depending on policy, might re-raise or return False. Let's re-raise for unexpected parsing issues.
            raise InvalidDicomError(f"Failed to parse SOPInstanceUID from data: {e}")


        # 1. C-FIND Check (reusing logic from store, effectively)
        # We can call self.store which is now a C-FIND check, but it might be clearer to repeat the core logic
        # or factor out the C-FIND part if it becomes more complex.
        # For now, let's be explicit for clarity in the verify method.
        
        find_args = Namespace(
            aet=self.calling_aet, aec=self.peer_aet, host=self.peer_host, port=self.peer_port,
            query_level="IMAGE", patient_id="*", study_uid="", series_uid="",
            sop_instance_uid=sop_instance_uid, modality="", verbose=False
        )
        found_on_peer = False
        for attempt in range(retries + 1):
            try:
                logger.debug(f"Verify Step: Attempting C-FIND for {sop_instance_uid} (attempt {attempt+1})")
                dicom_utils._handle_find_scu(find_args) # Assumes error if not found
                logger.info(f"Verify Step: C-FIND successful for {sop_instance_uid}. Instance exists on peer.")
                found_on_peer = True
                break
            except DicomOperationError as e:
                logger.warning(f"Verify Step: C-FIND for {sop_instance_uid} failed (attempt {attempt+1}): {e}")
            except DicomConnectionError as e:
                logger.error(f"Verify Step: C-FIND connection error for {sop_instance_uid} (attempt {attempt+1}): {e}")
            except Exception as e:
                 logger.error(f"Verify Step: Unexpected error during C-FIND for {sop_instance_uid} (attempt {attempt+1}): {e}", exc_info=True)

            if attempt < retries:
                logger.info(f"Retrying C-FIND for {sop_instance_uid} in verify step...")
        
        if not found_on_peer:
            logger.error(f"Instance {sop_instance_uid} not found on peer {self.peer_aet} via C-FIND. Verification cannot proceed.")
            return False

        # 2. C-GET Implementation
        temp_dir = ""
        try:
            temp_dir = tempfile.mkdtemp()
            logger.debug(f"Created temporary directory for C-GET: {temp_dir}")

            get_args = Namespace(
                aet=self.calling_aet, aec=self.peer_aet, host=self.peer_host, port=self.peer_port,
                patient_id="", study_uid="", series_uid="", # SOPInstanceUID is primary key
                sop_instance_uid=sop_instance_uid,
                out_dir=temp_dir,
                verbose=False, # Or configure
            )

            retrieved_successfully = False
            for attempt in range(retries + 1):
                try:
                    logger.debug(f"Verify Step: Attempting C-GET for {sop_instance_uid} to {temp_dir} (attempt {attempt+1})")
                    dicom_utils._handle_get_scu(get_args) # Assumes error on failure
                    # _handle_get_scu uses _on_get_response to save the file.
                    # The filename is based on SOPInstanceUID.dcm.
                    retrieved_file_path = os.path.join(temp_dir, f"{sop_instance_uid}.dcm")
                    if os.path.exists(retrieved_file_path):
                        logger.info(f"Verify Step: C-GET successful. Instance {sop_instance_uid} retrieved to {retrieved_file_path}.")
                        retrieved_successfully = True
                        break
                    else:
                        # This case implies C-GET command itself succeeded (no exception from _handle_get_scu)
                        # but the file was not saved as expected by _on_get_response.
                        # This could happen if _on_get_response had an issue but returned 0x0000.
                        logger.error(f"Verify Step: C-GET for {sop_instance_uid} reported success, but output file {retrieved_file_path} not found (attempt {attempt+1}).")
                        # This might be treated as a failure of the C-GET operation.
                except DicomOperationError as e:
                    logger.error(f"Verify Step: C-GET operation for {sop_instance_uid} failed (attempt {attempt+1}): {e}")
                except DicomConnectionError as e:
                    logger.error(f"Verify Step: C-GET connection error for {sop_instance_uid} (attempt {attempt+1}): {e}")
                except InvalidInputError as e: # Should not happen
                    logger.error(f"Verify Step: Invalid input for C-GET for {sop_instance_uid} (attempt {attempt+1}): {e}")
                    # No retry for this usually
                except Exception as e:
                    logger.error(f"Verify Step: Unexpected error during C-GET for {sop_instance_uid} (attempt {attempt+1}): {e}", exc_info=True)
                
                if attempt < retries:
                    logger.info(f"Retrying C-GET for {sop_instance_uid}...")
            
            if not retrieved_successfully:
                logger.error(f"Failed to retrieve {sop_instance_uid} via C-GET after {retries+1} attempts.")
                return False # Return False, finally block will clean up temp_dir

            # 3. Verification and Cleanup
            retrieved_file_path = os.path.join(temp_dir, f"{sop_instance_uid}.dcm")
            logger.debug(f"Reading retrieved file: {retrieved_file_path}")
            
            retrieved_dcm = pydicom.dcmread(retrieved_file_path, force=True)
            
            # To compare bytes, we need to write the retrieved dataset to a BytesIO object
            # This ensures consistent byte representation if pydicom made any alterations on read
            # (e.g. related to file meta information if not present, or private tags).
            # Using write_like_original=True is important if the original data had specific encoding.
            # However, original_data is what we have. The SCP might have altered the instance (e.g. coercion).
            # A simple byte comparison might fail if the SCP is not perfectly preserving.
            # For now, strict byte comparison is implemented as per instructions.
            
            retrieved_data_bytesIO = io.BytesIO()
            # Ensure file_meta is written if it was present in the original, or a default one if not.
            # pydicom.dcmwrite by default creates new file_meta if not present on dataset.
            pydicom.dcmwrite(retrieved_data_bytesIO, retrieved_dcm, write_like_original=True) # Crucial for comparison
            retrieved_data = retrieved_data_bytesIO.getvalue()

            if original_data == retrieved_data:
                logger.info(f"Verification successful: Retrieved data matches original data for SOPInstanceUID {sop_instance_uid}.")
                return True
            else:
                logger.warning(
                    f"Verification failed: Retrieved data does not match original data for SOPInstanceUID {sop_instance_uid}."
                )
                logger.debug(f"Original data length: {len(original_data)}, Retrieved data length: {len(retrieved_data)}")
                # For deeper debugging, one might save both original_data and retrieved_data to files.
                # e.g., with open(os.path.join(temp_dir, "original.dcm"), "wb") as f: f.write(original_data)
                return False

        except Exception as e:
            logger.error(f"An error occurred during the verification process for {sop_instance_uid}: {e}", exc_info=True)
            return False # General failure
        finally:
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                    logger.debug(f"Successfully removed temporary directory: {temp_dir}")
                except Exception as e:
                    logger.error(f"Failed to remove temporary directory {temp_dir}: {e}")
