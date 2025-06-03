from .dicom_qr_source import DicomQrDataSource
# Note: Dataset, AE, SOP classes are now primarily used in DicomQrDataSource
# We might not need all of them directly in ARIA.py anymore if ARIA specific logic doesn't use them.
# from pydicom.dataset import Dataset # Keep if ARIA specific methods use it
# from pynetdicom import AE # Keep if ARIA specific methods use it
# from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind, StudyRootQueryRetrieveInformationModelMove # Keep if ARIA specific methods use it
import logging

# Module-level logger can still be used for ARIA-specific messages
# not originating from the DicomQrDataSource base methods.
logger = logging.getLogger(__name__)

class ARIA(DicomQrDataSource):
    """
    Represents the ARIA data source system.

    This class utilizes DicomQrDataSource for common DICOM C-FIND and C-MOVE
    operations and can be extended with ARIA-specific functionalities if needed.
    """
    def __init__(self):
        """
        Initializes the ARIA data source interface.
        It sets the source_name to "ARIA" for the base class.
        """
        super().__init__(source_name="ARIA")
        # The base class (DicomQrDataSource) initializes its own logger using logging.getLogger(__name__)
        # which will be 'src.data_sources.dicom_qr_source'.
        # The self.source_name = "ARIA" in the base class will be used in those log messages.
        # This ARIA module's own 'logger' (logging.getLogger(__name__)) can be used for
        # any ARIA-specific logging outside of the inherited query/transfer methods.
        logger.debug("ARIA DataSource initialized using DicomQrDataSource.")

    # query() and transfer() methods are inherited from DicomQrDataSource.
    # ARIA-specific overrides or new methods can be added here if necessary.
    # For example, if ARIA had a special way to prepare the query_dataset:
    #
    # def create_aria_specific_query(self, patient_id: str) -> Dataset:
    #     ds = Dataset()
    #     ds.PatientID = patient_id
    #     ds.QueryRetrieveLevel = "STUDY"
    #     # ... add any ARIA specific tags or default values
    #     self.logger.info(f"Created ARIA-specific query for patient {patient_id}")
    #     return ds
    #
    # This method could then be called by the application logic before calling self.query().
