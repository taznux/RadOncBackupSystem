# Data Sources

The data sources provide DICOM data for backup and recovery processes. The following data sources are available:

## ARIA
- **Description**: A data source for ARIA systems.
- **Module**: `src/data_sources/aria.py`
- **Methods**:
  - `query(query_dataset: Dataset, qr_scp: dict)`: Queries the ARIA system for DICOM data.
  - `transfer(move_dataset: Dataset, qr_scp: dict, store_scp: dict, handle_store)`: Transfers DICOM data from ARIA to the specified storage SCP.

## MIM
- **Description**: A data source for MIM systems.
- **Module**: `src/data_sources/mim.py`
- **Methods**:
  - `query(query_dataset: Dataset, qr_scp: dict)`: Queries the MIM system for DICOM data.
  - `transfer(get_dataset: Dataset, qr_scp: dict, store_scp: dict, handle_store)`: Transfers DICOM data from MIM to the specified storage SCP.

## Mosaiq
- **Description**: A data source for Mosaiq systems.
- **Module**: `src/data_sources/mosaiq.py`
- **Methods**:
  - `query(sql_query: str, db_config: dict)`: Queries the Mosaiq system for DICOM data using SQL.
  - `transfer(rt_record_data: dict, store_scp: dict)`: Transfers DICOM data from Mosaiq to the specified storage SCP.
  - `get_treatment_summary_report(patient_mrn: str, db_config: dict, start_date: str = None, end_date: str = None) -> list`: Retrieves a simplified treatment summary report from the Mosaiq database using a hypothetical SQL query. It returns a list of dictionaries, where each dictionary represents a treatment record. This method is utilized by the `src/cli/get_report.py` tool.
