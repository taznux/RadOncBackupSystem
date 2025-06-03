import os
import tomllib # Or tomli if Python < 3.11
import logging
import logging.config
import dotenv
from typing import Dict, Any, List, Union

logger = logging.getLogger(__name__)

class ConfigLoaderError(Exception):
    """Base exception for configuration loading errors."""

class MissingEnvVarError(ConfigLoaderError):
    """Raised when a required environment variable is not set."""

def _resolve_secrets(config_node: Union[Dict[str, Any], List[Any]], loaded_env_vars: Dict[str, str]) -> Union[Dict[str, Any], List[Any]]:
    """
    Recursively traverses the configuration structure to resolve environment variable placeholders.

    Placeholders are expected to be keys ending with '_env_var'.
    The corresponding environment variable is fetched, and its value replaces/creates
    a new key without the '_env_var' suffix.

    Example:
        "db_password_env_var": "MY_DB_PASSWORD"
        becomes (if MY_DB_PASSWORD is set in .env or os.environ):
        "db_password": "actual_password_value"

    Args:
        config_node: The current node (dict or list) of the configuration being processed.
        loaded_env_vars: Dictionary of environment variables (e.g., from os.environ).

    Returns:
        The configuration node with secrets resolved.

    Raises:
        MissingEnvVarError: If a key with '_env_var' suffix is found, but the
                            corresponding environment variable is not set.
    """
    if isinstance(config_node, dict):
        new_dict = {}
        for key, value in config_node.items():
            if isinstance(key, str) and key.endswith("_env_var"):
                env_var_name = str(value) # The value of *_env_var key is the name of the env variable
                env_var_value = loaded_env_vars.get(env_var_name)

                if env_var_value is not None:
                    new_key = key[:-len("_env_var")] # Remove the suffix
                    new_dict[new_key] = env_var_value
                    logger.debug(f"Resolved secret for '{new_key}' using environment variable '{env_var_name}'.")
                else:
                    # If an _env_var is specified, it implies it's required.
                    msg = (f"Required environment variable '{env_var_name}' not set, "
                           f"needed for config key '{key}'.")
                    logger.error(msg)
                    raise MissingEnvVarError(msg)
                # Do not copy the original *_env_var key to the new dict
            else:
                new_dict[key] = _resolve_secrets(value, loaded_env_vars)
        return new_dict
    elif isinstance(config_node, list):
        return [_resolve_secrets(item, loaded_env_vars) for item in config_node]
    else:
        return config_node

def load_config(
    config_path_environments: str,
    config_path_logging: str,
    config_path_dicom: Optional[str] = None # dicom.toml might be optional for some apps
) -> Dict[str, Any]:
    """
    Loads all application configurations (environments, logging, DICOM)
    and resolves secrets from environment variables.

    .env file is loaded if present. Environment variables override .env file values.

    Args:
        config_path_environments: Path to the environments TOML configuration file.
        config_path_logging: Path to the logging TOML configuration file.
        config_path_dicom: Optional path to the DICOM (AE) TOML configuration file.

    Returns:
        A dictionary containing all loaded and resolved configurations.
        Keys typically include 'environments', 'logging', 'dicom'.

    Raises:
        ConfigLoaderError: For file not found, TOML decoding issues, or missing env vars.
    """
    # Load .env file. Variables in os.environ will take precedence.
    dotenv_path = dotenv.find_dotenv(usecwd=True) # Find .env in current working dir or parents
    if dotenv_path:
        logger.info(f"Loading environment variables from: {dotenv_path}")
        dotenv.load_dotenv(dotenv_path)
    else:
        logger.info(".env file not found, using only OS environment variables.")

    # Use a copy of current environment variables for resolving secrets
    # This ensures consistency if os.environ is modified elsewhere during app lifetime.
    effective_env_vars = dict(os.environ)

    app_config: Dict[str, Any] = {}

    # Load logging configuration
    try:
        logger.info(f"Loading logging configuration from: {config_path_logging}")
        with open(config_path_logging, "rb") as f_log:
            logging_cfg = tomllib.load(f_log)
        logging.config.dictConfig(logging_cfg) # Apply logging config immediately
        app_config['logging'] = logging_cfg
        logger.info("Logging configuration loaded and applied.")
    except FileNotFoundError:
        msg = f"Logging configuration file not found: {config_path_logging}"
        logger.error(msg)
        raise ConfigLoaderError(msg) from None
    except tomllib.TOMLDecodeError as e:
        msg = f"Error decoding TOML from logging config {config_path_logging}: {e}"
        logger.error(msg)
        raise ConfigLoaderError(msg) from e
    except Exception as e: # Catch other errors like invalid logging schema
        msg = f"Error applying logging configuration from {config_path_logging}: {e}"
        logger.error(msg, exc_info=True) # Include stack trace for these errors
        raise ConfigLoaderError(msg) from e


    # Load DICOM AE configuration (optional)
    if config_path_dicom:
        try:
            logger.info(f"Loading DICOM AE configuration from: {config_path_dicom}")
            with open(config_path_dicom, "rb") as f_dicom:
                app_config['dicom'] = tomllib.load(f_dicom)
            logger.info("DICOM AE configuration loaded.")
        except FileNotFoundError:
            # If optional, this might be a warning or handled by app logic.
            # For now, let's log a warning if path is provided but file not found.
            logger.warning(f"DICOM AE configuration file not found: {config_path_dicom}. Proceeding without it.")
            app_config['dicom'] = {} # Ensure key exists
        except tomllib.TOMLDecodeError as e:
            msg = f"Error decoding TOML from DICOM AE config {config_path_dicom}: {e}"
            logger.error(msg)
            raise ConfigLoaderError(msg) from e
    else:
        logger.info("No DICOM AE configuration path provided, skipping.")
        app_config['dicom'] = {}


    # Load environments configuration and resolve secrets
    try:
        logger.info(f"Loading environments configuration from: {config_path_environments}")
        with open(config_path_environments, "rb") as f_env:
            environments_cfg_raw = tomllib.load(f_env)

        logger.info("Resolving secrets in environments configuration...")
        app_config['environments'] = _resolve_secrets(environments_cfg_raw, effective_env_vars)
        logger.info("Environments configuration loaded and secrets resolved.")

    except FileNotFoundError:
        msg = f"Environments configuration file not found: {config_path_environments}"
        logger.error(msg)
        raise ConfigLoaderError(msg) from None
    except tomllib.TOMLDecodeError as e:
        msg = f"Error decoding TOML from environments config {config_path_environments}: {e}"
        logger.error(msg)
        raise ConfigLoaderError(msg) from e
    # MissingEnvVarError from _resolve_secrets will propagate up.

    return app_config
