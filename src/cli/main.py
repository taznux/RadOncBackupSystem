import click
import logging
from . import dicom_utils # Import the refactored dicom_utils

# It's good practice to have a logger for the main CLI module too
logger = logging.getLogger(__name__)

@click.group()
@click.option('--verbose', '-v', is_flag=True, help="Enable verbose logging for all commands.")
@click.pass_context
def robs(ctx, verbose):
    """
    RadOnc Backup System (ROBS) CLI.

    A unified command-line interface for various ROBS operations.
    """
    # Ensure ctx.obj is a dict
    ctx.obj = {}
    ctx.obj['VERBOSE'] = verbose

    # Basic logging configuration, can be refined later or by individual commands
    log_level = logging.DEBUG if verbose else logging.INFO
    # For now, let's ensure basicConfig is called if no handlers are set for the root logger.
    # More sophisticated logging might be loaded from config by specific commands later.
    if not logging.getLogger().hasHandlers():
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            handlers=[logging.StreamHandler()] # Ensure logs go to stderr/stdout
        )
    else: # If handlers exist, just set the level for the root logger
        logging.getLogger().setLevel(log_level)

    logger.debug(f"ROBS CLI started. Verbose mode: {verbose}")

# Add the dicom command group
robs.add_command(dicom_utils.dicom_cli_group)

if __name__ == '__main__':
    robs()
