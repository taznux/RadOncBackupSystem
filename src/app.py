"""
Main Flask application for the DICOM Backup and Recovery System.

This application provides HTTP endpoints for configuring backups, viewing logs,
and initiating recovery processes. It loads configurations for DICOM settings,
logging, and environments at startup.
"""
from flask import Flask, request, jsonify
import logging
import logging.config
import os
import sys
import tomllib

# Initialize logger temporarily for startup messages
# This will be replaced by the configured logger later
logging.basicConfig(level=logging.INFO)
temp_logger = logging.getLogger('startup_logger')

def load_app_config():
    """
    Loads application configurations from TOML files.

    This function loads configurations for DICOM, logging, and environments.
    It handles FileNotFoundError and tomllib.TOMLDecodeError, logging errors
    and exiting if critical configurations are missing or malformed.

    :return: A dictionary containing all loaded configurations.
    :rtype: dict
    :raises SystemExit: If a configuration file is not found or is malformed.
    """
    config_paths = {
        "dicom": 'src/config/dicom.toml',
        "logging": 'src/config/logging.toml',
        "environments": 'src/config/environments.toml'
    }
    loaded_configs = {}

    for name, path in config_paths.items():
        try:
            with open(path, 'rb') as f:
                loaded_configs[name] = tomllib.load(f)
            temp_logger.info(f"Successfully loaded {name} configuration from {path}")
        except FileNotFoundError:
            temp_logger.error(f"Configuration file {path} not found.")
            sys.exit(f"Error: Configuration file {path} not found. Application cannot start.")
        except tomllib.TOMLDecodeError as e:
            temp_logger.error(f"Error decoding TOML file {path}: {e}")
            sys.exit(f"Error: Malformed configuration file {path}. Application cannot start.")

    return loaded_configs

app_config = load_app_config()

# Configure logging using the loaded configuration
# Ensure that dictConfig is called before any logger instances are created
# that rely on this configuration.
if 'logging' in app_config:
    logging.config.dictConfig(dict(app_config['logging']))
else:
    # Fallback basicConfig if logging config failed to load but app didn't exit
    # This case should ideally be prevented by sys.exit in load_app_config
    temp_logger.warning("Logging configuration not found, falling back to basicConfig.")
    logging.basicConfig(level=logging.INFO)


app = Flask(__name__)


# Initialize logger
# The logger 'flask_app' is configured via logging.config.dictConfig.
# Ensure 'flask_app' is defined in the 'loggers' section of logging.toml
# (which was done as part of the refactoring).
logger = logging.getLogger('flask_app')


# --- Constants ---
DEFAULT_LOG_TYPE = 'daily_backup'
LOG_DIRECTORY_NAME = 'logs'


# --- Flask Routes ---

