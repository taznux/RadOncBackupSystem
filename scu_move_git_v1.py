import copy
from datetime import datetime, timedelta
import logging.config
import time
import tomllib
import os

import pydicom
import pynetdicom.status
from pynetdicom import AE, evt, StoragePresentationContexts
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelMove, RTBeamsTreatmentRecordStorage, RTPlanStorage
from tenacity import retry, TryAgain, wait_exponential, stop_after_attempt, RetryError
from pynetdicom.pdu_primitives import SOPClassExtendedNegotiation
from scu_find_git_v1 import find

from scu_move_support_git_v1 import run_with_scu_move

with open ('logging.toml', 'rb') as f:
    logging.config.dictConfig(tomllib.load(f))
    LOGGER = logging.getLogger('scu_move')
    LOGGER2 = logging.getLogger('error_test')

with open('config.toml', 'rb') as f:
    sockets = tomllib.load(f)   
    SCU = sockets['local']
    QR_SCP = sockets['aria']
    LOCAL_STORE_SCP = sockets['local']
    PACS_STORE_SCP = sockets['mim_server']

def handle_store(event):
    """Handle a C-STORE service request"""
    ds = event.dataset
    ds.file_meta = event.file_meta
    global moved_ds
    moved_ds = ds
    pacs_store(ds, SCU['AETitle'], PACS_STORE_SCP['IP'], PACS_STORE_SCP['Port'], PACS_STORE_SCP['AETitle'])
    return 0x0000


@retry(wait=wait_exponential(multiplier=1, exp_base=2), stop=stop_after_attempt(7))
def move(ds, handler_function):

    ae = AE(SCU['AETitle'])
    ae.add_requested_context(StudyRootQueryRetrieveInformationModelMove)
    ae.supported_contexts = StoragePresentationContexts
    ae.acse_timeout = 120
    ae.dimse_timeout = 121
    ae.network_timeout = 122

    handlers = [(evt.EVT_C_STORE, handler_function)]

    ext_neg = []
    app_info = b""
    app_info += b"\x01"
    item1 = SOPClassExtendedNegotiation()

    # The class uid is necessary when interacting with a SCP
    item1.sop_class_uid = StudyRootQueryRetrieveInformationModelMove
    item1.service_class_application_information = app_info
    ext_neg = [item1]

    scp = ae.start_server((SCU['IP'], SCU['Port']), evt_handlers=handlers, block=False)

    assoc = ae.associate(QR_SCP['IP'], QR_SCP['Port'], ae_title=QR_SCP['AETitle'], ext_neg=ext_neg)

    if assoc.is_established:
        LOGGER.info(f"Association established with {QR_SCP['AETitle']}")
        responses = assoc.send_c_move(ds, LOCAL_STORE_SCP['AETitle'], StudyRootQueryRetrieveInformationModelMove)
        for (status, identifier) in responses:
            if status:
                LOGGER.debug(f'C-MOVE query status: 0x{status.Status:04X}')
            else:
                LOGGER.warning('Connection timed out, was aborted or received invalid response')
        if status.Status != 0x0000:
            assoc.release()
            raise ConnectionError(f'C-MOVE failed ... status 0x{status.Status:04X}')
        assoc.release()
    else:
        LOGGER.error('Association rejected, aborted or never connected')
        raise ConnectionError('Association rejected, aborted or never connected')
    
    scp.shutdown()

    return moved_ds
    
def pacs_store(ds, calling_ae, assoc_ip, assoc_port, assoc_ae):
    ae2 = AE(calling_ae)
    ae2.requested_contexts = StoragePresentationContexts
    ae2.acse_timeout = 120
    ae2.dimse_timeout = 121
    ae2.network_timeout = 122

    assoc = ae2.associate(addr = assoc_ip, port=assoc_port, ae_title=assoc_ae)

    if assoc.is_established:
        # Use the C-STORE service to send the dataset
        # returns the response status as a pydicom Dataset
        status = assoc.send_c_store(ds)

        # Check the status of the storage request
        if status:
            # If the storage request succeeded this will be 0x0000
            LOGGER.debug('C-STORE request status: 0x{0:04x}'.format(status.Status))
        else:
            LOGGER.warning('Connection timed out, was aborted or received invalid response')

        # Release the association
        assoc.release()
    else:
        print('Association rejected, aborted or never connected')
    

