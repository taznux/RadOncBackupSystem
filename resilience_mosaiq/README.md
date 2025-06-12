# Purpose
- This repository contains code to generate RT Beams Treatment Record (RTRecord) files in DICOM format, based on SQL queries from the MOSAIQ database.
- It also includes utility scripts for converting DICOM files to text and retrieving DICOM files from a PACS server.

# Generating RTRecord Files
- Requires a .env file containing database credentials.
- Requires SQL query files located in app/db/*.sql for connecting to and querying the MOSAIQ database.
- generate_rtrecords.py: Main script to generate RTRecord files for each day based on the scheduled treatments. It creates one or more RTRecord files per treatment plan (potentially multiple per patient) and archives SITE_SETUP_NOTES in private records.
- Generated RTRecord files are saved in the ./rtrecords/ directory with a .dcm extension.
- The storescu tool can be used to send the generated RTRecord files to the MIM_ARCHIVE system (functionality has been tested).

# Utility Scripts
- read_dicom.py: Reads .dcm files and outputs their contents in a human-readable text format.
- dicom_retrieve.py: Provides sample code for testing DICOM C-FIND and C-GET operations.