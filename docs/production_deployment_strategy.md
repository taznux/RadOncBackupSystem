# Production Deployment Strategy

## 1. Introduction

This document outlines the strategy for deploying and managing the DICOM Backup System in a production environment. It covers environment setup, configuration management, and a basic rollback plan for the application.

## 2. Environment Setup

### Overview

The production environment consists of several key components: a main Orthanc server for backup, a staging SCP for the Mosaiq workflow, and a host for the backup application itself. These components will be networked securely to allow necessary DICOM and database communications.

### Component Details

#### Main Backup Orthanc Server (`orthanc-main`)

*   **Assumed Hostname:** `orthanc-main.prod.dicom.clinic.com`
*   **Assumed AET:** `ORTHANC_PROD_MAIN`
*   **Recommended:** Dedicated Virtual Machine (VM) or Container (e.g., Docker).
*   **OS:** Linux (e.g., Ubuntu Server LTS).
*   **Orthanc Installation:** Standard Orthanc installation, configured to run as a service (e.g., systemd). Ensure it is configured to support the required DICOM services (C-STORE SCP, C-FIND SCP, C-GET SCP, C-MOVE SCP).
*   **Storage:**
    *   High-availability, fault-tolerant storage (e.g., RAID array, SAN, or cloud-based durable storage).
    *   Storage capacity scaled according to the expected volume of DICOM data, with monitoring and procedures for expansion.
    *   Regularly backed up: Orthanc's internal database and DICOM file storage should have their own robust backup strategy (e.g., database dumps, filesystem snapshots, Orthanc's own backup plugins if applicable). This is separate from the application's backup function.
*   **Networking:**
    *   Accessible by the Backup Application Host on its configured DICOM port (e.g., 104 or 11112, as defined in its configuration).
    *   Accessible by the Staging SCP (`orthanc-staging`) for C-MOVE operations.
    *   Firewall rules should be implemented to restrict access to these specific hosts and services.

#### Staging SCP for Mosaiq (`orthanc-staging`)

*   **Assumed Hostname:** `orthanc-staging.prod.dicom.clinic.com`
*   **Assumed AET:** `ORTHANC_PROD_STAGE_MOSAIQ`
*   **Recommended:** Dedicated Virtual Machine or Container. This could potentially be a less resource-intensive system compared to the main backup server if its role is purely transient for daily Mosaiq records.
*   **OS:** Linux (e.g., Ubuntu Server LTS).
*   **Orthanc/SCP Installation:** Standard Orthanc installation or another lightweight DICOM SCP that is capable of:
    *   Acting as a C-STORE SCP (to receive generated RT Records from the Backup Application).
    *   Acting as a C-MOVE SCP (to respond to C-MOVE requests from the Backup Application, moving data to `orthanc-main`).
*   **Storage:** Sufficient disk space for the temporary storage of daily Mosaiq RT Record instances. A regular automated cleanup mechanism (e.g., a cron job deleting files older than a few days) should be implemented to manage disk usage.
*   **Networking:**
    *   Accessible by the Backup Application Host (for C-STORE from the application and for initiating C-MOVE to `orthanc-main`).
    *   The main Orthanc server (`orthanc-main`) must be able to receive data from this staging SCP via C-MOVE (i.e., staging SCP needs to be able to initiate association to `orthanc-main` if C-MOVE is initiated from staging, or `orthanc-main` needs to be able to pull if C-MOVE is initiated from `orthanc-main`). Given the current application design, the Backup Application Host initiates the C-MOVE from staging to main.

#### Backup Application Host

*   **Recommended:** Dedicated Virtual Machine or Container.
*   **OS:** Linux (e.g., Ubuntu Server LTS).
*   **Runtime:**
    *   Python 3.x environment.
    *   All dependencies as specified in `requirements.txt` installed, preferably within a virtual environment (e.g., venv, conda).
