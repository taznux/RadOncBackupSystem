from flask import Flask, request, jsonify
import logging
import os
import tomllib

app = Flask(__name__)

# Load configurations
with open('config/dicom.toml', 'rb') as f:
    dicom_config = tomllib.load(f)

with open('config/logging.toml', 'rb') as f:
    logging_config = tomllib.load(f)
    logging.config.dictConfig(dict(logging_config))

with open('config/environments.toml', 'rb') as f:
    environments_config = tomllib.load(f)

# Initialize logger
logger = logging.getLogger('flask_app')

@app.route('/configure_backup', methods=['POST'])
def configure_backup():
    data = request.json
    # Process the configuration data
    # For example, save it to a file or update in-memory configuration
    return jsonify({"message": "Backup configuration updated successfully"}), 200

@app.route('/view_logs', methods=['GET'])
def view_logs():
    log_type = request.args.get('type', 'daily_backup')
    log_file = f'logs/{log_type}.log'
    if os.path.exists(log_file):
        with open(log_file, 'r') as f:
            logs = f.read()
        return jsonify({"logs": logs}), 200
    else:
        return jsonify({"error": "Log file not found"}), 404

@app.route('/run_recovery', methods=['POST'])
def run_recovery():
    data = request.json
    # Process the recovery data
    # For example, initiate recovery process based on the provided data
    return jsonify({"message": "Recovery process initiated successfully"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
