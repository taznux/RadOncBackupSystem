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

The system primarily uses `src/config/environments.toml` for defining operational environments. This file is central to configuring DICOM AE details, database connections, and other environment-specific parameters.

1.  **Environments (`src/config/environments.toml`)**:
    *   This is the **primary configuration file**. It defines different operational environments (e.g., clinical sites like UCLA, TJU).
    *   Each environment is a top-level TOML table (e.g., `[UCLA]`, `[TJU]`).
    *   Within each environment, configurations are organized into nested tables:
        *   `script_ae`: Defines the calling AE Title (AET) for the backup scripts when operating in this environment.
        *   `sources`: A table containing definitions for various data sources (e.g., ARIA, MIM, Mosaiq). Each source entry includes:
            *   `type`: Specifies the source type (e.g., "aria", "mim", "mosaiq").
            *   DICOM AE details (`aet`, `ip`, `port`) if applicable.
            *   Database connection details (`db_server`, `db_database`, `db_username`, `db_password`, `odbc_driver`) for database sources like Mosaiq.
            *   For Mosaiq, a `staging_target_alias` can be specified, pointing to an entry in `backup_targets` that will serve as the C-STORE destination for generated RT Records.
        *   `backup_targets`: A table defining backup destinations. Each entry includes:
            *   `type`: Specifies the target type (e.g., "orthanc" for the main backup, "dicom_scp" for a generic staging SCP).
            *   DICOM AE details (`aet`, `ip`, `port`).
        *   `settings`: Contains environment-specific application settings, such as `max_uids_per_run` or `mosaiq_backup_sql_query`.
    *   Each environment section (e.g., `[UCLA]`) should also define `default_source` and `default_backup` keys. These keys hold aliases that point to specific entries within the `sources` and `backup_targets` tables for that environment, respectively. This simplifies CLI calls by allowing the user to omit source/backup aliases if the defaults are appropriate.
    *   Example structure:
        ```toml
        # Example for src/config/environments.toml:
        [UCLA]
        description = "UCLA Testbed Environment"
        default_source = "ARIA_MAIN"
        default_backup = "ORTHANC_UCLA_BACKUP"

        [UCLA.script_ae]
        aet = "UCLA_SCRIPT_SCU"

        [UCLA.sources]
        ARIA_MAIN = { type = "aria", aet = "ARIA_UCLA_AE", ip = "192.168.1.100", port = 104 }
        MOSAIQ_DB = { type = "mosaiq", db_server = "ucla_db", db_database = "ucla_mosaiq", db_username = "user_ucla", db_password = "__UCLA_DB_PASSWORD__", odbc_driver = "DefaultDriver", staging_target_alias = "UCLA_MOSAIQ_STAGE" }

        [UCLA.backup_targets]
        ORTHANC_UCLA_BACKUP = { type = "orthanc", aet = "UCLA_ORTHANC_AE", ip = "192.168.1.200", port = 4242 }
        UCLA_MOSAIQ_STAGE = { type = "dicom_scp", aet = "UCLA_STAGE_AE", ip = "192.168.1.201", port = 11113 }

        [UCLA.settings]
        max_uids_per_run = 100
        # mosaiq_backup_sql_query = "SELECT ..."
        ```

2.  **DICOM Configuration (`src/config/dicom.toml`)**:
    *   This file is now **largely deprecated**. All environment-specific DICOM AE definitions and database configurations have been moved into `environments.toml`.
    *   It might be kept for truly global, non-environment-specific settings if any arise, but currently, all operational AE and DB details are expected to be in `environments.toml`.

3.  **Logging (`src/config/logging.toml`)**:
    *   Configure formatters, handlers (console, file, email), and loggers for different modules.
    *   Review and update handler settings, especially for file paths and the `SMTPHandler` (e.g., `mailhost`, `fromaddr`, `toaddrs`, `subject`) if email notifications are desired.

## Running the CLI Applications

All CLI scripts are located in `src/cli/` and should be run as Python modules from the project's root directory. The primary argument for most scripts is now the `<environment_name>`, which corresponds to a top-level section in `src/config/environments.toml`.

1.  **Backup Data (`backup.py`)**:
    *   Backs up DICOM data for a specified environment and source.
    *   Usage:
        ```bash
        python -m src.cli.backup <environment_name> [source_alias]
        ```
    *   Example:
        ```bash
        python -m src.cli.backup UCLA ARIA_MAIN
        # To use the default_source for UCLA:
        python -m src.cli.backup UCLA
        ```

2.  **Query Data Sources (`query.py`)**:
    *   Queries information from data sources using DICOM C-FIND.
    *   Note: For Mosaiq, this script currently only sets up the data source; actual SQL query execution based on arguments is not implemented. It's primarily for DICOM Q/R sources.
    *   Usage:
        ```bash
        python -m src.cli.query <environment_name> [source_alias] [--mrn <mrn>] [--treatment_date <date>] [--study_date <date>]
        ```
    *   Example:
        ```bash
        python -m src.cli.query UCLA ARIA_MAIN --mrn "PAT123"
        ```

3.  **Validate Data (`validate.py`)**:
    *   Validates DICOM data consistency between a source and the Orthanc backup.
    *   Retrieves data from the source via C-MOVE and verifies against Orthanc.
    *   Usage:
        ```bash
        python -m src.cli.validate <environment_name> [source_alias] [backup_alias] [--log_level <DEBUG|INFO|WARNING|ERROR>]
        ```
    *   Example:
        ```bash
        python -m src.cli.validate UCLA ARIA_MAIN ORTHANC_UCLA_BACKUP --log_level DEBUG
        ```

4.  **Get Treatment Summary Report (`get_report.py`)**:
    *   Retrieves or generates a treatment summary report, currently focused on Mosaiq data sources.
    *   This script uses the `get_treatment_summary_report` method of the `Mosaiq` data source class.
    *   Usage:
        ```bash
        python -m src.cli.get_report <environment_name> [mosaiq_source_alias] --mrn <patient_mrn> [--start_date <YYYY-MM-DD>] [--end_date <YYYY-MM-DD>]
        ```
    *   Example:
        ```bash
        python -m src.cli.get_report UCLA MOSAIQ_DB --mrn "PAT007" --start_date "2023-01-01"
        ```
    *   For full details on all options, run `python -m src.cli.get_report --help`.
    *   More detailed documentation is available in [CLI Tools Documentation](docs/cli_tools.md).

5.  **DICOM Network Utilities (`dicom_utils.py`)**:
    *   A general-purpose command-line utility for ad-hoc DICOM network operations: C-ECHO, C-FIND, C-MOVE, C-GET, and C-STORE.
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
-   **Mocking for Orthanc Interface**: For testing the Orthanc backup system interface (`src/backup_systems/orthanc.py`), `unittest.mock` is used to simulate DICOM C-FIND and C-GET network operations, removing the previous dependency on `requests-mock`.

All test-specific dependencies are listed in the `requirements.txt` file. Unit tests are located in the `src/tests/` directory. For an overview of the test files and specific strategies, refer to `docs/test_files.md`.

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
-   `environments.toml`: Primary configuration file defining operational environments (sites), including their specific DICOM AEs, database connections, backup targets, script AETs, and other settings.
-   `dicom.toml`: Largely deprecated. Most AE and DB configurations are now in `environments.toml`.
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
