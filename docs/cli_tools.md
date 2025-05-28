# CLI Tools Detailed Documentation

This document provides detailed documentation for the command-line interface (CLI) tools available in the `src/cli/` directory of the RadOncBackupSystem.

## 1. Get Treatment Summary Report (`get_report.py`)

This script retrieves or generates a treatment summary report. Currently, its primary implementation is focused on fetching data from Mosaiq data sources using a pre-defined (though hypothetical in the base implementation) SQL query.

**Purpose**:
*   To provide users with a quick way to get a summary of treatment courses for a patient from a Mosaiq database.
*   To demonstrate how data can be extracted and presented from a clinical data source.

**Usage**:
```bash
python -m src.cli.get_report --environments_config <path_to_environments.toml> \
                             --dicom_config <path_to_dicom.toml> \
                             --environment <mosaiq_environment_name> \
                             --mrn <patient_mrn> \
                             [--start_date <YYYY-MM-DD>] \
                             [--end_date <YYYY-MM-DD>] \
                             [--verbose]
```

**Arguments**:
*   `--environments_config PATH`: Path to the environments configuration file (e.g., `src/config/environments.toml`). (Required)
*   `--dicom_config PATH`: Path to the DICOM/Database configuration file (e.g., `src/config/dicom.toml`). This file should contain the Mosaiq database connection details under a key matching the `source` in the specified environment. (Required)
*   `--environment NAME`: Name of the Mosaiq environment to use, as defined in the environments configuration file. This environment entry must specify `"Mosaiq"` as its source. (Required)
*   `--mrn MRN`: Medical Record Number of the patient for whom the report is to be generated. (Required)
*   `--start_date YYYY-MM-DD`: Optional start date to filter treatment records. Records starting on or after this date will be included.
*   `--end_date YYYY-MM-DD`: Optional end date to filter treatment records. Records ending on or before this date will be included.
*   `--verbose`, `-v`: Enable verbose logging output.
*   `--help`, `-h`: Show the help message and exit.

**Example**:
```bash
python -m src.cli.get_report --environments_config src/config/environments.toml \
                             --dicom_config src/config/dicom.toml \
                             --environment TJU_MOSAIQ \
                             --mrn "PAT007" \
                             --start_date "2023-01-01" \
                             --end_date "2023-12-31"
```
This command will connect to the Mosaiq database specified in the `TJU_MOSAIQ` environment, query for treatment records for patient `PAT007` within the year 2023, and print the summary to the console.