*   **Application Deployment:** Code deployed from a version control system (e.g., Git clone of a stable release tag).
*   **Scheduling:** The backup script (`src/cli/backup.py`) will be scheduled to run at appropriate intervals (e.g., daily, nightly) using a system scheduler like `cron`.
*   **Networking:**
    *   Must have network connectivity to all source DICOM AEs (ARIA, MIM).
    *   Must have network connectivity to the Mosaiq SQL database.
    *   Must have network connectivity to the Main Backup Orthanc Server (`orthanc-main`) for C-FIND and C-GET operations (verification).
    *   Must have network connectivity to the Staging SCP for Mosaiq (`orthanc-staging`) for C-STORE and C-MOVE operations.
    *   Outbound internet access might be required for system updates or if any Python libraries used for non-core tasks require it. Ideally, system and package updates are managed through internal repositories or controlled environments.

### Network Security

*   **Firewalls:** Implement host-based or network firewalls to restrict DICOM and database traffic strictly to the necessary hosts and ports.
    *   Example: `orthanc-main` only accepts DICOM traffic from the Backup Application Host and `orthanc-staging`.
    *   Example: Source systems (ARIA, MIM) only accept DICOM traffic from the Backup Application Host's AET.
*   **VPN/Private Networks:** If components are geographically distributed (e.g., different sites) or reside in different cloud Virtual Private Clouds (VPCs), secure communication channels such as VPNs or dedicated private network links should be established.
*   **Least Privilege:** Ensure network access controls follow the principle of least privilege.

## 3. Configuration Management

### Overview

The primary configuration file for the DICOM Backup System is `src/config/environments.toml`. This file defines different operational environments (e.g., UCLA, TJU) and, within each environment, specifies:
*   The calling AE Title for the backup scripts (`script_ae`).
*   Details for various data `sources` (DICOM AEs like ARIA/MIM, and database connections like Mosaiq).
*   Details for `backup_targets` (e.g., the main Orthanc backup server, staging SCPs).
*   Operational `settings` such as `max_uids_per_run` and default SQL queries for Mosaiq.

OS-level environment variables are primarily used for:
1.  Overriding the default logging level (e.g., via `LOG_LEVEL`).
2.  Providing sensitive data, such as database passwords. `environments.toml` may contain placeholders for these secrets (e.g., `db_password = "__UCLA_MOSAIQ_DB_PASSWORD__"`), and the actual values are supplied via OS environment variables at runtime. The application or a deployment script would be responsible for substituting these placeholders.
3.  Setting `PYTHONPATH` if necessary for the execution environment.

### Key Configuration Parameters

The following are key parameters. Most are defined per environment within `src/config/environments.toml`. OS-level environment variables should be used for `LOG_LEVEL` and to supply actual values for secrets referenced in `environments.toml`.

*   **Logging (OS Environment Variable):**
    *   `LOG_LEVEL`: Controls the application's logging verbosity (e.g., `INFO`, `DEBUG`, `WARNING`, `ERROR`). Recommended for production: `INFO`.
*   **Python Environment (Execution Environment):**
    *   `PYTHONPATH`: Must be correctly set in the execution environment (e.g., systemd service file, Dockerfile) to include the project's `src` directory if running scripts as modules.
*   **Secrets (OS Environment Variables for Placeholder Substitution):**
    *   For parameters like database passwords defined in `environments.toml` (e.g., `db_password = "__UCLA_MOSAIQ_DB_PASSWORD__"` within a Mosaiq source definition under `[UCLA.sources.MOSAIQ_DB]`), corresponding OS environment variables must be set.
    *   Example: `UCLA_MOSAIQ_DB_PASSWORD="actual_secret_value"`.
    *   The application (or a helper script/deployment process) needs to be capable of reading these OS environment variables and substituting them into the configuration loaded from `environments.toml` where placeholders are used. **Note:** The current CLI scripts load `environments.toml` directly and do not have a built-in placeholder substitution mechanism from OS environment variables for arbitrary keys within the TOML structure. This would be an enhancement for production deployments if direct TOML modification is to be avoided for secrets. For now, the TOML file is the direct source, implying secrets might be in it or the file is generated with secrets at deploy time.
