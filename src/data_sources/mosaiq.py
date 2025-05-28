from . import DataSource
import pyodbc
from pydicom.dataset import Dataset, FileMetaDataset
from pynetdicom import AE, evt, StoragePresentationContexts # evt and StoragePresentationContexts might be unused if only C-STORE SCU
from pynetdicom.sop_class import RTBeamsTreatmentRecordStorage
from pydicom.uid import generate_uid, ExplicitVRLittleEndian
import logging

logger = logging.getLogger(__name__)

class Mosaiq(DataSource):
    """
    Represents the Mosaiq data source system.

    This class provides methods to query data directly from the Mosaiq database
    and to transfer (send) DICOM RT Record objects to a DICOM C-STORE SCP.
    """
    DEFAULT_ODBC_DRIVER = "ODBC Driver 17 for SQL Server"

    def __init__(self, odbc_driver: str = None):
        """
        Initializes the Mosaiq data source interface.

        :param odbc_driver: The name of the ODBC driver to use for connecting to the Mosaiq database.
                            If None, defaults to "ODBC Driver 17 for SQL Server".
        :type odbc_driver: str, optional
        """
        super().__init__()
        self.odbc_driver = odbc_driver if odbc_driver is not None else self.DEFAULT_ODBC_DRIVER
        logger.debug(f"Mosaiq DataSource initialized with ODBC driver: {self.odbc_driver}")

    def query(self, sql_query: str, db_config: dict) -> list:
        """
        Executes a SQL query against the Mosaiq database.

        :param sql_query: The SQL query string to execute.
        :type sql_query: str
        :param db_config: A dictionary containing database connection parameters:
                          {'server': 'db_server_address',
                           'database': 'db_name',
                           'username': 'db_user',
                           'password': 'db_password'}
        :type db_config: dict
        :return: A list of rows fetched from the database as a result of the query.
                 Each row is typically a tuple of values.
        :rtype: list
        :raises pyodbc.Error: If database connection or query execution fails.
        """
        connection_string = (
            f"DRIVER={{{self.odbc_driver}}};"
            f"SERVER={db_config['server']};"
            f"DATABASE={db_config['database']};"
            f"UID={db_config['username']};"
            f"PWD={db_config['password']}"
        )
        logger.info(f"Connecting to Mosaiq database: {db_config['server']}/{db_config['database']} using driver {self.odbc_driver}")
        try:
            with pyodbc.connect(connection_string) as conn:
                with conn.cursor() as cursor:
                    logger.debug(f"Executing SQL query: {sql_query}")
                    cursor.execute(sql_query)
                    rows = cursor.fetchall()
                    logger.info(f"SQL query executed successfully, fetched {len(rows)} rows.")
                    return rows
        except pyodbc.Error as ex:
            sqlstate = ex.args[0]
            logger.error(f"Mosaiq database query failed. SQLSTATE: {sqlstate}. Error: {ex}", exc_info=True)
            raise # Re-raise the exception after logging

    def transfer(self, rt_record: Dataset, store_scp: dict):
        """
        Sends a DICOM RT Record Dataset to a C-STORE SCP.

        This method takes a pydicom Dataset (assumed to be an RT Record),
        ensures its meta information is correctly set, and then sends it
        to the specified DICOM C-STORE SCP.

        :param rt_record: The pydicom Dataset to send (e.g., an RTBeamsTreatmentRecord).
                          This dataset should be populated with patient/study/series information.
                          SOPClassUID and SOPInstanceUID will be set/updated by this method.
        :type rt_record: pydicom.dataset.Dataset
        :param store_scp: A dictionary containing the C-STORE SCP details:
                          {'IP': 'host_ip', 'Port': port_number, 'AETitle': 'AE_TITLE'}
        :type store_scp: dict
        :raises TypeError: If `rt_record` is not a pydicom Dataset.
        :raises Exception: Can raise various exceptions from pynetdicom related to
                           network issues or DICOM protocol errors.
        """
        if not isinstance(rt_record, Dataset):
            logger.error("Invalid rt_record type. Must be a pydicom Dataset.")
            raise TypeError("rt_record must be a pydicom Dataset object")

        logger.info(f"Preparing to transfer RT Record SOPInstanceUID (original/to be generated): "
                    f"{rt_record.get('SOPInstanceUID', 'Not Set Yet')} to SCP {store_scp['AETitle']}.")

        # Set/Overwrite SOP Class and Instance UIDs for this specific record instance
        rt_record.SOPClassUID = RTBeamsTreatmentRecordStorage
        if not hasattr(rt_record, 'SOPInstanceUID') or not rt_record.SOPInstanceUID:
            rt_record.SOPInstanceUID = generate_uid()
            logger.debug(f"Generated new SOPInstanceUID for RT Record: {rt_record.SOPInstanceUID}")
        
        # Create file_meta explicitly if it doesn't exist or needs standardization
        if not hasattr(rt_record, 'file_meta') or rt_record.file_meta is None:
            rt_record.file_meta = FileMetaDataset()
            logger.debug("Created new FileMetaDataset for RT Record.")
        
        # Populate File Meta Information
        rt_record.file_meta.FileMetaInformationVersion = b'\x00\x01'
        rt_record.file_meta.MediaStorageSOPClassUID = rt_record.SOPClassUID
        rt_record.file_meta.MediaStorageSOPInstanceUID = rt_record.SOPInstanceUID
        rt_record.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian 
        rt_record.file_meta.ImplementationClassUID = generate_uid(prefix='1.2.826.0.1.3680043.9.7156.1.') # Example OID
        rt_record.file_meta.ImplementationVersionName = "PYNETDICOM_MOSAIQ_1.0"
        
        # Ensure dataset encoding matches the TransferSyntaxUID for pynetdicom
        rt_record.is_little_endian = True
        rt_record.is_implicit_VR = False # Explicit VR Little Endian

        ae = AE()
        # Add requested presentation context for the SOP Class and Transfer Syntax being sent
        ae.add_requested_context(rt_record.SOPClassUID, rt_record.file_meta.TransferSyntaxUID)
        
        logger.info(f"Attempting C-STORE association to SCP: {store_scp['AETitle']} at {store_scp['IP']}:{store_scp['Port']}")
        assoc = ae.associate(store_scp['IP'], store_scp['Port'], ae_title=store_scp['AETitle'])
        
        if assoc.is_established:
            logger.info("C-STORE Association established.")
            # Log accepted presentation contexts
            if assoc.accepted_contexts:
                for context in assoc.accepted_contexts:
                    logger.info(f"Accepted Presentation Context: Abstract Syntax {context.abstract_syntax}, Transfer Syntax {context.transfer_syntax}")
            else:
                logger.warning("No presentation contexts accepted by SCP!")
            try:
                status = assoc.send_c_store(rt_record)
                if status:
                    logger.info(f"C-STORE request completed. Status: 0x{status.Status:04X}.")
                    if hasattr(status, 'ErrorComment') and status.ErrorComment:
                        logger.warning(f"C-STORE Error Comment: {status.ErrorComment}")
                else:
                    logger.error("C-STORE request failed: No status returned (connection timed out or aborted).")
            except Exception as e:
                logger.error(f"Exception during C-STORE operation: {e}", exc_info=True)
            finally:
                logger.debug("Releasing C-STORE association.")
                assoc.release()
        else:
            logger.error(f"C-STORE Association rejected, aborted or never connected to SCP: {store_scp['AETitle']}.")

    def get_treatment_summary_report(self, patient_mrn: str, db_config: dict, start_date: str = None, end_date: str = None) -> list:
        """
        Retrieves a simplified treatment summary report from the Mosaiq database.

        :param patient_mrn: The Medical Record Number of the patient.
        :type patient_mrn: str
        :param db_config: A dictionary containing database connection parameters.
        :type db_config: dict
        :param start_date: Optional start date for the report (YYYY-MM-DD).
        :type start_date: str, optional
        :param end_date: Optional end date for the report (YYYY-MM-DD).
        :type end_date: str, optional
        :return: A list of dictionaries, where each dictionary represents a treatment record.
                 Returns an empty list if no records are found or in case of a non-connection error.
        :rtype: list
        :raises pyodbc.Error: If database connection or critical query execution fails.
        """
        # Hypothetical SQL query - actual table and field names will vary.
        # This query assumes tables for Patient, Course, Plan, and Site.
        sql_query = f"""
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
                TxField TxFld ON Pat.Pat_IDE = TxFld.Pat_IDE -- Hypothetical join, actual schema needed
            -- Additional JOINs would be needed here for a real query, e.g., to link to Course or Plan tables
            -- JOIN Course Crs ON Pat.Pat_IDE = Crs.Pat_IDE
            -- JOIN Plan Pln ON Crs.Crs_IDE = Pln.Crs_IDE
            WHERE
                Pat.Pat_ID1 = '{patient_mrn}'
        """

        if start_date:
            sql_query += f" AND TxFld.Plan_Start_DtTm >= '{start_date}'"
        if end_date:
            sql_query += f" AND TxFld.Plan_End_DtTm <= '{end_date}'"
        
        sql_query += """
            GROUP BY
                Pat.Last_Name, Pat.First_Name, Pat.Pat_ID1, TxFld.Plan_Start_DtTm, TxFld.Plan_End_DtTm, TxFld.VS_ID
            ORDER BY
                TxFld.Plan_Start_DtTm DESC;
        """
        
        logger.info(f"Fetching treatment summary report for MRN: {patient_mrn} with date range: {start_date} - {end_date}")
        
        try:
            rows = self.query(sql_query, db_config)
            if not rows:
                logger.info(f"No treatment records found for MRN: {patient_mrn}")
                return []

            # Assuming column names are returned by the query method or are known
            # For pyodbc, cursor.description provides column names
            # However, self.query returns a list of tuples directly.
            # We need to know the column order from the SELECT statement.
            # Hypothetical column names based on the SELECT query:
            column_names = ["PatientName", "PatientMRN", "StartDate", "EndDate", "TotalDose", "NumberOfFractions", "TargetVolume"]
            
            report_data = []
            for row in rows:
                # Convert row (tuple) to dictionary
                record = dict(zip(column_names, row))
                report_data.append(record)
            
            logger.info(f"Successfully fetched {len(report_data)} treatment records for MRN: {patient_mrn}")
            return report_data
        except pyodbc.Error as e:
            # Specific handling for "No results. Previous SQL was not a query." if the query was not a SELECT.
            # This shouldn't happen here, but good to be aware of.
            if "No results" in str(e) and "Previous SQL was not a query" in str(e):
                 logger.warning(f"The SQL query for MRN {patient_mrn} did not return results (possibly not a SELECT query or empty table): {e}")
                 return [] # Return empty list for non-critical errors like no data found
            logger.error(f"Database error while fetching treatment summary for MRN {patient_mrn}: {e}", exc_info=True)
            raise # Re-raise for connection errors or critical query failures
        except Exception as e:
            logger.error(f"An unexpected error occurred while fetching treatment summary for MRN {patient_mrn}: {e}", exc_info=True)
            raise
