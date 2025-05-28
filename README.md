# RadOncBackupSystem

A DICOM Backup and Recovery System for Radiation Oncology workflows. This system provides tools to backup data from various sources like ARIA, MIM, and Mosaiq to a central backup system (e.g., Orthanc), and to query and validate this data. It includes a Flask web application for interaction and CLI scripts for batch operations.

For detailed documentation on specific components, please refer to the `docs/` directory.

## Installation

1.  **Clone Repository**:
    ```bash
    git clone <repository_url>
    cd RadOncBackupSystem
    ```
2.  **Create Python Environment**:
    It's recommended to use a virtual environment (e.g., venv, conda).
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```
3.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

## Configuration

The system uses TOML configuration files located in the `src/config/` directory.

1.  **DICOM Application Entities (`src/config/dicom.toml`)**:
    *   Define AE Titles, IP addresses, and Ports for all relevant DICOM systems (sources, backup destinations).
    *   Example:
        ```toml
        [ARIA_AE]
        AETitle = "ARIA_SCU"
        IP = "192.168.1.100"
        Port = 104 
        ```
    *   **Note**: Port values must be integers.
    *   For Mosaiq database connections, include a `[db_config]` sub-table under the Mosaiq AE entry if you are using `src/cli/backup.py` for Mosaiq SQL queries:
        ```toml
        [Mosaiq] # This is the source_name used in environments.toml
        # ... other Mosaiq AE details if it also acts as a DICOM AE ...
        db_config = { server = "MOSAIQ_DB_IP", database = "MOSAIQ_DB_NAME", username = "user", password = "pw" }
        ```


2.  **Environments (`src/config/environments.toml`)**:
    *   Define different operational environments (e.g., clinical sites like UCLA, TJU_Mosaiq, TJU_MIM).
    *   Each environment specifies its data `source` (name matching an entry in `dicom.toml`), `backup` target (name matching an entry in `dicom.toml`), and any source-specific configurations.
    *   For Mosaiq sources, specify the ODBC driver:
        ```toml
        [TJU_Mosaiq]
        source = "Mosaiq" # Must match a key in dicom.toml
        backup = "ORTHANC_BACKUP" # Must match a key in dicom.toml
        mosaiq_odbc_driver = "ODBC Driver 17 for SQL Server" 
        ```

3.  **Logging (`src/config/logging.toml`)**:
    *   Configure formatters, handlers (console, file, email), and loggers for different modules.
    *   Review and update handler settings, especially for file paths and the `SMTPHandler` (e.g., `mailhost`, `fromaddr`, `toaddrs`, `subject`) if email notifications are desired.

## Running the CLI Applications

All CLI scripts are located in `src/cli/` and should be run as Python modules from the project's root directory. Configuration files are typically found automatically if the scripts are run from the root, or paths can be specified.

1.  **Backup Data (`backup.py`)**:
    *   Backs up DICOM data for a specified environment.
    *   Usage:
        ```bash
        python -m src.cli.backup <environment_name>
        ```
    *   Example:
        ```bash
        python -m src.cli.backup TJU_Mosaiq
        ```
    *   (Default config paths are `src/config/environments.toml` and `src/config/dicom.toml`)

2.  **Query Data Sources (`query.py`)**:
    *   Queries information from data sources using DICOM C-FIND.
    *   Note: For Mosaiq, this script currently only sets up the data source; actual SQL query execution based on arguments is not implemented. It's primarily for DICOM Q/R sources.
    *   Usage:
        ```bash
        python -m src.cli.query --environments_config <path_to_environments.toml> \
                                  --dicom_config <path_to_dicom.toml> \
                                  --environment <env_name> \
                                  [--mrn <mrn>] [--treatment_date <date>] [--study_date <date>]
        ```
    *   Example:
        ```bash
        python -m src.cli.query --environments_config src/config/environments.toml \
                                  --dicom_config src/config/dicom.toml \
                                  --environment UCLA --mrn "PAT123"
        ```

3.  **Validate Data (`validate.py`)**:
    *   Validates DICOM data consistency between a source and the Orthanc backup.
    *   Retrieves data from the source via C-MOVE and verifies against Orthanc using its REST API.
    *   Usage:
        ```bash
        python -m src.cli.validate <environment_name> \
                                  [--env_config <path_to_environments.toml>] \
                                  [--dicom_config <path_to_dicom.toml>] \
                                  [--log_level <DEBUG|INFO|WARNING|ERROR>]
        ```
    *   Example:
        ```bash
        python -m src.cli.validate TJU_Mosaiq --log_level DEBUG
        ```
    *   (Default config paths are `src/config/environments.toml` and `src/config/dicom.toml`)

4.  **Get Treatment Summary Report (`get_report.py`)**:
    *   Retrieves or generates a treatment summary report, currently focused on Mosaiq data sources.
    *   This script uses the `get_treatment_summary_report` method of the `Mosaiq` data source class.
    *   Usage:
        ```bash
        python -m src.cli.get_report --environments_config <path_to_environments.toml> \
                                     --dicom_config <path_to_dicom.toml> \
                                     --environment <mosaiq_env_name> \
                                     --mrn <patient_mrn> \
                                     [--start_date <YYYY-MM-DD>] [--end_date <YYYY-MM-DD>]
        ```
    *   Example:
        ```bash
        python -m src.cli.get_report --environment TJU_MOSAIQ --mrn "PAT007" --start_date "2023-01-01"
        ```
    *   For full details on all options, run `python -m src.cli.get_report --help`.
    *   More detailed documentation is available in [CLI Tools Documentation](docs/cli_tools.md).

5.  **DICOM Network Utilities (`dicom_utils.py`)**:
    *   A general-purpose command-line utility for ad-hoc DICOM network operations: C-ECHO, C-FIND, C-MOVE, and C-STORE.
    *   This tool is useful for testing connectivity, querying remote AEs, initiating transfers, or sending DICOM files.
    *   Common arguments for all sub-commands include `--aet` (Calling AE Title), `--aec` (Called AE Title), `--host`, and `--port`.
    *   Usage Examples:
        *   **C-ECHO SCU**:
            ```bash
            python -m src.cli.dicom_utils echo --aec <SCP_AETITLE> --host <SCP_IP> --port <SCP_PORT>
            ```
        *   **C-FIND SCU**:
            ```bash
            python -m src.cli.dicom_utils find --aec <SCP_AETITLE> --host <SCP_IP> --port <SCP_PORT> \
                                               --patient-id "PAT123" --query-level STUDY --modality CT
            ```
        *   **C-MOVE SCU**:
            ```bash
            python -m src.cli.dicom_utils move --aec <SCP_AETITLE> --host <SCP_IP> --port <SCP_PORT> \
                                               --move-dest-aet <MOVE_DEST_AET> \
                                               --patient-id "PAT123" --query-level SERIES --series-uid "1.2.840..."
            ```
        *   **C-STORE SCU**:
            ```bash
            python -m src.cli.dicom_utils store --aec <SCP_AETITLE> --host <SCP_IP> --port <SCP_PORT> \
                                                --filepath /path/to/dicom_file.dcm
            ```
            (Can also specify a directory for `--filepath` to send all DICOM files within it.)
    *   For detailed options for each command, run `python -m src.cli.dicom_utils <command> --help` (e.g., `python -m src.cli.dicom_utils find --help`).
    *   More detailed documentation is available in [CLI Tools Documentation](docs/cli_tools.md).


## Testing

The unit tests for this project are designed to run in isolation, without requiring dependencies on external DICOM services or live Orthanc instances. This is achieved through:

-   **Mock DICOM Services**: For testing data source interactions (ARIA, MIM, and the DICOM C-STORE part of Mosaiq), an internal mock DICOM server (`MockDicomServer`) is used. This server simulates C-FIND, C-MOVE, and C-STORE responses, allowing verification of the DICOM communication logic within the respective data source classes.
-   **HTTP Mocking**: For testing the Orthanc backup system interface, the `requests-mock` library is used. This allows simulation of Orthanc's REST API responses, ensuring that the `Orthanc.store()` and `Orthanc.verify()` methods correctly handle various scenarios (e.g., success, failure, data mismatch).

All test-specific dependencies, such as `requests-mock`, are listed in the `requirements.txt` file. Unit tests are located in the `src/tests/` directory. For an overview of the test files and specific strategies, refer to `docs/test_files.md`.

Before running the tests, ensure all dependencies are installed or updated by running: `pip install -r requirements.txt`


To run the tests, you can use Python's `unittest` module from the project root:
```bash
python -m unittest discover src/tests
```

## Flask Web Application

The Flask application provides HTTP endpoints for interacting with the backup system.

*   **Endpoints**:
    *   `POST /configure_backup`: Placeholder for configuring backup settings.
    *   `GET /view_logs?type=<log_type>`: View specific log files (e.g., `pynetdicom`, `scu`, `flask_app`).
    *   `POST /run_recovery`: Placeholder for initiating a recovery process.
*   **Running the App**:
    1.  Ensure configurations in `src/config/` are set up.
    2.  Run the application from the project root:
        ```bash
        python src/app.py
        ```
    3.  The application will be available at `http://0.0.0.0:5000` by default.