@app.route('/configure_backup', methods=['POST'])
def configure_backup():
    """
    Configures a new backup job or updates an existing one.

    Expected Functionality:
    This endpoint defines or updates backup job configurations. It specifies
    source DICOM systems, backup targets (other DICOM systems or storage),
    backup schedules, data retention policies, and filters for data to be backed up
    (e.g., specific imaging modalities, patient groups, date ranges).

    Request Body (JSON):
    Example:
    {
        "job_name": "DailyCriticalCTScanBackup",
        "source_alias": "MainHospitalPACS", // Defined in environments.toml
        "backup_target_alias": "OffsiteArchivePACS", // Defined in environments.toml
        "schedule": "daily@02:00", // Could be cron expression or predefined like "hourly"
        "retention_days": 365,
        "filters": { // Optional: what specific data to back up
            "modalities": ["CT", "XRAY"],
            "date_range": {"start_date": "2023-01-01", "end_date": "2023-12-31"},
            "patient_id_prefix": "PAT_", // Example custom filter
            // Other filters like 'min_study_size_mb', 'exclude_aet_titles'
        },
        "options": { // Optional: advanced settings
            "compression": "lossless", // "lossy", "none"
            "retry_attempts": 3,
            "notification_email": "admin@example.com"
        }
    }

    Response Body (JSON):
    Success Example:
    {
        "status": "success",
        "job_id": "backup_job_001", // Generated or user-defined if unique
        "message": "Backup job 'DailyCriticalCTScanBackup' configured successfully."
    }
    Error Example:
    {
        "status": "error",
        "message": "Invalid source_alias: MainHospitalPACS not found in environments.",
        "details": { "field": "source_alias", "error": "Not found" }
    }

    Architectural Considerations:
    - Idempotency: The endpoint should ideally be idempotent. Creating a job with
      the same name/parameters multiple times should result in the same state.
    - Configuration Storage: Backup job configurations need to be stored persistently,
      e.g., in a dedicated database (SQL or NoSQL), a new set of configuration files,
      or potentially by extending `environments.toml` (though direct TOML updates via
      web API can be risky and complex, consider a management layer).
    - Validation: Rigorous validation of all input parameters is crucial. This includes
      checking for the existence of `source_alias` and `backup_target_alias` in
      `environments_config` (loaded from `environments.toml`). Schedule formats,
      retention policies, and filter validity must also be checked.
    - Security: This endpoint must be protected by strong authentication and
      authorization mechanisms to ensure only permitted users can define or modify
      backup configurations. Audit logging of configuration changes is also recommended.
    - Atomicity: If a configuration involves multiple steps (e.g., writing to DB,
      updating a scheduler), these should be atomic or handle partial failures gracefully.

    :reqjson data: JSON object containing backup configuration settings.
    :resjson message: A confirmation or error message.
    :status 200: Backup configuration updated/created successfully.
    :status 400: Invalid input data or configuration error.
    :status 401/403: Authentication/Authorization error.
    :return: JSON response with status, message, and potentially job_id.
    :rtype: flask.Response
    """
    data = request.json
    logger.info(f"/configure_backup: Received request with data: {data}")

    # Placeholder logic:
    # In a real application, this section would involve:
    # 1. Authentication & Authorization: Verify user permissions.
    #    - Example: if not current_user.can('configure_backup'): return jsonify({...}), 403
    # 2. Comprehensive Validation:
    #    - Validate `data` against a schema (e.g., using Marshmallow, Pydantic).
    #    - Check if `source_alias` and `backup_target_alias` exist in `app_config['environments']`.
    #    - Validate `schedule` format, `retention_days`, `filters`, etc.
    #    - Example:
    #      required_fields = ["job_name", "source_alias", "backup_target_alias", "schedule"]
    #      if not all(field in data for field in required_fields):
    #          logger.warning(f"/configure_backup: Missing required fields in request: {data}")
    #          return jsonify({"status": "error", "message": "Missing required fields."}), 400
    #      source_env = app_config.get('environments', {}).get(data['source_alias'])
    #      if not source_env:
    #          logger.warning(f"/configure_backup: Invalid source_alias: {data['source_alias']}")
    #          return jsonify({"status": "error", "message": f"Invalid source_alias: {data['source_alias']} not found."}), 400
    # 3. Configuration Storage:
    #    - Generate a unique `job_id` if not provided or if updates are based on name.
    #    - Store the configuration (e.g., in a database, new config file).
    #    - This might involve creating or updating a record for the backup job.
    # 4. Scheduler Interaction:
    #    - If the schedule is valid, configure a scheduler (e.g., APScheduler, Celery Beat)
    #      to trigger the backup job according to the defined schedule.
    # 5. Respond with success or error:
    #    - Return a meaningful success message, including the `job_id`.
    #    - Provide detailed error messages if validation or storage fails.

    if data:
        # This is highly simplified. A real implementation needs robust processing.
        job_id = data.get("job_name", "unknown_job") + "_id_placeholder" # Example job ID
        logger.info(f"/configure_backup: Backup configuration data received for job: {data.get('job_name', 'N/A')}")
        # Simulate successful configuration:
        return jsonify({
            "status": "success",
            "job_id": job_id,
            "message": f"Backup job '{data.get('job_name', 'N/A')}' placeholder configured successfully."
        }), 200
    else:
        logger.warning("/configure_backup: Called with no data.")
        return jsonify({"status": "error", "message": "No data provided."}), 400


