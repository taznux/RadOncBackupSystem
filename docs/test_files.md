# Test Files

The test files are used to verify the functionality of the backup and recovery processes. The following test files are available:

- **test_aria.py**: Unit tests for the ARIA data source, using an internal mock DICOM server to simulate ARIA system interactions.
- **test_mim.py**: Unit tests for the MIM data source, using an internal mock DICOM server to simulate MIM system interactions.
- **test_mosaiq.py**: Unit tests for the Mosaiq data source, using an internal mock DICOM server for C-STORE operations.
- **test_orthanc.py**: Unit tests for the Orthanc backup system, using a mock HTTP server to simulate Orthanc's REST API.

## test_aria.py
This test file contains unit tests for the ARIA data source. It verifies the query and transfer methods of the ARIA class by interacting with a mock DICOM server that simulates ARIA responses for C-FIND and C-MOVE operations.

## test_mim.py
This test file contains unit tests for the MIM data source. It verifies the query and transfer methods of the MIM class by interacting with a mock DICOM server that simulates MIM responses for C-FIND and C-MOVE operations.

## test_mosaiq.py
This test file contains unit tests for the Mosaiq data source. It verifies the transfer method of the Mosaiq class by interacting with a mock DICOM server that simulates a C-STORE SCP. The query method, which interacts with a SQL database, is not covered by this mock DICOM server.

## test_orthanc.py
This test file contains unit tests for the Orthanc backup system. It verifies the store and verify methods of the Orthanc class by using a mock HTTP server to simulate Orthanc's REST API responses.
