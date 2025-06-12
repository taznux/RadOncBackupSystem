# Data Sources

Data source modules are responsible for connecting to various clinical systems, querying for relevant data, and initiating data transfer operations. For DICOM-based query/retrieve sources like ARIA and MIM, common functionalities are often provided by the `src.data_sources.dicom_qr_source.DicomQrDataSource` base class, which these specific sources extend.

The data sources provide DICOM data for backup and recovery processes. The following data sources are available:

## ARIA
- **Description**: A data source for ARIA systems.
- **Module**: `src/data_sources/aria.py`
- **Inherits from**: `src.data_sources.dicom_qr_source.DicomQrDataSource`
- **Methods**:
  - `query(self, query_dataset: Dataset, source_config: Dict[str, Any]) -> List[str]`: Queries the ARIA system using C-FIND based on the `query_dataset`. The `source_config` dictionary (derived from `environments.toml` for this source) must provide ARIA's AE details, typically `aet`, `ip`, and `port`. Returns a list of SOPInstanceUIDs found.
  - `transfer(self, retrieve_dataset: Dataset, source_config: Dict[str, Any], backup_destination_aet: str, calling_aet: str) -> bool`: Initiates a C-MOVE operation from the ARIA system. `retrieve_dataset` specifies the instances to move. `source_config` provides ARIA's AE details. `backup_destination_aet` is the AE Title of the target system where data should be sent (e.g., the Orthanc backup server or a staging SCP). `calling_aet` is the AE Title of the script initiating the transfer. Returns `True` on success.

## MIM
- **Description**: A data source for MIM systems.
- **Module**: `src/data_sources/mim.py`
- **Inherits from**: `src.data_sources.dicom_qr_source.DicomQrDataSource`
- **Methods**:
  - `query(self, query_dataset: Dataset, source_config: Dict[str, Any]) -> List[str]`: Queries the MIM system using C-FIND. `source_config` (from `environments.toml`) must provide MIM's AE details (`aet`, `ip`, `port`). Returns a list of SOPInstanceUIDs.
  - `transfer(self, retrieve_dataset: Dataset, source_config: Dict[str, Any], backup_destination_aet: str, calling_aet: str) -> bool`: Initiates a C-MOVE operation from the MIM system. `retrieve_dataset` specifies instances. `source_config` provides MIM's AE details. `backup_destination_aet` is the target AE Title. `calling_aet` is the script's AE Title. Returns `True` on success.
  *Note: While MIM typically uses C-GET for retrieval in some contexts, this `transfer` method, when used by `backup.py`, standardizes on C-MOVE via `DicomQrDataSource` for consistency in the backup workflow.*

## Mosaiq
- **Description**: A data source for Mosaiq systems.
- **Module**: `src/data_sources/mosaiq.py`
- **Methods**:
  - `query(self, sql_query: str, db_config: Dict[str, Any]) -> List[Dict[str, Any]]`: Queries the Mosaiq database using the provided `sql_query`. The `db_config` dictionary (from `environments.toml`) must contain connection details such as `server`, `database`, `username`, and `password` (which is resolved from an environment variable). Returns a list of rows as dictionaries.
  - `transfer(self, dicom_dataset: Dataset, target_scp_config: Dict[str, Any]) -> bool`: Transfers (via C-STORE) the provided `dicom_dataset` (which was generated from a database record by `cli/backup.py`) to a target SCP. The `target_scp_config` dictionary (from `environments.toml`, typically representing a staging SCP) must provide the target's AE details: `aet`, `ip`, and `port`. Returns `True` on success.
  - `get_treatment_summary_report(patient_mrn: str, db_config: dict, start_date: str = None, end_date: str = None) -> list`: Retrieves a simplified treatment summary report from the Mosaiq database using a hypothetical SQL query. It returns a list of dictionaries, where each dictionary represents a treatment record. This method is utilized by the `src/cli/get_report.py` tool.
