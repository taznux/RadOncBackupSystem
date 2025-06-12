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

Configuration is managed centrally by `src/config/config_loader.py`, which loads settings from TOML files and resolves secrets using environment variables.

1.  **Environments (`src/config/environments.toml`)**:
    *   This is the **primary configuration file** for defining operational environments (e.g., `[UCLA]`, `[TJU]`).
    *   It specifies DICOM AE details, database connection parameters (excluding passwords directly), and other environment-specific settings.
    *   **Important**: Sensitive values like database passwords are not stored directly here. Instead, a key ending with `_env_var` is used to specify the name of an environment variable that holds the actual secret. See the "Secret Management" section below.
    *   Structure:
        *   `[EnvironmentName]`: Top-level table for each environment.
            *   `script_ae`: Defines the calling AE Title for scripts.
            *   `sources`: Table for data sources (ARIA, MIM, Mosaiq).
                *   Includes `type`, connection details (AET, IP, port for DICOM; server, database, username for DBs).
                *   For database passwords, use `db_password_env_var = "YOUR_ENV_VARIABLE_NAME"`.
            *   `backup_targets`: Table for backup destinations.
            *   `settings`: Environment-specific application settings.
        *   "This table is used for environment-specific operational parameters. For example:"
        *   ```toml
          # Example for [EnvironmentName.settings]
          # [UCLA.settings]
          # max_uids_per_run = 100 # Optional: limits instances processed by backup.py for ARIA/MIM per run
          # mosaiq_backup_sql_query = "SELECT PatientID, StudyDate FROM RadOncOutputTable WHERE ExportDate > '2023-01-01'" # Example SQL for Mosaiq backup
          ```
            *   `default_source`, `default_backup`: Aliases for default source/backup targets.
    *   Example with secret management:
        ```toml
        # Example for src/config/environments.toml:
        [UCLA.sources.MOSAIQ_DB]
        type = "mosaiq"
        db_server = "ucla_db_server_address"
        db_database = "ucla_mosaiq_db_name"
        db_username = "ucla_db_user"
        db_password_env_var = "UCLA_MOSAIQ_DB_PASSWORD" # Env var for password
        odbc_driver = "ODBC Driver 17 for SQL Server"
        staging_target_alias = "UCLA_MOSAIQ_STAGE"
        ```

2.  **DICOM Configuration (`src/config/dicom.toml`)**:
    *   This file is intended for truly global, non-environment-specific DICOM settings, which are expected to be rare.
    *   Most, if not all, DICOM AE and related configurations should be defined within `environments.toml` under their respective environment, source, or backup target. This `dicom.toml` file may be empty or very minimal in typical setups.

3.  **Logging (`src/config/logging.toml`)**:
    *   Standard TOML configuration for Python's `logging` module. Defines formatters, handlers, and loggers.
    *   This is loaded and applied by `config_loader.py` at application startup.

## Important: Secret Management

Sensitive configuration values such as database passwords, API keys, and other secrets **must not** be stored directly in configuration files like `environments.toml` that are committed to version control.

This system uses the following approach for managing secrets:

1.  **Environment Variable Placeholders in Configuration**:
    *   In `src/config/environments.toml`, sensitive fields are configured to point to environment variables. This is done by using a key with an `_env_var` suffix.
    *   Example: For a database password, instead of `db_password = "actual_password"`, you would use `db_password_env_var = "NAME_OF_THE_ENV_VARIABLE"`.

2.  **Loading Secrets from Environment**:
    *   The `src/config/config_loader.py` module is responsible for loading all configurations.
    *   It uses the `python-dotenv` library to automatically load variables defined in a `.env` file located in the project root into the environment. This is primarily for local development.
    *   When resolving a key like `db_password_env_var`, `config_loader.py` will look for an environment variable named `NAME_OF_THE_ENV_VARIABLE` (as specified by the value of `db_password_env_var`).
    *   The value of this environment variable will then be used for the actual `db_password` field in the loaded configuration.

3.  **`.env` File for Local Development**:
    *   Create a file named `.env` in the root of the project.
    *   Define your actual secrets in this file, one per line, in the format `VARIABLE_NAME="value"`.
    *   Example `.env` content:
        ```env
        UCLA_MOSAIQ_DB_PASSWORD="actual_ucla_mosaiq_password"
        RADONC_API_KEY="your_flask_app_api_key"
        ```
    *   A template file, `.env.example`, is provided in the project root. Copy this to `.env` and fill in your actual secrets.

4.  **`.gitignore`**:
    *   The `.env` file **must be listed in `.gitignore`** to ensure it is never committed to the version control system. This has been pre-configured.

5.  **Production Environments**:
    *   In production or deployed environments (e.g., Docker containers, CI/CD systems, cloud platforms), these environment variables should be set directly in the execution environment or through the platform's secret management tools. The `.env` file is typically not used in production.

This system ensures that secrets are decoupled from the codebase and configuration files, enhancing security.

## Running the CLI Applications

All CLI scripts are located in `src/cli/` and should be run as Python modules from the project's root directory. They now use `src/config/config_loader.py` to load all necessary configurations, including resolving secrets from environment variables (or `.env` file). The primary argument for most scripts is the `<environment_name>`.

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
    *   **Mosaiq Backup Workflow:** For Mosaiq sources, `backup.py` executes a two-step process:
        1.  Data is queried from the Mosaiq database and converted into DICOM format.
        2.  These DICOM instances are C-STOREd to a *staging SCP*. The AE details for this staging SCP are specified by the `staging_target_alias` key within the Mosaiq source's configuration block in `environments.toml`. This alias must correspond to an entry under `[EnvironmentName.backup_targets]`.
        3.  A C-MOVE operation is then initiated to transfer the data from the staging SCP to the final *backup target SCP* (typically defined by `default_backup` in the environment's configuration).
    *   **Default Source/Target:** If the `[source_alias]` argument is omitted, the `default_source` defined for the `<environment_name>` in `environments.toml` will be used. The backup destination is determined by the `default_backup` alias specified in the same environment's configuration.

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
    *   `GET /view_logs?type=<log_type>`: View specific log files (e.g., `pynetdicom`, `scu`, `flask_app`) (e.g., `GET /view_logs?type=flask_app` to view the Flask application log, or `GET /view_logs?type=pynetdicom` for pynetdicom logs).
    *   `POST /run_recovery`: Placeholder for initiating a recovery process.
*   All Flask application endpoints are protected by an API key. Ensure the `RADONC_API_KEY` environment variable is set before running the application. Refer to the 'Important: Secret Management' section for guidance on setting environment variables.
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
-   **Orthanc**: The `src/backup_systems/orthanc.py` module provides an interface (the `Orthanc` class) to interact with a DICOM peer (typically your Orthanc server instance) primarily for *verifying* backups. It uses C-FIND to confirm instance existence and C-GET to retrieve instances for byte-level comparison against original data. The actual transfer of data to the Orthanc server (acting as an SCP) is usually handled by C-MOVE commands initiated from data sources (for ARIA/MIM) or from a staging SCP (for Mosaiq), as orchestrated by `src/cli/backup.py`.

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
