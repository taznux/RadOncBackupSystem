# CLI Tools Detailed Documentation

This document provides detailed documentation for the command-line interface (CLI) tools available in the `src/cli/` directory of the RadOncBackupSystem.

## 1. Get Treatment Summary Report (`get_report.py`)

This script retrieves or generates a treatment summary report. Currently, its primary implementation is focused on fetching data from Mosaiq data sources using a pre-defined (though hypothetical in the base implementation) SQL query.

**Purpose**:
*   To provide users with a quick way to get a summary of treatment courses for a patient from a Mosaiq database.
*   To demonstrate how data can be extracted and presented from a clinical data source.

**Usage**:
```bash
python -m src.cli.get_report <environment_name> <mosaiq_source_alias> --mrn <patient_mrn> \
                             [--start_date <YYYY-MM-DD>] \
                             [--end_date <YYYY-MM-DD>] \
                             [--verbose]
```

**Arguments**:
*   `<environment_name>`: Name of the operational environment to use, as defined in `src/config/environments.toml`. (Required)
*   `<mosaiq_source_alias>`: Alias of the Mosaiq data source to query, as defined under `[environment_name.sources]` in `environments.toml`. This source must be of type "mosaiq". (Required)
*   `--mrn MRN`: Medical Record Number of the patient for whom the report is to be generated. (Required)
*   `--start_date YYYY-MM-DD`: Optional start date to filter treatment records. Records starting on or after this date will be included.
*   `--end_date YYYY-MM-DD`: Optional end date to filter treatment records. Records ending on or before this date will be included.
*   `--verbose`, `-v`: Enable verbose logging output.
*   `--help`, `-h`: Show the help message and exit.

**Configuration**:
This script uses `src/config/config_loader.py` to automatically load configurations (like environments, logging, DICOM settings) from TOML files located in the project's `src/config/` directory. No command-line arguments for configuration file paths are required.

**Example**:
```bash
python -m src.cli.get_report UCLA MOSAIQ_DB \
                             --mrn "PAT007" \
                             --start_date "2023-01-01" \
                             --end_date "2023-12-31"
```
This command will connect to the Mosaiq database specified by the `MOSAIQ_DB` source within the `UCLA` environment, query for treatment records for patient `PAT007` within the year 2023, and print the summary to the console.

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

## Backup Script (src/cli/backup.py)
### Purpose
The `backup.py` script is the primary tool for initiating DICOM data backups from configured source systems to designated backup targets. It orchestrates the querying of data from sources like ARIA, MIM, or Mosaiq, and manages its transfer and verification to a backup system (e.g., an Orthanc server).

### Usage
```bash
python -m src.cli.backup <environment_name> [source_alias]
```

### Arguments
*   `<environment_name>`: (Required) The name of the operational environment to use, as defined in `src/config/environments.toml`. This determines which set of configurations (sources, targets, settings) will be utilized.
*   `[source_alias]`: (Optional) The alias of a specific data source to back up, as defined under `[environment_name.sources]` in `environments.toml`. If this argument is omitted, the script will use the `default_source` specified for the given `<environment_name>`.

### Configuration
This script uses `src/config/config_loader.py` to automatically load configurations (environments, logging, DICOM settings) from TOML files located in the project's `src/config/` directory. No command-line arguments for configuration file paths are required. Key settings from `environments.toml` utilized by this script include:
*   `[EnvironmentName.script_ae]`: Defines the calling AET for the backup operations.
*   `[EnvironmentName.sources.YourSourceAlias]`: Contains connection details (AET, IP, port for DICOM; DB details for Mosaiq) and type for each data source.
*   `[EnvironmentName.backup_targets.YourBackupAlias]`: Contains AE details for backup destinations and staging SCPs.
*   `[EnvironmentName.default_source]`: Specifies the default source alias if not provided in arguments.
*   `[EnvironmentName.default_backup]`: Specifies the default backup target alias used for ARIA/MIM direct backups and as the final destination for Mosaiq's staged backups.
*   `[EnvironmentName.settings.mosaiq_backup_sql_query]`: The SQL query used to fetch records from Mosaiq.
*   `[EnvironmentName.settings.max_uids_per_run]`: Optional limit on instances processed per run for ARIA/MIM.
*   For Mosaiq sources, `staging_target_alias` within the source's configuration block points to an alias in `backup_targets` that defines the staging SCP.

