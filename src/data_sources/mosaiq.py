from . import DataSource
import pyodbc
from pydicom.dataset import Dataset, FileMetaDataset
from pynetdicom import AE # evt and StoragePresentationContexts removed
from pynetdicom.sop_class import RTBeamsTreatmentRecordStorage
from pydicom.uid import generate_uid, ExplicitVRLittleEndian
import logging
from typing import List, Dict, Optional, Any, Tuple


logger = logging.getLogger(__name__)


class MosaiqQueryError(Exception):
    """Custom exception for errors during Mosaiq database queries."""

    pass


class Mosaiq(DataSource):
    """
    Represents the Mosaiq data source system.

    This class provides methods to query data directly from the Mosaiq database
    and to transfer (send) DICOM RT Record objects to a DICOM C-STORE SCP.
    """

    DEFAULT_ODBC_DRIVER = "ODBC Driver 17 for SQL Server"
    # Expected column names from the get_treatment_summary_report SQL query
    # This should be kept in sync with the SQL query structure.
    _TREATMENT_SUMMARY_COLUMNS = [
        "PatientName",
        "PatientMRN",
        "StartDate",
        "EndDate",
        "TotalDose",
        "NumberOfFractions",
        "TargetVolume",
    ]

    def __init__(self, odbc_driver: Optional[str] = None):
        """
        Initializes the Mosaiq data source interface.

        Args:
            odbc_driver: The name of the ODBC driver to use for connecting
                         to the Mosaiq database. If None, defaults to
                         "ODBC Driver 17 for SQL Server".
        """
        super().__init__()
        self.odbc_driver = (
            odbc_driver if odbc_driver is not None else self.DEFAULT_ODBC_DRIVER
        )
        logger.debug(f"Mosaiq DataSource initialized with ODBC driver: {self.odbc_driver}")

    def query(self, sql_query: str, db_config: Dict[str, str]) -> List[Tuple[Any, ...]]:
        """
        Executes a SQL query against the Mosaiq database.

        Args:
            sql_query: The SQL query string to execute.
            db_config: A dictionary containing database connection parameters:
                       {"server": "db_server_address",
                        "database": "db_name",
                        "username": "db_user",
                        "password": "db_password"}

        Returns:
            A list of rows fetched from the database as a result of the query.
            Each row is a tuple of values.

        Raises:
            MosaiqQueryError: If database connection or query execution fails,
                              wrapping the original pyodbc.Error.
        """
        connection_string = (
            f"DRIVER={{{self.odbc_driver}}};"
            f"SERVER={db_config['server']};"
            f"DATABASE={db_config['database']};"
            f"UID={db_config['username']};"
            f"PWD={db_config['password']}"
        )
        logger.info(
            f"Connecting to Mosaiq database: {db_config['server']}/{db_config['database']} "
            f"using driver {self.odbc_driver}"
        )
        try:
            # autocommit=True can be useful for read-only scenarios or when DML doesn't need explicit transaction mgmt
            with pyodbc.connect(connection_string, autocommit=True) as conn:
                with conn.cursor() as cursor:
                    logger.debug(f"Executing SQL query: {sql_query}")
                    cursor.execute(sql_query)
                    rows = cursor.fetchall()
                    # Ensure consistent return type (list of tuples)
                    processed_rows = [tuple(row) for row in rows]
                    logger.info(
                        f"SQL query executed successfully, fetched {len(processed_rows)} rows."
                    )
                    return processed_rows
        except pyodbc.Error as ex:
            sqlstate = ex.args[0] if ex.args else "Unknown SQLSTATE"
            logger.error(
                f"Mosaiq database query failed. SQLSTATE: {sqlstate}. Error: {ex}",
                exc_info=True,
            )
            raise MosaiqQueryError(f"Database query failed: {ex}") from ex

    def transfer(self, rt_record: Dataset, store_scp: Dict[str, Any]):
        """
        Sends a DICOM RT Record Dataset to a C-STORE SCP.

        Args:
            rt_record: The pydicom Dataset to send.
            store_scp: A dictionary containing the C-STORE SCP details.

        Raises:
            TypeError: If `rt_record` is not a pydicom Dataset.
            DicomConnectionError: For association failures.
            DicomOperationError: For C-STORE operation failures.
        """
        if not isinstance(rt_record, Dataset):
            logger.error("Invalid rt_record type. Must be a pydicom Dataset.")
            raise TypeError("rt_record must be a pydicom Dataset object")

        logger.info(
            f"Preparing to transfer RT Record SOPInstanceUID "
            f"{rt_record.get('SOPInstanceUID', 'Not Set Yet')} to SCP {store_scp['AETitle']}."
        )

        rt_record.SOPClassUID = RTBeamsTreatmentRecordStorage
        if not getattr(rt_record, "SOPInstanceUID", None): # Pythonic check for attr
            rt_record.SOPInstanceUID = generate_uid()
            logger.debug(
                f"Generated new SOPInstanceUID for RT Record: {rt_record.SOPInstanceUID}"
            )

        if not getattr(rt_record, "file_meta", None):
            rt_record.file_meta = FileMetaDataset()
            logger.debug("Created new FileMetaDataset for RT Record.")

        rt_record.file_meta.FileMetaInformationVersion = b"\x00\x01"
        rt_record.file_meta.MediaStorageSOPClassUID = rt_record.SOPClassUID
        rt_record.file_meta.MediaStorageSOPInstanceUID = rt_record.SOPInstanceUID
        rt_record.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        # Ensure a unique ImplementationClassUID for your application
        rt_record.file_meta.ImplementationClassUID = generate_uid(
            prefix="1.2.826.0.1.3680043.9.7156.1.99." # Example, make it specific
        )
        rt_record.file_meta.ImplementationVersionName = "RadOncBackupSystem_Mosaiq_1.0"

        rt_record.is_little_endian = True
        rt_record.is_implicit_VR = False

        ae = AE()
        ae.add_requested_context(
            rt_record.SOPClassUID, rt_record.file_meta.TransferSyntaxUID
        )

        logger.info(
            f"Attempting C-STORE association to SCP: {store_scp['AETitle']} at {store_scp['IP']}:{store_scp['Port']}"
        )
        
        assoc = None
        try:
            assoc = ae.associate(
                store_scp["IP"], store_scp["Port"], ae_title=store_scp["AETitle"]
            )
            if assoc.is_established:
                logger.info("C-STORE Association established.")
                if not assoc.accepted_contexts:
                    logger.warning("No presentation contexts accepted by SCP!")
                    # Depending on strictness, this could be an error
                
                status = assoc.send_c_store(rt_record)
                if status:
                    logger.info(f"C-STORE request completed. Status: 0x{status.Status:04X}.")
                    if hasattr(status, "ErrorComment") and status.ErrorComment:
                        logger.warning(f"C-STORE Error Comment: {status.ErrorComment}")
                    if status.Status != 0x0000: # Check if status is not success
                        # Raise a specific error for C-STORE failure
                        raise MosaiqQueryError(f"C-STORE operation failed with status 0x{status.Status:04X}. SCP Comment: {status.ErrorComment or 'N/A'}")
                else:
                    # This is a more severe pynetdicom level issue
                    raise MosaiqQueryError("C-STORE request failed: No status returned (connection timed out or aborted).")
            else:
                # Association failed
                reason = assoc.acceptor.primitive.result_str if assoc.acceptor and assoc.acceptor.primitive else "Unknown reason"
                raise MosaiqQueryError(f"C-STORE Association rejected or aborted: {reason}")
        except Exception as e: # Catch pynetdicom internal errors, socket errors, or our own MosaiqQueryError
            logger.error(f"Exception during C-STORE operation or association: {e}", exc_info=True)
            # Re-raise as a consistent error type if not already one
            if not isinstance(e, MosaiqQueryError):
                 raise MosaiqQueryError(f"C-STORE process failed: {e}") from e
            else:
                raise
        finally:
            if assoc and assoc.is_established:
                logger.debug("Releasing C-STORE association.")
                assoc.release()


    def _build_treatment_summary_sql(
        self, patient_mrn: str, start_date: Optional[str], end_date: Optional[str]
    ) -> str:
        """
        Constructs the SQL query for the treatment summary report.

        Args:
            patient_mrn: The Medical Record Number of the patient.
            start_date: Optional start date for filtering (YYYY-MM-DD).
            end_date: Optional end date for filtering (YYYY-MM-DD).

        Returns:
            The formatted SQL query string.
        """
        # IMPORTANT: This query uses f-string formatting for MRN and dates for brevity.
        # In a production system, always use parameterized queries to prevent SQL injection.
        # This would require self.query() to be adapted to accept parameters.
        # Example of parameterized query part: "WHERE Pat.Pat_ID1 = ?"
        # And then: cursor.execute(sql_query_base, (patient_mrn,))

        # Using a more readable multi-line string format for the SQL query
        # Actual table and field names will vary based on Mosaiq schema.
        sql_query_base = f"""
            SELECT
                Pat.Last_Name + ', ' + Pat.First_Name AS PatientName,
                Pat.Pat_ID1 AS PatientMRN,
                TxFld.Plan_Start_DtTm AS StartDate,
                TxFld.Plan_End_DtTm AS EndDate,
                SUM(TxFld.Dose_Tx_Sum) AS TotalDose,
                SUM(TxFld.Fractions_Sum) AS NumberOfFractions,
                TxFld.VS_ID AS TargetVolume
            FROM
                Patient Pat
            JOIN
                TxField TxFld ON Pat.Pat_IDE = TxFld.Pat_IDE -- Hypothetical join
            WHERE
                Pat.Pat_ID1 = '{patient_mrn}'
        """

        date_filters: List[str] = []
        if start_date:
            date_filters.append(f" AND TxFld.Plan_Start_DtTm >= '{start_date}'")
        if end_date:
            date_filters.append(f" AND TxFld.Plan_End_DtTm <= '{end_date}'")

        sql_query_filters = "".join(date_filters)

        sql_query_suffix = """
            GROUP BY
                Pat.Last_Name, Pat.First_Name, Pat.Pat_ID1,
                TxFld.Plan_Start_DtTm, TxFld.Plan_End_DtTm, TxFld.VS_ID
            ORDER BY
                TxFld.Plan_Start_DtTm DESC;
        """
        return sql_query_base + sql_query_filters + sql_query_suffix

    def get_treatment_summary_report(
        self,
        patient_mrn: str,
        db_config: Dict[str, str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves a simplified treatment summary report from the Mosaiq database.

        Args:
            patient_mrn: The Medical Record Number (MRN) of the patient.
            db_config: Database connection parameters.
            start_date: Optional start date for the report (format YYYY-MM-DD).
            end_date: Optional end date for the report (format YYYY-MM-DD).

        Returns:
            A list of dictionaries, each representing a treatment record.
            Returns an empty list if no records are found.

        Raises:
            MosaiqQueryError: If there's an issue with query execution.
            ValueError: If query results don't match expected column structure.
        """
        sql_query = self._build_treatment_summary_sql(
            patient_mrn, start_date, end_date
        )

        logger.info(
            f"Fetching treatment summary report for MRN: {patient_mrn} "
            f"with date range: {start_date or 'N/A'} - {end_date or 'N/A'}"
        )

        try:
            rows = self.query(sql_query, db_config)
            if not rows:
                logger.info(f"No treatment records found for MRN: {patient_mrn}")
                return []

            report_data: List[Dict[str, Any]] = []
            for row_tuple in rows:
                if len(row_tuple) != len(self._TREATMENT_SUMMARY_COLUMNS):
                    logger.error(
                        f"Query for MRN {patient_mrn} returned an unexpected number of columns. "
                        f"Expected {len(self._TREATMENT_SUMMARY_COLUMNS)}, got {len(row_tuple)}."
                    )
                    # This indicates a mismatch between SQL query and defined columns.
                    raise ValueError(
                        "Mismatch between expected columns and query result columns."
                    )
                record = dict(zip(self._TREATMENT_SUMMARY_COLUMNS, row_tuple))
                report_data.append(record)

            logger.info(
                f"Successfully fetched {len(report_data)} treatment records for MRN: {patient_mrn}"
            )
            return report_data
        except MosaiqQueryError: # Already logged in self.query()
            raise
        except ValueError: # Already logged above
            raise
        except Exception as e:
            logger.error(
                f"An unexpected error occurred while processing treatment summary for MRN {patient_mrn}: {e}",
                exc_info=True,
            )
            # Wrap unexpected errors for consistent error handling by the caller
            raise MosaiqQueryError(f"Unexpected error processing report data: {e}") from e
