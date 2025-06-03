"""
Main Flask application for the DICOM Backup and Recovery System.

This application provides HTTP endpoints for configuring backups, viewing logs,
and initiating recovery processes. It loads configurations for DICOM settings,
logging, and environments at startup.
"""
from flask import Flask, request, jsonify
import logging
# logging.config is used by config_loader
import os
# sys and tomllib are used by config_loader
from src.config.config_loader import load_config, ConfigLoaderError # Import the new loader
from flask_httpauth import HTTPTokenAuth # For API Key Auth
from marshmallow import Schema, fields, ValidationError # For input validation
from typing import Optional # For type hinting

# Initialize a basic logger for messages before config is loaded
# This will be quickly superseded by the config from logging.toml once load_config runs.
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
bootstrap_logger = logging.getLogger("bootstrap_logger") # Use a distinct name

# Define configuration file paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
ENVIRONMENTS_CONFIG_PATH = os.path.join(CONFIG_DIR, "environments.toml")
LOGGING_CONFIG_PATH = os.path.join(CONFIG_DIR, "logging.toml")
DICOM_CONFIG_PATH = os.path.join(CONFIG_DIR, "dicom.toml")

# Must import sys here for sys.exit calls below
import sys

try:
    bootstrap_logger.info("Loading application configurations...")
    app_config = load_config(
        config_path_environments=ENVIRONMENTS_CONFIG_PATH,
        config_path_logging=LOGGING_CONFIG_PATH,
        config_path_dicom=DICOM_CONFIG_PATH
    )
    bootstrap_logger.info("Application configurations loaded successfully.")