### Key Workflows
*   **ARIA and MIM Sources:**
    1.  The script queries the source system using DICOM C-FIND based on criteria often configured in `[EnvironmentName.settings.dicom_query_keys]` or default parameters.
    2.  For each found instance (up to `max_uids_per_run` if set), a DICOM C-MOVE is initiated from the source system directly to the AE Title defined by `default_backup` for the environment.
    3.  If an Orthanc backup system interface is configured for the `default_backup` target, the script then verifies the existence of the moved instance on the backup target using C-FIND.
*   **Mosaiq Sources:**
    1.  The script queries the Mosaiq database using the SQL query specified in `[EnvironmentName.settings.mosaiq_backup_sql_query]`.
    2.  Each returned database record is converted into a DICOM dataset.
    3.  This DICOM dataset is sent via DICOM C-STORE to a *staging SCP*. The staging SCP's AE details are taken from the `backup_targets` alias specified by the `staging_target_alias` key in the Mosaiq source's configuration.
    4.  If the C-STORE to staging is successful, a DICOM C-MOVE is initiated to transfer the instance from the staging SCP to the final backup target (defined by `default_backup` for the environment).
    5.  Finally, if an Orthanc interface is configured for the `default_backup` target, existence is verified using C-FIND.

### Examples
```bash
# Back up the default source for the 'UCLA' environment
python -m src.cli.backup UCLA

# Back up a specific source 'ARIA_MAIN' for the 'UCLA' environment
python -m src.cli.backup UCLA ARIA_MAIN
```

## Validation Script (src/cli/validate.py)
### Purpose
The `validate.py` script is used to verify the consistency of DICOM data between a source system and a backup target (e.g., an Orthanc server). This typically involves retrieving data from both locations and comparing it.

### Usage
```bash
python -m src.cli.validate <environment_name> [source_alias] [backup_alias] [--log_level <LEVEL>]
```

### Arguments
*   `<environment_name>`: (Required) The name of the operational environment (defined in `environments.toml`).
*   `[source_alias]`: (Optional) The alias of the data source to validate against (defined under `[environment_name.sources]`). If omitted, uses the `default_source` for the environment.
*   `[backup_alias]`: (Optional) The alias of the backup target to validate (defined under `[environment_name.backup_targets]`). If omitted, uses the `default_backup` for the environment.
*   `[--log_level <LEVEL>]`: (Optional) Sets the logging level (e.g., DEBUG, INFO, WARNING, ERROR).

### Configuration
This script uses `src/config/config_loader.py` to automatically load configurations from TOML files in `src/config/`. No command-line arguments for configuration file paths are required. It utilizes AE details and other settings from `environments.toml` for the specified environment, source, and backup target.

### Workflow (Conceptual)
*(Review `src/cli/validate.py` to confirm and detail the workflow. The following is a general expectation based on its name and README description.)*
1.  Identifies instances to validate (e.g., based on a query to the source or a list).
2.  For each instance:
    a.  Retrieves the instance data from the specified `source_alias` (e.g., via C-MOVE or other source-specific methods).
    b.  Retrieves the corresponding instance data from the specified `backup_alias` (e.g., using the `Orthanc` backup system's C-GET capability).
    c.  Compares the data from the source and backup.
3.  Reports on any discrepancies found.

### Example
```bash
# Validate data for the default source and default backup in 'UCLA' environment
python -m src.cli.validate UCLA

# Validate 'ARIA_MAIN' source against 'ORTHANC_UCLA' backup in 'UCLA' environment
python -m src.cli.validate UCLA ARIA_MAIN ORTHANC_UCLA_BACKUP --log_level DEBUG
```

## 2. DICOM Network Utilities (`dicom_utils.py`)

A general-purpose command-line utility for performing common DICOM network operations. This tool allows direct interaction with DICOM-compliant systems (SCPs - Service Class Providers) for testing, querying, and data transfer.

**Configuration**:
This script uses `src/config/config_loader.py` to automatically load configurations, primarily for logging, from TOML files located in the project's `src/config/` directory. Most operational parameters (like AE details) are provided directly via command-line arguments rather than being sourced from `environments.toml`.

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
