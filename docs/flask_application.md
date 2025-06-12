# Flask Application

The Flask application provides endpoints for configuring backups, viewing logs, and running recovery processes.

## Authentication

All API endpoints in the Flask application require API key authentication.

-   **API Key Configuration:** The server expects a valid API key to be set in the `RADONC_API_KEY` environment variable. If this variable is not set or is empty, the API will be inaccessible. Refer to the main `README.md`'s "Secret Management" section for details on setting environment variables.
-   **Authorization Header:** Clients must send the API key in the `Authorization` header using the `ApiKey` scheme.
    -   Example: `Authorization: ApiKey YOUR_ACTUAL_API_KEY_VALUE`

If authentication fails (e.g., missing or invalid API key), the server will respond with a `401 Unauthorized` error.

## Endpoints

- `POST /configure_backup`: Configure backup settings.
- `GET /view_logs?type=<log_type>`: View logs of different types.
  - Requires a `type` query parameter (e.g., `type=flask_app`, `type=pynetdicom`, `type=scu`) to specify which log file to retrieve. The available log types generally correspond to logger names defined in `src/config/logging.toml`.
  - Example: `curl -H "Authorization: ApiKey YOUR_KEY" "http://localhost:5000/view_logs?type=flask_app"`
- `POST /run_recovery`: Initiate a recovery process.

## Usage

1. Set up the Flask application:
    - Ensure necessary configurations (logging, environments) are present in the `src/config/` directory, as these are loaded by the application.
    - Define the `RADONC_API_KEY` environment variable with your desired API key. Other environment variables like `FLASK_RUN_HOST` or `FLASK_RUN_PORT` can also be set to override default Flask development server settings.
2. Run the Flask application using the command: `python src/app.py`.
3. Use the provided endpoints to interact with the application.
