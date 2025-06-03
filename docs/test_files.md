# Test Files

The test files are used to verify the functionality of the backup and recovery processes. The following test files are available:

- **test_aria.py**: Unit tests for the ARIA data source, using an internal mock DICOM server to simulate ARIA system interactions.
- **test_mim.py**: Unit tests for the MIM data source, using an internal mock DICOM server to simulate MIM system interactions.
- **test_mosaiq.py**: Unit tests for the Mosaiq data source, including database interaction mocks (e.g., for `pyodbc`) and DICOM C-STORE operations to a mock DICOM server. It also tests internal data parsing logic like MLC leaf data.
- **test_orthanc.py**: Unit tests for the Orthanc backup system. These tests verify the `confirm_instance_exists` and `verify` methods of the `Orthanc` class, primarily by mocking the calls to the public API of `src/cli/dicom_utils.py` (e.g., `perform_c_find`, `perform_c_get`).
- **test_dicom_utils.py**: Unit tests for the DICOM network utility functions in `src/cli/dicom_utils.py`. These tests focus on the public API (e.g., `perform_c_find`, `perform_c_get`, `perform_c_store`, `perform_c_echo`) and involve mocking `pynetdicom`'s underlying network operations to simulate various success and failure scenarios.

## test_aria.py
This test file contains unit tests for the ARIA data source. Since `ARIA` now inherits from `DicomQrDataSource`, these tests primarily ensure that ARIA correctly initializes the base class with `source_name="ARIA"`. The core C-FIND/C-MOVE logic is tested via `test_dicom_qr_source.py` (if created) or implicitly through tests of `dicom_utils.py` if those directly test the Q/R service classes. More specific ARIA tests would focus on any overridden or unique methods in the `ARIA` class itself.

## test_mim.py
This test file contains unit tests for the MIM data source. Similar to ARIA, `MIM` inherits from `DicomQrDataSource`. Tests verify correct initialization (`source_name="MIM"`) and any MIM-specific logic. The common C-FIND/C-MOVE functionality is tested elsewhere.

## test_mosaiq.py
This test file contains unit tests for the Mosaiq data source.
- It verifies the `transfer` method (DICOM C-STORE to a staging SCP) by interacting with a mock DICOM server.
- The `query` method, which interacts with a SQL database, is tested by mocking `pyodbc` calls to simulate database responses.
- **RT Record Generation Testing**: Specific tests are included to verify the logic within `_create_rt_record_dataset`, including the parsing of binary Multi-Leaf Collimator (MLC) data by `_parse_binary_leaf_data`. These tests use sample binary inputs (created using `struct.pack`) and assert the correctness of the parsed leaf position lists and the overall structure of the generated DICOM RT Record dataset.

## test_orthanc.py
This test file contains unit tests for the `Orthanc` backup system interface (`src/backup_systems/orthanc.py`).
- The tests focus on verifying the `confirm_instance_exists` (previously `store`) and `verify` methods.
- Instead of mocking Orthanc's HTTP REST API directly, these tests now primarily mock the calls made to the public API functions within `src/cli/dicom_utils.py` (i.e., `perform_c_find` and `perform_c_get`).
- By using `unittest.mock.patch` on these `dicom_utils` functions, the tests can simulate various outcomes of DICOM operations (e.g., instance found, instance not found, connection error, operation error) and assert that the `Orthanc` class methods handle these outcomes correctly.
- This approach decouples `test_orthanc.py` from the specifics of DICOM network communication and focuses on the logic within the `Orthanc` class itself.

## test_dicom_utils.py
This test file contains unit tests for the public API functions provided by `src/cli/dicom_utils.py`.
- Tests cover functions like `perform_c_find`, `perform_c_get`, `perform_c_store`, and `perform_c_echo`.
- These tests typically involve extensive mocking of `pynetdicom`'s `AE.associate`, `assoc.send_c_find`, `assoc.send_c_get`, etc., methods.
- The goal is to simulate various network and SCP responses (success, failure, specific statuses, no results) and ensure that the `perform_c_...` functions correctly handle these scenarios, return appropriate values, or raise the defined custom exceptions (`DicomOperationError`, `DicomConnectionError`, `InvalidInputError`).
- The preservation of CLI functionality (i.e., ensuring the `_handle_...` functions correctly call the `perform_c_...` functions and manage CLI output/exit codes) is also an important aspect, though direct CLI invocation might be tested in integration tests.
