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

    def confirm_instance_exists(self, sop_instance_uid: str, retries: int = 1) -> bool:
        """
        Verifies if a DICOM instance (identified by SOPInstanceUID) exists on the
        DICOM peer using C-FIND. This method does not store data itself but checks
        if external storage (e.g., via C-MOVE or C-STORE) was successful.
        (This method was previously named 'store').

        :param sop_instance_uid: The SOPInstanceUID of the DICOM instance to check.
        :type sop_instance_uid: str
        :param retries: Number of times to retry the C-FIND operation on failure. Defaults to 1.
        :type retries: int, optional
        :return: True if the instance is found on the peer, False otherwise.
        :rtype: bool
        """
        logger.info(
            f"Confirming existence of SOPInstanceUID {sop_instance_uid} on peer {self.peer_aet} "
            f"at {self.peer_host}:{self.peer_port} using C-FIND."
        )

        for attempt in range(retries + 1):
            try:
                # Call the new public API function from dicom_utils
                found_datasets = dicom_utils.perform_c_find(
                    calling_aet=self.calling_aet,
                    peer_aet=self.peer_aet,
                    peer_host=self.peer_host,
                    peer_port=self.peer_port,
                    query_level="IMAGE", # For specific SOPInstanceUID
                    patient_id="*",      # Wildcard for higher levels
                    study_uid="",        # Empty means match any
                    series_uid="",       # Empty means match any
                    sop_instance_uid=sop_instance_uid,
                    modality=""          # Not typically needed for IMAGE level SOP UID query
                )
                
                # If perform_c_find returns a non-empty list, the instance was found.
                if found_datasets: # Check if list is not empty
                    logger.info(
                        f"C-FIND successful for SOPInstanceUID {sop_instance_uid}. Instance exists on peer."
                    )
                    # Optionally, verify if the returned SOPInstanceUID matches the queried one,
                    # though for a specific IMAGE level query, it should.
                    # For example: if any(ds.SOPInstanceUID == sop_instance_uid for ds in found_datasets): return True
                    return True
                else:
                    # This case should ideally be covered by DicomOperationError("No instances found")
                    # from perform_c_find, but as a safeguard:
                    logger.warning(
                        f"C-FIND for SOPInstanceUID {sop_instance_uid} completed but returned no datasets. "
                        f"Assuming instance not found (attempt {attempt + 1}/{retries + 1})."
                    )
                    # This path might not be hit if perform_c_find strictly raises "No instances found"
                    return False

            except DicomOperationError as e:
                if e.status == 0x0000 and "No instances found" in str(e):
                    logger.info(
                        f"C-FIND for SOPInstanceUID {sop_instance_uid}: Instance not found on peer (attempt {attempt + 1}/{retries + 1})."
                    )
                    return False # Explicitly means not found
                logger.warning(
                    f"C-FIND operation for SOPInstanceUID {sop_instance_uid} failed (attempt {attempt + 1}/{retries + 1}): {e}"
                )
            except DicomConnectionError as e:
                logger.error(
                    f"C-FIND connection error for SOPInstanceUID {sop_instance_uid} (attempt {attempt + 1}/{retries + 1}): {e}"
                )
            except InvalidInputError as e:
                logger.error(f"Invalid input for C-FIND (attempt {attempt + 1}/{retries + 1}): {e}")
                return False # No retry for this
            except Exception as e:
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
        2. Checking existence using `self.confirm_instance_exists()`.
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
        except InvalidDicomError as e:
            logger.error(f"Invalid DICOM data provided for verification: {e}", exc_info=True)
            raise
        except Exception as e: 
            logger.error(f"Error parsing original DICOM data to get SOPInstanceUID: {e}", exc_info=True)
            raise InvalidDicomError(f"Failed to parse SOPInstanceUID from data: {e}")

        # 1. C-FIND Check using the refactored method
        if not self.confirm_instance_exists(sop_instance_uid, retries=retries):
            logger.error(f"Instance {sop_instance_uid} not found on peer {self.peer_aet} via C-FIND. Verification cannot proceed.")
            return False

        # 2. C-GET Implementation
        temp_dir = ""
        try:
            temp_dir = tempfile.mkdtemp()
            logger.debug(f"Created temporary directory for C-GET: {temp_dir}")

            retrieved_successfully = False
            for attempt in range(retries + 1):
                try:
                    logger.debug(f"Verify Step: Attempting C-GET for {sop_instance_uid} to {temp_dir} (attempt {attempt+1})")
                    dicom_utils.perform_c_get(
                        calling_aet=self.calling_aet,
                        peer_aet=self.peer_aet,
                        peer_host=self.peer_host,
                        peer_port=self.peer_port,
                        output_directory=temp_dir,
                        # For C-GET of a specific instance, SOPInstanceUID is primary.
                        # Other UIDs might be helpful for some SCPs or specific C-GET models if not CompositeInstanceRoot.
                        sop_instance_uid=sop_instance_uid
                        # study_uid, series_uid could be extracted from original_data if needed by SCP,
                        # but typically SOPInstanceUID is enough for IMAGE level with CompositeInstanceRootRetrieveGet.
                    )

                    retrieved_file_path = os.path.join(temp_dir, f"{sop_instance_uid}.dcm")
                    if os.path.exists(retrieved_file_path) and os.path.getsize(retrieved_file_path) > 0:
                        logger.info(f"Verify Step: C-GET successful. Instance {sop_instance_uid} retrieved to {retrieved_file_path}.")
                        retrieved_successfully = True
                        break
                    else:
                        logger.error(f"Verify Step: C-GET for {sop_instance_uid} reported success by dicom_utils, "
                                     f"but output file {retrieved_file_path} not found or is empty (attempt {attempt+1}).")
                        # This implies perform_c_get might not raise an error if the file isn't saved,
                        # which depends on its internal logic (e.g., if 0 completed ops is not an error).
                        # The check `if completed_ops == 0 and ... query_level == "IMAGE"` in perform_c_get
                        # logs a warning but doesn't raise. This is where we explicitly fail.

                except DicomOperationError as e:
                    logger.error(f"Verify Step: C-GET operation for {sop_instance_uid} failed (attempt {attempt+1}): {e}")
                except DicomConnectionError as e:
                    logger.error(f"Verify Step: C-GET connection error for {sop_instance_uid} (attempt {attempt+1}): {e}")
                except InvalidInputError as e:
                    logger.error(f"Verify Step: Invalid input for C-GET for {sop_instance_uid} (attempt {attempt+1}): {e}")
                except Exception as e:
                    logger.error(f"Verify Step: Unexpected error during C-GET for {sop_instance_uid} (attempt {attempt+1}): {e}", exc_info=True)
                
                if attempt < retries:
                    logger.info(f"Retrying C-GET for {sop_instance_uid}...")
            
            if not retrieved_successfully:
                logger.error(f"Failed to retrieve {sop_instance_uid} via C-GET after {retries+1} attempts.")
                return False

            # 3. Verification and Cleanup
            retrieved_file_path = os.path.join(temp_dir, f"{sop_instance_uid}.dcm")
            logger.debug(f"Reading retrieved file: {retrieved_file_path}")
            
            with open(retrieved_file_path, 'rb') as f_retrieved:
                retrieved_data = f_retrieved.read()

            if original_data == retrieved_data:
                logger.info(f"Verification successful: Retrieved data matches original data for SOPInstanceUID {sop_instance_uid}.")
                return True
            else:
                logger.warning(
                    f"Verification failed: Retrieved data does not match original data for SOPInstanceUID {sop_instance_uid}."
                )
                logger.debug(f"Original data length: {len(original_data)}, Retrieved data length: {len(retrieved_data)}")
                # For deeper debugging, one might save both original_data and retrieved_data to files.
                # e.g., with open(os.path.join(temp_dir, "original_for_debug.dcm"), "wb") as f: f.write(original_data)
                # with open(os.path.join(temp_dir, "retrieved_for_debug.dcm"), "wb") as f: f.write(retrieved_data)
                return False

        except Exception as e: # General catch for unexpected issues in C-GET or verification logic
            logger.error(f"An error occurred during the verification process for {sop_instance_uid}: {e}", exc_info=True)
            return False
        finally:
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                    logger.debug(f"Successfully removed temporary directory: {temp_dir}")
                except Exception as e:
                    logger.error(f"Failed to remove temporary directory {temp_dir}: {e}")