@app.route('/view_logs', methods=['GET'])
def view_logs():
    """
    Retrieves and returns log file contents.

    Allows viewing of specified log files (e.g., 'daily_backup.log').
    Defaults to 'daily_backup' if no type is specified.

    :queryparam type: The type of log to view (e.g., 'daily_backup', 'pynetdicom', 'scu').
                      This corresponds to the log filename prefix.
    :type type: str, optional
    :resjson logs: The content of the requested log file.
    :resjson error: An error message if the log file is not found.
    :status 200: Log file content returned successfully.
    :status 404: Log file not found.
    :return: JSON response with log content or error message, and HTTP status code.
    :rtype: flask.Response
    """
    log_type = request.args.get('type', DEFAULT_LOG_TYPE)

    # Sanitize log_type: allow only alphanumeric characters and underscores.
    if not log_type.replace('_', '').isalnum():
        logger.warning(f"/view_logs: Invalid characters in log_type: '{log_type}'")
        return jsonify({"error": "Invalid log type format"}), 400

    # Construct the log file path and perform security checks.
    # Goal: Ensure the path is within the LOG_DIRECTORY_NAME and doesn't use '..'
    try:
        # Define the allowed directory for logs
        allowed_dir_abs = os.path.abspath(LOG_DIRECTORY_NAME)

        # Construct the path to the log file
        log_file_name = f"{log_type}.log"
        log_file_path_constructed = os.path.join(LOG_DIRECTORY_NAME, log_file_name)

        # Critical check: ensure the normalized constructed path doesn't contain '..' components
        # This prevents bypassing the abspath check with sequences like 'logs/../../etc/passwd'
        # Normalizing here also helps simplify the check by resolving things like './' or '//'.
        path_segments = os.path.normpath(log_file_path_constructed).split(os.sep)
        if '..' in path_segments:
            logger.warning(f"/view_logs: Directory traversal attempt detected in log_type: '{log_type}', constructed path: '{log_file_path_constructed}'")
            return jsonify({"error": "Invalid log type specified"}), 400

        # Normalize and get the absolute path of the requested log file
        # This path is what we'll use for actual file operations after validation.
        absolute_log_path = os.path.abspath(log_file_path_constructed)

        # Security check: Ensure the absolute path of the log file is within the allowed directory.
        # Check if `absolute_log_path` starts with `allowed_dir_abs` and a path separator.
        # Also ensure it's not the directory itself but a file within.
        if not absolute_log_path.startswith(allowed_dir_abs + os.sep):
            logger.warning(f"/view_logs: Path validation failed. Log type '{log_type}' resolved to '{absolute_log_path}', which is outside allowed directory '{allowed_dir_abs}'")
            return jsonify({"error": "Invalid log type specified"}), 400

        log_file_to_open = absolute_log_path # Use the validated absolute path
        
    except Exception as e: # Catch any unexpected errors during path manipulation
        logger.error(f"/view_logs: Error during log path processing for log_type '{log_type}': {e}", exc_info=True)
        return jsonify({"error": "Error processing request"}), 500

    logger.info(f"/view_logs: Attempting to serve log_type: '{log_type}', validated file path: '{log_file_to_open}'")

    if os.path.exists(log_file_to_open) and os.path.isfile(log_file_to_open):
        try:
            with open(log_file_to_open, 'r') as f:
                logs = f.read()
            logger.debug(f"/view_logs: Successfully read log file: '{log_file_to_open}'")
            return jsonify({"logs": logs}), 200
        except Exception as e:
            logger.error(f"/view_logs: Error reading log file '{log_file_to_open}': {e}", exc_info=True)
            return jsonify({"error": "Could not read log file"}), 500
    else:
        logger.warning(f"/view_logs: Log file not found or is not a file: '{log_file_to_open}'")
        return jsonify({"error": "Log file not found"}), 404