## Documentation Structure

Detailed documentation for different components of the system can be found in the `docs/` directory:
-   `docs/backup_systems.md`: Information about backup system interfaces (e.g., Orthanc).
-   `docs/data_sources.md`: Details about data source interfaces (ARIA, MIM, Mosaiq).
-   `docs/flask_application.md`: Information specific to the Flask web application.
-   `docs/test_files.md`: Overview of the test files and testing strategy.
-   `docs/cli_tools.md`: Detailed documentation for the command-line interface tools.

## Project Components

### Backup Systems
Interfaces for interacting with backup destinations.
-   **Orthanc**: Implements storage and verification against an Orthanc server using its REST API. (See `src/backup_systems/orthanc.py`)

### Data Sources
Interfaces for querying and retrieving data from various clinical systems.
-   **ARIA**: DICOM C-FIND/C-MOVE for ARIA systems. (See `src/data_sources/aria.py`)
-   **MIM**: DICOM C-FIND/C-GET for MIM systems. (See `src/data_sources/mim.py`)
-   **Mosaiq**: SQL queries (via ODBC) and DICOM C-STORE for Mosaiq. (See `src/data_sources/mosaiq.py`)

### CLI Scripts
Located in `src/cli/`:
-   `backup.py`: Main script for initiating backups.
-   `query.py`: Script for querying DICOM sources.
-   `validate.py`: Script for validating data consistency between source and backup.