*   **`environments.toml` Structure (Primary Configuration Source):**
    *   **Environment Blocks (e.g., `[UCLA]`, `[TJU]`):** Define distinct operational contexts.
        *   `description`: Human-readable description.
        *   `default_source`: Alias to an entry in `[UCLA.sources]`.
        *   `default_backup`: Alias to an entry in `[UCLA.backup_targets]`.
    *   **Script AE (e.g., `[UCLA.script_ae]`):**
        *   `aet`: Calling AE Title for the backup/query/validation scripts.
        *   `port` (optional): Port for the local SCP started by `validate.py`.
    *   **Sources (e.g., `[UCLA.sources.ARIA_MAIN]`, `[UCLA.sources.MOSAIQ_DB]`):**
        *   `type`: "aria", "mim", or "mosaiq".
        *   For "aria"/"mim": `aet`, `ip`, `port`. May include `dicom_query_level`, `dicom_query_keys`.
        *   For "mosaiq": `db_server`, `db_database`, `db_username`, `db_password` (can be placeholder), `odbc_driver`. May include `staging_target_alias` (pointing to an entry in `backup_targets`).
    *   **Backup Targets (e.g., `[UCLA.backup_targets.ORTHANC_MAIN]`, `[UCLA.backup_targets.MOSAIQ_STAGE]`):**
        *   `type`: "orthanc" (for the main backup, implies Orthanc class usage) or "dicom_scp" (for generic staging).
        *   `aet`, `ip`, `port`.
    *   **Settings (e.g., `[UCLA.settings]`):**
        *   `max_uids_per_run`: For ARIA/MIM C-MOVE instance limiting.
        *   `mosaiq_backup_sql_query`: Default SQL query for Mosaiq backups in this environment.
        *   Other operational parameters like `dicom_query_level` or `modality_to_query` for query scripts.

### Example Environment Variable File (`.env.prod.template`)

(Content updated as per separate instructions)

## 4. Rollback Plan

A rollback plan is essential to revert to a last known good state in case of deployment issues or critical bugs.

### Code Rollback

*   **Method:** Re-deploy the previous stable version of the application.
*   **Source:** Utilize the version control system (Git). Identify the commit hash or tag of the last stable version.
*   **Procedure:** If a CI/CD pipeline is used for deployment, trigger a deployment of the specific stable tag/commit. If deployment is manual, check out the stable tag/commit and redeploy the application files.

### Configuration Rollback

*   **`environments.toml`:** If changes to `environments.toml` caused issues, revert this file to its previous stable version from version control (Git) and redeploy.
*   **Environment Variables (for secrets/overrides):** If OS-level environment variables were changed (e.g., for `LOG_LEVEL` or actual secret values), revert these variables to their previous known good settings. This might involve updating startup scripts or the configuration of the environment variable injection system.
*   **Configuration Management Tools:** If a dedicated configuration management tool (e.g., Ansible, Chef, Puppet) or a platform like Kubernetes (using ConfigMaps/Secrets) is used for managing either the TOML file deployment or environment variables, use that tool to redeploy the previous known-good configuration version.

### Orthanc Data (Catastrophic Failure - Out of Scope for Application Rollback)

*   The rollback of the Orthanc servers (`orthanc-main`, `orthanc-staging`) and their stored DICOM data in case of data corruption or server failure is considered an infrastructure-level concern, separate from the rollback of this backup application.
*   This would involve restoring the Orthanc servers from their own backups (database and DICOM file storage) according to established disaster recovery procedures for those systems.
*   The DICOM Backup System application's rollback focuses on its own codebase and direct operational configuration.

### Testing

*   The rollback procedure (for code and application configuration) should be documented and tested periodically in a staging or pre-production environment that mirrors the production setup as closely as possible. This ensures the procedure is viable and can be executed efficiently if needed.