except ConfigLoaderError as e:
    bootstrap_logger.critical(f"Failed to load application configuration: {e}", exc_info=True)
    print(f"FATAL: Failed to load application configuration: {e}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    bootstrap_logger.critical(f"An unexpected error occurred during configuration loading: {e}", exc_info=True)
    print(f"FATAL: Unexpected error during configuration loading: {e}", file=sys.stderr)
    sys.exit(1)

app = Flask(__name__)

# Initialize main application logger (configured by load_config via logging.toml)
logger = logging.getLogger('flask_app')

# --- API Key Authentication Setup ---
auth = HTTPTokenAuth(scheme='ApiKey')

VALID_API_KEY = os.environ.get("RADONC_API_KEY")
if not VALID_API_KEY:
    logger.critical("RADONC_API_KEY environment variable not set. API will be inaccessible.")
    # For a production app, you might want to sys.exit(1) here if the key is mandatory.
    # For now, auth.login_required will simply fail all requests.

@auth.verify_token
def verify_api_key(token: str) -> Optional[str]:
    """Verifies the provided API key token."""
    if VALID_API_KEY and token == VALID_API_KEY:
        return "api_user" # Return a dummy user/role, or just True
    return None

@auth.error_handler
def auth_error(status: int):
    """Handles authentication errors by returning a JSON response."""
    return jsonify({"error": "Authentication failed", "message": "Invalid or missing API Key."}), status

# --- Input Validation Schemas ---
class ViewLogsSchema(Schema):
    """Schema for validating /view_logs query parameters."""
    type = fields.Str(
        required=True,
        error_messages={"required": "Log 'type' parameter is required."},
        # Basic validation for allowed characters, more robust validation is in the path construction logic
        validate=lambda x: x.replace('_', '').isalnum() and len(x) > 0
    )

view_logs_schema = ViewLogsSchema()

# --- Constants (from previous refactoring, ensure they are defined before routes if used) ---
DEFAULT_LOG_TYPE = 'daily_backup' # Used in /view_logs
LOG_DIRECTORY_NAME = 'logs'     # Used in /view_logs

# --- Flask Routes ---

@app.route('/configure_backup', methods=['POST'])
@auth.login_required
def configure_backup():
    """
    Configures a new backup job or updates an existing one.
    (Protected by API Key)

    Expected Functionality:
    Defines or updates backup job configurations: source systems, targets,
    schedules, retention policies, data filters.

    Request Body (JSON) Example:
    {
        "job_name": "DailyCriticalCTScanBackup", "source_alias": "MainHospitalPACS",
        "backup_target_alias": "OffsiteArchivePACS", "schedule": "daily@02:00",
        "retention_days": 365, "filters": {"modalities": ["CT", "XRAY"]},
        "options": {"compression": "lossless"}
    }

    Response (JSON) Success Example:
    {"status": "success", "job_id": "backup_job_001", "message": "Job configured."}

    Architectural Notes: Idempotency, persistent storage for configs, input validation,
    security (authN/authZ already added), atomicity.

    TODO: Implement input validation using Marshmallow for the request payload.
    Example:
    # class ConfigureBackupSchema(Schema):
    #     job_name = fields.Str(required=True)
    #     source_alias = fields.Str(required=True)
    #     # ... other fields ...
    # configure_backup_schema = ConfigureBackupSchema()
    # try:
    #     validated_data = configure_backup_schema.load(request.json)
    # except ValidationError as err:
    #     logger.warning(f"/configure_backup: Invalid JSON payload: {err.messages}")
    #     return jsonify({"error": "Invalid input", "messages": err.messages}), 400
    # data = validated_data # Use validated_data hereafter
    """
    data = request.json
    logger.info(f"/configure_backup: Received request with data: {data}") # Log after validation

    if data:
        job_id = data.get("job_name", "unknown_job") + "_id_placeholder"
        logger.info(f"/configure_backup: Backup configuration data received for job: {data.get('job_name', 'N/A')}")
        return jsonify({
            "status": "success",
            "job_id": job_id,
            "message": f"Backup job '{data.get('job_name', 'N/A')}' placeholder configured successfully."
        }), 200
    else:
        logger.warning("/configure_backup: Called with no data.")
        return jsonify({"status": "error", "message": "No data provided."}), 400

@app.route('/view_logs', methods=['GET'])
@auth.login_required
def view_logs():
    """
    Retrieves and returns log file contents. (Protected by API Key)

    Allows viewing of specified log files (e.g., 'daily_backup.log').
    Defaults to 'daily_backup' if no type is specified.

    :queryparam type: The type of log to view (e.g., 'daily_backup', 'pynetdicom', 'scu').
    :type type: str, optional
    :resjson logs: The content of the requested log file or error message.
    :status 200: Log content returned.
    :status 400: Invalid input.
    :status 404: Log file not found.
    :status 500: Error reading log file or other server error.
    :rtype: flask.Response
    """
    try:
        args = view_logs_schema.load(request.args)
        log_type = args['type']
    except ValidationError as err:
        logger.warning(f"/view_logs: Invalid input parameters: {err.messages}")
        return jsonify({"error": "Invalid input", "messages": err.messages}), 400

    try:
        allowed_dir_abs = os.path.abspath(LOG_DIRECTORY_NAME)
        log_file_name = f"{log_type}.log"
        log_file_path_constructed = os.path.join(LOG_DIRECTORY_NAME, log_file_name)

        path_segments = os.path.normpath(log_file_path_constructed).split(os.sep)
        if '..' in path_segments:
            logger.warning(f"/view_logs: Directory traversal attempt: '{log_type}', path: '{log_file_path_constructed}'")
            return jsonify({"error": "Invalid log type specified"}), 400

        absolute_log_path = os.path.abspath(log_file_path_constructed)

        if not absolute_log_path.startswith(allowed_dir_abs + os.sep):
            logger.warning(f"/view_logs: Path validation failed. '{log_type}' resolved to '{absolute_log_path}', outside '{allowed_dir_abs}'")
            return jsonify({"error": "Invalid log type specified"}), 400

        log_file_to_open = absolute_log_path
        
    except Exception as e:
        logger.error(f"/view_logs: Error during log path processing for '{log_type}': {e}", exc_info=True)
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
@auth.login_required
def run_recovery():
    """
    Initiates a data recovery process from backups. (Protected by API Key)

    Expected Functionality: Triggers recovery workflow, specifying data, backup source,
    and restoration destination.

    Request Body (JSON) Example:
    {
        "recovery_job_name": "RestorePatient123CriticalCT",
        "backup_source_alias": "OffsiteArchivePACS_BackupData",
        "destination_details": {"type": "DICOM_NODE", "aet_title": "RADIOLOGY_WS1", ...},
        "items_to_recover": [{"patient_id": "PAT12345", "study_uid": "1.2.3..."}]
    }

    Response (JSON) Success Example (for async):
    {"status": "pending", "recovery_job_id": "recovery_job_002", "message": "Job initiated."}

    Architectural Notes: Asynchronous operation, status tracking, error handling,
    security (authN/authZ already added), resource management.

    TODO: Implement input validation using Marshmallow for the request payload.
    Example:
    # class RunRecoverySchema(Schema):
    #     recovery_job_name = fields.Str(required=True)
    #     # ... other fields ...
    # run_recovery_schema = RunRecoverySchema()
    # try:
    #     validated_data = run_recovery_schema.load(request.json)
    # except ValidationError as err:
    #     logger.warning(f"/run_recovery: Invalid JSON payload: {err.messages}")
    #     return jsonify({"error": "Invalid input", "messages": err.messages}), 400
    # data = validated_data # Use validated_data hereafter
    """
    data = request.json
    logger.info(f"/run_recovery: Received request with data: {data}") # Log after validation

    if data:
        recovery_job_id = data.get("recovery_job_name", "unknown_recovery") + "_id_placeholder"
        logger.info(f"/run_recovery: Recovery process initiation data received for job: {data.get('recovery_job_name', 'N/A')}")
        return jsonify({
            "status": "pending",
            "recovery_job_id": recovery_job_id,
            "message": f"Recovery job '{data.get('recovery_job_name', 'N/A')}' placeholder initiated."
        }), 202
    else:
        logger.warning("/run_recovery: Called with no data.")
        return jsonify({"status": "error", "message": "No data provided."}), 400

if __name__ == '__main__':
    HOST = os.environ.get('FLASK_RUN_HOST', '0.0.0.0')
    PORT = int(os.environ.get('FLASK_RUN_PORT', 5000))
    logger.info(f"Starting Flask development server. Host: {HOST}, Port: {PORT}")
    app.run(host=HOST, port=PORT)