### Configuration
Located in `src/config/`:
-   `dicom.toml`: DICOM AE definitions, Mosaiq DB connection details.
-   `environments.toml`: Environment-specific settings (source, backup, ODBC driver).
-   `logging.toml`: Logging configuration for the system.

### Flask Application
Located in `src/app.py`. Provides a web interface for system interaction.

---

## About the `old/` Directory (Legacy Scripts)

The `old/` directory contains scripts from a previous version or earlier development phase of this project. These scripts are retained for historical reference but are no longer actively maintained and are considered superseded by the newer CLI tools available in `src/cli/`.

*   `old/scu_move_git_v1.py` and `old/scu_move_support_git_v1.py`: These scripts likely provided functionality for performing ad-hoc DICOM C-MOVE operations, possibly with specific configurations or for particular use cases at the time. General DICOM C-MOVE capabilities are now available via `src/cli/dicom_utils.py move ...`.
*   `old/get_treatment_report_git_v1.py`: This script was probably used for generating specific treatment reports from data sources like Mosaiq. Treatment report generation, particularly for Mosaiq, is now handled by `src/cli/get_report.py`.
*   `old/scu_find_git_v1.py`: This script likely provided functionality for performing ad-hoc DICOM C-FIND operations. General DICOM C-FIND capabilities are now available via `src/cli/dicom_utils.py find ...`.
*   `old/config_git_v1.toml` and `old/logging_git_v1.toml`: These were configuration files for the legacy scripts. The current system uses configurations in `src/config/`.

Users should prefer the modern CLI tools in `src/cli/` for current operations. If functionality from the `old/` scripts is needed, it's recommended to adapt or verify compatibility with the new tools or develop equivalent functionality using the current framework.
The note about the removal of old script documentation has been updated to this more comprehensive section.
