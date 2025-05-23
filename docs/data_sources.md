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