@app.route('/run_recovery', methods=['POST'])
def run_recovery():
    """
    Initiates a data recovery process from backups.

    Expected Functionality:
    This endpoint triggers a recovery workflow. It allows users to specify
    what data to recover (e.g., specific studies, series, or entire patient datasets)
    from a chosen backup source and where to restore it (e.g., a clinical
    workstation's DICOM listener, a research PACS, or a specific directory).

    Request Body (JSON):
    Example:
    {
        "recovery_job_name": "RestorePatient123CriticalCT",
        // Identifies which backup set/archive to use. Could be an alias from environments.toml
        // or a specific backup job ID that previously ran.
        "backup_source_alias": "OffsiteArchivePACS_BackupData",
        "destination_details": { // Where to send the recovered data
            "type": "DICOM_NODE", // "FILE_SYSTEM"
            "aet_title": "RADIOLOGY_WS1", // Required if type is DICOM_NODE
            "ip_address": "192.168.1.100", // Required if type is DICOM_NODE
            "port": 11112, // Required if type is DICOM_NODE
            // "path": "/mnt/recovery_staging_area/" // Required if type is FILE_SYSTEM
        },
        "items_to_recover": [ // Specifies what data items to recover
            {
                "patient_id": "PAT12345",
                // Optional: further narrow down by StudyInstanceUID, SeriesInstanceUIDs
                "study_uid": "1.2.840.113619.2.55.3.2831184615.907.1373500323.926",
                "series_uids": [
                    "1.2.840.113619.2.55.3.2831184615.907.1373500323.928",
                    "1.2.840.113619.2.55.3.2831184615.907.1373500323.930"
                ]
            },
            // Can also support recovery by accession number, date range for a patient, etc.
            // { "accession_number": "ACC000789" }
        ],
        "options": { // Optional: advanced settings
            "priority": "high", // "medium", "low"
            "on_completion_notify_email": "requesting_user@example.com"
        }
    }

    Response Body (JSON):
    Success Example (for asynchronous operation):
    {
        "status": "pending", // Or "queued", "initiated"
        "recovery_job_id": "recovery_job_002",
        "message": "Recovery job 'RestorePatient123CriticalCT' initiated successfully. Check status for updates."
    }
    Error Example:
    {
        "status": "error",
        "message": "Invalid backup_source_alias or items_to_recover specification.",
        "details": { "field": "items_to_recover.0.patient_id", "error": "Patient ID not found in backup index." }
    }

    Architectural Considerations:
    - Asynchronous Operation: Data recovery can be lengthy. This endpoint should
      initiate an asynchronous background task (e.g., using Celery, RQ, or a
      custom task queue with thread/process pools) and return quickly.
    - Status Tracking: A separate endpoint (e.g., /recovery_status/{job_id}) will be
      necessary for clients to poll the progress and result of the recovery job.
    - Error Handling & Rollback: Robust error handling for issues like backup
      unavailability, network problems during transfer, or destination errors.
      Consider partial success/failure and potential rollback or cleanup actions.
    - Security: Requires strong authentication and authorization. Recovery actions
      must be meticulously logged for auditing (HIPAA, GDPR compliance).
      Ensure that data is only restored to authorized destinations.
    - Resource Management: Recovery operations can be resource-intensive. Implement
      throttling or queuing to prevent overwhelming the backup system, network,
      or destination systems.
    - Data Validation & Indexing: Assumes a searchable index of backed-up data exists
      to validate `items_to_recover` and locate the actual backup files.

    :reqjson data: JSON object containing recovery parameters.
    :resjson message: A confirmation or error message.
    :status 202: Recovery process initiated successfully (for asynchronous tasks).
    :status 400: Invalid input data or parameters.
    :status 401/403: Authentication/Authorization error.
    :status 404: Backup source or items to recover not found.
    :return: JSON response with status, message, and recovery_job_id.
    :rtype: flask.Response
    """
    data = request.json
    logger.info(f"/run_recovery: Received request with data: {data}")

    # Placeholder logic:
    # In a real application, this section would involve:
    # 1. Authentication & Authorization: Verify user permissions for recovery.
    # 2. Comprehensive Validation:
    #    - Validate `data` against a schema.
    #    - Check `backup_source_alias` validity (e.g., exists in `app_config['environments']` or a backup catalog).
    #    - Validate `destination_details` (e.g., AET format, IP/port reachability if possible).
    #    - Validate `items_to_recover` structure and query parameters against backup index/catalog.
    #    - Example:
    #      if not data.get("items_to_recover"):
    #          logger.warning(f"/run_recovery: No items_to_recover specified in request: {data}")
    #          return jsonify({"status": "error", "message": "No items_to_recover specified."}), 400
    # 3. Asynchronous Task Initiation:
    #    - Generate a unique `recovery_job_id`.
    #    - Package the recovery parameters and submit them to a background task queue
    #      (e.g., Celery: `trigger_recovery_task.delay(recovery_params)`).
    #    - The task itself would handle querying the backup, retrieving data, and sending
    #      it to the destination.
    # 4. Respond with Job ID and Pending Status:
    #    - Return a 202 Accepted status indicating the request is being processed.
    #    - Include the `recovery_job_id` for status tracking.

    if data:
        # This is highly simplified. A real implementation would trigger an async task.
        recovery_job_id = data.get("recovery_job_name", "unknown_recovery") + "_id_placeholder" # Example job ID
        logger.info(f"/run_recovery: Recovery process initiation data received for job: {data.get('recovery_job_name', 'N/A')}")
        # Simulate successful initiation of an asynchronous job:
        return jsonify({
            "status": "pending",
            "recovery_job_id": recovery_job_id,
            "message": f"Recovery job '{data.get('recovery_job_name', 'N/A')}' placeholder initiated."
        }), 202 # 202 Accepted is suitable for async operations
    else:
        logger.warning("/run_recovery: Called with no data.")
        return jsonify({"status": "error", "message": "No data provided."}), 400


if __name__ == '__main__':
    # Retrieve host and port from environment variables, with defaults.
    # FLASK_RUN_HOST and FLASK_RUN_PORT are standard Flask environment variables.
    HOST = os.environ.get('FLASK_RUN_HOST', '0.0.0.0')
    PORT = int(os.environ.get('FLASK_RUN_PORT', 5000))

    # Note: For production deployments, it's highly recommended to use a
    # production-grade WSGI server (e.g., Gunicorn, uWSGI) instead of
    # Flask's built-in development server.
    logger.info(f"Starting Flask development server. Host: {HOST}, Port: {PORT}")
    app.run(host=HOST, port=PORT)
