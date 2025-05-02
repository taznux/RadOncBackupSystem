# Installation Guide
1. Clone respository to your machine and change to the cloned respository directory
2. Initialize your python environment
3. Install Python packages with
'pip install -r requirements.txt'
4. Set up the Flask application by configuring the necessary environment variables and running the application.

# Updating AETitles, IP Addresses, and Ports
1. Open the config_git_v1.toml file
    a. Update the AETitles, IP Addresses, and Ports
    b. The ports should always be integers, not strings
    c. "[local]" referes to your machine, "[rvs]" refers to the Record and Verify System, "[mim_server]" refers to the RTPACS server,   and "[mim_server_qr]" refers to the RTPACS query information
2. Logger configurations should be updated in logging_git_v1.toml. Ensure the change the SMTP email handler

# Backup System
- Purpose: To transfer RTRecord Objects from a Record and Verify System (RVS) to a RTPACS.
- Function 1: Backup program queries RVS for all RTRecords within a given time interval and generate a list of their UIDs. These UIDs are compared against a log file 'logs/daily_backup.log' which contain the UIDs of RTRecords successfully backed-up to the RTPacs. If the backup is successfull, the log 'daily_backup.log' will the UIDs of the backed-up RTRecords. The backup will retry seven different times if it initially fails, and will be added to 'logs/daily_failures.log'
- Function 2: Backup treatment plan information (RTPlan, RTStruct, CT) corresponding to each RTRecord from the RVS to the RTPACS. Running totals of each RTPlan, RTStruct, CT are updated in log files.
- The main backup program is 'scu_move_git_v1.py'. This script calls 'scu_find_git_v1.py' to query the RVS, and calls 'scu_move_support_git_v1.py' to backup treatment plan information corresponding to the RTRecords.

# Generate Treatment Report
1. Run 'get_treatment_report_git_v1.py' (make sure to adjust the study_start_date, treatment_start_date, and end_date variables before running)
2. Purpose: To generate a report of all patients currently undergoing RT Treatments including their current fraction number using back up records from MIM in case ARIA is unavailable
3. 'treatment_start_date' and 'end_date' specifies the date range of what the user considers to be a "patient currently undergoing treatment" to be included in the report (e.g. received a fraction within the past 7 days)
4. 'study_start_date' is when the study was first created. Since MIM queries in a hierarchial fashion from Study->Series->Image, 'study_start_date' should be set to long before the 'treatment_start_date' to ensure no patients are erroneously overlooked (recommended at least 1 month prior). However, setting 'study_start_date' too far into the past increases the number of patient cases MIM has to search through, which significantly increases the workload on the system.

# Setting Up Windows Task Scheduler
1. In 'Task Scheduler', click 'Create Task'
2. In 'Trigger' tab, click 'New' and set task to repeat 10 minutes, indefinitely
3. In 'Actions' tab, clicl 'New'
4. In the 'Program Script' field, provide the absolute path to 'python.exe'. Note: this could be in the conda environment
5. In the 'Add arguments (optional)' field, provide the absolute path to 'scu_move_git_v1.py'
6. In the 'Start in (optional)' field, provide the absolute path to this project directory

# Running the CLI Application
1. Navigate to the `src` directory.
2. Use the following commands to interact with the CLI application:
   - `python -m src.cli.query --config path/to/config.toml --source ARIA --mrn 12345 --study_date 20220101 --treatment_date 20220101`: Query information from data sources.
   - `python -m src.cli.backup UCLA`: Backup DICOM data for the specified environment (e.g., UCLA, TJU).
   - `python -m src.cli.validate path/to/config.toml UCLA`: Validate DICOM data for the specified environment (e.g., UCLA, TJU).

# Flask Application
The Flask application provides endpoints for configuring backups, viewing logs, and running recovery processes.

## Endpoints
- `POST /configure_backup`: Configure backup settings.
- `GET /view_logs`: View logs of different types.
- `POST /run_recovery`: Initiate a recovery process.

## Usage
1. Set up the Flask application by configuring the necessary environment variables.
2. Run the Flask application using the command: `python src/app.py`.
3. Use the provided endpoints to interact with the application.

# Backup Systems
The backup systems are responsible for storing and verifying DICOM data. The following backup systems are available:

- **Orthanc**: A DICOM server for healthcare and medical research.

For more details, refer to the `docs/backup_systems.md` file.

# Data Sources
The data sources provide DICOM data for backup and recovery processes. The following data sources are available:

- **ARIA**: A data source for ARIA systems.
- **MIM**: A data source for MIM systems.
- **Mosaiq**: A data source for Mosaiq systems.

For more details, refer to the `docs/data_sources.md` file.

# Test Files
The test files are used to verify the functionality of the backup and recovery processes. The following test files are available:

- **test_aria.py**: Tests for the ARIA data source.
- **test_mim.py**: Tests for the MIM data source.
- **test_mosaiq.py**: Tests for the Mosaiq data source.
- **test_orthanc.py**: Tests for the Orthanc backup system.

For more details, refer to the `docs/test_files.md` file.