**Output Format**:
The script prints a formatted table to the console with columns such as:
*   `PatientName`
*   `PatientMRN`
*   `StartDate`
*   `EndDate`
*   `TotalDose`
*   `NumberOfFractions`
*   `TargetVolume`
(Note: The exact fields depend on the SQL query defined in `src/data_sources/mosaiq.py`'s `get_treatment_summary_report` method.)

## 2. DICOM Network Utilities (`dicom_utils.py`)

A general-purpose command-line utility for performing common DICOM network operations. This tool allows direct interaction with DICOM-compliant systems (SCPs - Service Class Providers) for testing, querying, and data transfer.

**Common Arguments for all sub-commands**:
These arguments must be provided for each sub-command (`echo`, `find`, `move`, `store`).
*   `--aet CALLING_AETITLE`: Specifies the Application Entity Title (AET) of your client (SCU - Service Class User). Default: `DICOMUTILS`.
*   `--aec CALLED_AETITLE`: Specifies the AET of the remote DICOM server (SCP) you are connecting to. (Required)
*   `--host HOSTNAME_OR_IP`: The hostname or IP address of the remote SCP. (Required)
*   `--port PORT_NUMBER`: The port number on which the remote SCP is listening. (Required)
*   `--verbose`, `-v`: Enable verbose logging for pynetdicom, useful for debugging communication.
*   `--help`, `-h`: Show the help message for the specific command or sub-command and exit.

---

### 2.1. C-ECHO SCU (`echo`)

Used to verify basic DICOM connectivity with a remote AE (Application Entity). It sends a DICOM C-ECHO request and waits for a response.

**Usage**:
```bash
python -m src.cli.dicom_utils echo --aec <SCP_AETITLE> --host <SCP_IP> --port <SCP_PORT> [common_args...]
```

**Example**:
```bash
python -m src.cli.dicom_utils echo --aec ORTHANCSCP --host 192.168.1.100 --port 4242 --aet MYSCU -v
```

---

### 2.2. C-FIND SCU (`find`)

Used to query a remote AE for DICOM objects (e.g., patients, studies, series, images) based on specified criteria.

**Usage**:
```bash
python -m src.cli.dicom_utils find --aec <SCP_AETITLE> --host <SCP_IP> --port <SCP_PORT> \
                                  [--query-level <LEVEL>] \
                                  [--patient-id <ID>] \
                                  [--study-uid <UID>] \
                                  [--series-uid <UID>] \
                                  [--modality <MODALITY>] \
                                  [common_args...]
```

**Specific Arguments for `find`**:
*   `--query-level {PATIENT,STUDY,SERIES,IMAGE}`: Specifies the level of the query. Default: `STUDY`.
*   `--patient-id PATIENT_ID`: Patient ID for the query.
*   `--study-uid STUDY_INSTANCE_UID`: Study Instance UID for the query.
*   `--series-uid SERIES_INSTANCE_UID`: Series Instance UID for the query.
*   `--modality MODALITY`: Modality for the query (e.g., CT, MR, RTPLAN, RTDOSE, RTSTRUCT).

**Example**:
```bash
python -m src.cli.dicom_utils find --aec VARIAN_AE --host aria.example.com --port 104 \
                                  --patient-id "PAT12345" --query-level STUDY --modality RTPLAN
```
This command queries the `VARIAN_AE` for all RTPLAN studies belonging to patient `PAT12345`.

---

### 2.3. C-MOVE SCU (`move`)

Used to request a remote AE (SCP) to initiate a C-STORE transfer of specified DICOM objects to another AE (the "move destination").

**Usage**:
```bash
python -m src.cli.dicom_utils move --aec <SCP_AETITLE> --host <SCP_IP> --port <SCP_PORT> \
                                  --move-dest-aet <MOVE_DESTINATION_AETITLE> \
                                  [--query-level <LEVEL>] \
                                  [--patient-id <ID>] \
                                  [--study-uid <UID>] \
                                  [--series-uid <UID>] \
                                  [common_args...]
```

**Specific Arguments for `move`**:
*   `--move-dest-aet MOVE_DESTINATION_AETITLE`: The AE Title of the DICOM node where the data should be sent. (Required)
*   `--query-level {PATIENT,STUDY,SERIES}`: Specifies the level of the objects to be moved. Default: `STUDY`.
*   `--patient-id PATIENT_ID`: Patient ID for selecting what to move.
*   `--study-uid STUDY_INSTANCE_UID`: Study Instance UID for selecting what to move.
*   `--series-uid SERIES_INSTANCE_UID`: Series Instance UID for selecting what to move (typically used with `--query-level SERIES`).

**Example**:
```bash
python -m src.cli.dicom_utils move --aec MIM_AE --host mim.example.com --port 104 \
                                  --move-dest-aet ORTHANC_BACKUP \
                                  --study-uid "1.2.840.113619.2.XYZ..."
```
This command requests `MIM_AE` to send the study with the specified Study Instance UID to the `ORTHANC_BACKUP` AE.

---

### 2.4. C-STORE SCU (`store`)

Used to send (store) DICOM files from your local machine to a remote AE.

**Usage**:
```bash
python -m src.cli.dicom_utils store --aec <SCP_AETITLE> --host <SCP_IP> --port <SCP_PORT> \
                                   --filepath <PATH_TO_FILE_OR_DIRECTORY> \
                                   [common_args...]
```

**Specific Arguments for `store`**:
*   `--filepath PATH_TO_FILE_OR_DIRECTORY`: Path to a single DICOM file or a directory containing DICOM files to send. If a directory is specified, the script will attempt to find and send all DICOM files within it. (Required)

**Example**:
```bash
python -m src.cli.dicom_utils store --aec ORTHANC_SCP --host orthanc.example.com --port 4242 \
                                   --filepath /dicom_data/patient_rtplan.dcm
```
This command sends the DICOM file `patient_rtplan.dcm` to `ORTHANC_SCP`.

If a directory is specified:
```bash
python -m src.cli.dicom_utils store --aec ORTHANC_SCP --host orthanc.example.com --port 4242 \
                                   --filepath /dicom_data/series_for_upload/
```
This command attempts to send all DICOM files found in the `/dicom_data/series_for_upload/` directory.

---

This document provides a comprehensive overview of the CLI tools. For specific command-line options not detailed here, always refer to the `--help` flag associated with each script or sub-command.
For example: `python -m src.cli.dicom_utils find --help`.