def update_daily_num_file_log_2(log_path, today_date, log_date, sum_log_path):

    with open(log_path, "r") as f1:
        lines = f1.readlines()
        with open(sum_log_path, "w") as f2: #open the file for writing the new number of files that need to be transferred
            f2.write(today_date + '\n') #write the date as the previous data from the file is wiped
            f2.write(str(len(lines) - 1)) #write the new total number of files transferred


if __name__ == '__main__':
    today_date = datetime.today().strftime('%Y%m%d')

    move_failures = set()

    backup_log = r''
    backup_sum_log = r""
    backup_failure_log = r""

    temp_move_path = r''
    missing_objects_file_path = r''

    # Generate Dataset
    ds = pydicom.Dataset()
    ds.PatientID = ''
    ds.StudyInstanceUID = ''
    ds.SeriesInstanceUID = ''
    ds.QueryRetrieveLevel = 'IMAGE'
    ds.Modality = 'RTRECORD'
    ds.SOPClassUID = RTBeamsTreatmentRecordStorage
    ds.TreatmentDate = today_date
    ds.SOPInstanceUID = ''

    # Find All Records For Today
    todays_uids = find(ds)


    #Create backup file if it does not exist    
    if not os.path.exists(backup_log): 
        open(backup_log, 'x')

    # Compare With Records Already Backed Up Today
    with open(backup_log, 'r') as f1:
        log_date = f1.readline().strip() # first line of log file is the date
        if log_date != today_date:
            with open(backup_log, 'w') as f2:
                f2.write(today_date)
                f2.close()
            with open(backup_failure_log, 'w') as f3:
                f3.write(today_date + '\n')
                f3.close()
            completed = set() # cheating but we know the file was just created so it will be empty added as a formality
            uncompleted = todays_uids - completed
            print(len(completed), len(uncompleted))
            uids = '\\'.join(uncompleted)
        else:
            completed = set(f1.read().splitlines())
            uncompleted = todays_uids - completed
            print(len(completed), len(uncompleted))
            uids = '\\'.join(uncompleted)

    # Back Up New Records (If Any)
    if uncompleted:
        for returned_uid in uncompleted:
            ds.SOPInstanceUID = returned_uid
            try:
                move(ds, handle_store)
            except RetryError:  #Check to make sure each rtrecord was moved to MIM
                move_failures.add(returned_uid)

        # Update Daily Log File With Newly Backed Up Records
        # Overwrite Log File If Date Has Changed (this is done to prevent file from getting too large)
        uncompleted = uncompleted - move_failures #remove all failed transfer from set that is written to disk
        # false_failures = []
        with open(backup_log, 'a') as f1:
            for uid in uncompleted:
                f1.write('\n' + uid)
        with open(backup_log, 'r') as f1:
            if move_failures: #if there are failures
                with open(backup_failure_log, 'r+') as f2:
                    lines2 = f2.readlines()
                    lines2 = [line.rstrip('\n') for line in lines2] #read in all data from the failure_log_file
                    for uid in move_failures: #loop through all the failures
                        if uid not in lines2: 
                            f2.write('\n' + uid) #write to file if the uid is not already in the failures file

            lines1 = f1.readlines() #get all of the successful backups for that day
            lines1 = [line.rstrip('\n') for line in lines1] #remove '\n' from lines
            with open(backup_failure_log, 'r') as f3:
                lines3 = f3.readlines() #get all the failed uids
                lines3 = [line.rstrip('\n') for line in lines3] #read in all data from the updated failure_log_file
                with open(backup_failure_log, 'w') as f4: #open the file for writing and wipe it
                    f4.write(today_date) #write todays date
                    for line in lines3:
                        if line not in lines1: #if failure was not successfully backed up in a different iteration; lines 1 is list of entires in daily_backup.log
                            f4.write('\n' + line) #write to disk
                        else:
                            pass #failure was successfully backed up in a different iteration, so do not write to log file

        run_with_scu_move(uncompleted, 'rtrecord_uid', 'rtplan_dataset', 'rtstruct_dataset', 'ct_dataset', temp_move_path, today_date)
    update_daily_num_file_log_2(backup_log, today_date, log_date, backup_sum_log)
