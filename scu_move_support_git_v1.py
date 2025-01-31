import pydicom
import pynetdicom.status
from pynetdicom import AE, evt, AllStoragePresentationContexts, StoragePresentationContexts
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelMove, RTBeamsTreatmentRecordStorage, StudyRootQueryRetrieveInformationModelFind, PatientRootQueryRetrieveInformationModelMove, Verification
import os
import copy
import tomllib
from datetime import datetime
from tenacity import retry, TryAgain, wait_exponential, stop_after_attempt, RetryError

with open('config.toml', 'rb') as f:
    sockets = tomllib.load(f)   
    SCU = sockets['local']
    QR_SCP = sockets['aria']
    LOCAL_STORE_SCP = sockets['local']
    PACS_STORE_SCP = sockets['mim_server']
    PACS_QUERY_SCP = sockets['mim_server_qr']

def create_patient_dictionary(num_patients):

    """
    Creates a dictionary for each patient corresponding to an RTRECORD UID

    Args:
        num_patients (int): Number of patients to create patient dictionary entries for
    Returns:
        patient_dict (dictionary): initialized dictionary with a unique key for each patient
    """

    patient_dict = {}
    for patient in range(num_patients):
        patient_string = "patient_" + str(patient)
        patient_dict[patient_string] = {}
    return patient_dict


def aria_query_function(dataset, local_ae_title, qr_ip, qr_port, qr_ae_title):

    """
    Queries ARIA using a specified dataset

    Args:
        dataset (pydicom dataset): Dataset that will be queried for in MIM
        identifier (str): unique identifier for this query so it can be separated from the other queries

    Returns:
        rtrecord_dataset (string): Dataset returned from the RTRecord query
    """

    ae = AE(ae_title=local_ae_title)
    ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)

    assoc = ae.associate(addr=qr_ip, port=qr_port, ae_title=qr_ae_title)
    if assoc.is_established:
        # Send the C-FIND request
        responses = assoc.send_c_find(dataset, StudyRootQueryRetrieveInformationModelFind)
        for (status, identifier) in responses:
            if status and status.Status in [0xff00]:
                if identifier:
                    try:
                        rtrecord_dataset = identifier
                    except IndexError:
                        rtrecord_dataset = ''
            elif status and status.Status in [0x0000]:
                pass
            else:
                print('Connection timed out, was aborted or received invalid response')
        # Release the association
        assoc.release()
    else:
        pass

    try:
        if rtrecord_dataset:
            return rtrecord_dataset
        else:
            return ''
    except NameError: #query was unsucessful
        return ''

def mim_query_function(dataset, key, local_ae_title, qr_addr, qr_port, qr_ae_title):
        
    """
    Queries MIM using a specified dataset

    Args:
        dataset (pydicom dataset): Dataset that will be queried for in MIM
        key (str): unique identifier for this query so it can be separated from the other queries

    Returns:
        c_find_responses (dictionary): Dictionary containing the returned pydicom datasets from the MIM query
    """

    ae = AE(ae_title=local_ae_title)
    ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)

    c_find_responses = {}

    assoc = ae.associate(addr=qr_addr, port=qr_port, ae_title = qr_ae_title)
    if assoc.is_established:
        #Send the C-FIND request
        responses = assoc.send_c_find(dataset, StudyRootQueryRetrieveInformationModelFind)
        iteration=0
        for (status, identifier) in responses:
            if status and status.Status in [0xff00]:
                sub_key = str(key) + "_sub_key_" + str(iteration)            
                c_find_responses[sub_key] = identifier
                iteration += 1
            elif status and status.Status in [0x0000]:
                pass
            else:
                pass
        # Release the association
        assoc.release()
    else:
        pass

    return c_find_responses


def c_move_service(input_dataset, trgt_modality, input_path, local_ip, local_port, local_ae, pacs_ip, pacs_port, pacs_ae, destination_ae):
    
    """
    Function to move a dicom file from MIM to the local computer

    Args:
        input_dataset (pydicom datset): Dataset that contains the information from the desired DICOM object to move
        trgt_modality (string): Dicom object to move (ex. 'RTSTRUCT')
        input_path (string): Path on the local computer where the moved DICOM object will be stored

    Returns:
       complete_file_path_list (list): list of strings pointing to the locations of the saved DICOM objects
    """

    global complete_file_path_list

    complete_file_path_list = []

    def handle_store(event):

        """
        Event handler to handle the returned dicom object from the c_move command

        Args:
            event (pydicom dataset):

        Returns:
            Success or failure
        """        

        ds = event.dataset
        ds.file_meta = event.file_meta
        
        modality = ds.Modality
        sop_uid = ds.SOPInstanceUID

        if modality == trgt_modality:
            filename = str(modality) + "_" + str(sop_uid) + ".dcm"

            path = input_path

            ds.save_as(os.path.join(path, filename), write_like_original=False) #Save the dataset

            global complete_file_path_list
    
            complete_file_path_list.append(os.path.join(path, filename))

            # Return a 'Success' status
            return 0x0000
    
        else:
            print("Wrong modality sent, doing nothing")
            return 0x0000

    ae = AE(ae_title=local_ae)
    ae.add_requested_context(PatientRootQueryRetrieveInformationModelMove)
    ae.supported_contexts = AllStoragePresentationContexts
    ae.add_supported_context(Verification)

    handlers = [(evt.EVT_C_STORE, handle_store)]

    scp = ae.start_server((local_ip, local_port), evt_handlers=handlers, block=False)

    assoc = ae.associate(addr=pacs_ip, port=pacs_port, ae_title = pacs_ae)

    if assoc.is_established:
        responses = assoc.send_c_move(input_dataset, destination_ae, PatientRootQueryRetrieveInformationModelMove)
        for (status, identifier) in responses:
            if status:
                pass
            else:
                pass
        assoc.release()
    else:
        pass

    scp.shutdown()

    return complete_file_path_list

def missing_dicom_objects(modality, sop_uid, patient_id, input_date):

    from scu_move_git_v1 import handle_store as handle_store_mim_move, pacs_store, move

    """
    Writes to text file if any DICOM object is not in MIM; will check to make sure the object is not already written to text file

    Args:
        modality (string): Modality of the DICOM object that is not in MIM
        sop_uid (str): SOPInstanceUID of the DICON object that is not in MIM
        patient_id (str): patient_id of the DICOM object that is not in MIM

    Returns:
        patient_dictionary (dictionary): Dictionary containing the returned pydicom datasets from all of the MIM series and image level queries. Each patient could have multiple datasets resulting from the series level query
    """

    @retry(wait=wait_exponential(multiplier=1, exp_base=2), stop=stop_after_attempt(7))
    def aria_mim_move(dataset):
        try:
            moved_ds = move(dataset, handle_store_mim_move)
            return 0, moved_ds
        except RetryError:
            return 1, moved_ds
        
    today_date = input_date

    missing_rtplan_log = r''
    missing_rtstruct_log = r''
    missing_ct_log = r''

    if not os.path.exists(missing_rtplan_log): 
        with open(missing_rtplan_log, 'w') as f1: #create backup log if it does not exist and write the date
            f1.write(today_date + '\n')
    if not os.path.exists(missing_rtstruct_log): 
        with open(missing_rtstruct_log, 'w') as f1: #create backup log if it does not exist and write the date
            f1.write(today_date + '\n') 
    if not os.path.exists(missing_ct_log): 
        with open(missing_ct_log, 'w') as f1: #create backup log if it does not exist and write the date
            f1.write(today_date + '\n')

    rtplan_bool = False
    rtstruct_bool = False
    ct_bool = False

    if modality == "RTPLAN":

        ds = pydicom.Dataset()
        ds.QueryRetrieveLevel = 'IMAGE'
        ds.PatientID = patient_id
        ds.Modality = modality
        ds.SOPInstanceUID = sop_uid

        returned_val, moved_ds = aria_mim_move(ds)

        if moved_ds:
            update_string = f"The DICOM object {modality} with an SOPInstanceUID of {sop_uid} and a PatientID of {patient_id} has been successfully backed up to MIM \n"
        else:
            update_string = f"The DICOM object {modality} with an SOPInstanceUID of {sop_uid} and a PatientID of {patient_id} has FAILED to be backed up to MIM \n"

        with open(missing_rtplan_log, 'r') as f1:
            log_date = f1.readline().strip()
            missing_rtplans = list(f1.read().splitlines())
            f1.close()

        if log_date != today_date:
            with open(missing_rtplan_log, 'w') as f2: #clear the text file since it is a new day; and then write new data to file
                f2.write(today_date + '\n')
                f2.write(f"The DICOM object {modality} with an SOPInstanceUID of {sop_uid}, RTPLAN label of {moved_ds.RTPlanLabel}, and a PatientID of {patient_id} is not in MIM \n")
                f2.write(update_string)                
                f2.close()
        else:
            if missing_rtplans: #check if list has more than just the date
                for line in missing_rtplans:
                    if sop_uid in line:
                        rtplan_bool = True
                if not rtplan_bool:
                    with open(missing_rtplan_log, 'a') as f2: #open in append mode since the log date is equal to todays date
                        f2.write(f"The DICOM object {modality} with an SOPInstanceUID of {sop_uid}, RTPLAN label of {moved_ds.RTPlanLabel}, and a PatientID of {patient_id} is not in MIM \n")
                        f2.write(update_string)
                        f2.close()
            else: #list is just the date so no need to check
                with open(missing_rtplan_log, 'a') as f2:
                    f2.write(f"The DICOM object {modality} with an SOPInstanceUID of {sop_uid}, RTPLAN label of {moved_ds.RTPlanLabel}, and a PatientID of {patient_id} is not in MIM \n")
                    f2.write(update_string)
                    f2.close()

    if modality == "RTSTRUCT":

        ds = pydicom.Dataset()
        ds.QueryRetrieveLevel = 'IMAGE'
        ds.PatientID = patient_id
        ds.Modality = modality
        ds.SOPInstanceUID = sop_uid

        returned_val, moved_ds = aria_mim_move(ds)

        if moved_ds:
            update_string = f"The DICOM object {modality} with an SOPInstanceUID of {sop_uid} and a PatientID of {patient_id} has been successfully backed up to MIM \n"
        else:
            update_string = f"The DICOM object {modality} with an SOPInstanceUID of {sop_uid} and a PatientID of {patient_id} has FAILED to be backed up to MIM \n"

        with open(missing_rtstruct_log, 'r') as f1:
            log_date = f1.readline().strip()
            missing_rtstructs = list(f1.read().splitlines())
            f1.close()

        if log_date != today_date:
            with open(missing_rtstruct_log, 'w') as f2: #clear the text file since it is a new day; and then write new data to file
                f2.write(today_date + '\n')
                f2.write(f"The DICOM object {modality} with an SOPInstanceUID of {sop_uid} and a PatientID of {patient_id} is not in MIM \n")
                f2.write(update_string)
                f2.close()
        else:
            if missing_rtstructs: #check if list has more than just the date
                for line in missing_rtstructs:
                    if sop_uid in line:
                        rtstruct_bool = True
                if not rtstruct_bool:
                    with open(missing_rtstruct_log, 'a') as f2: #open in append mode since the log date is equal to todays date
                        f2.write(f"The DICOM object {modality} with an SOPInstanceUID of {sop_uid} and a PatientID of {patient_id} is not in MIM \n")
                        f2.write(update_string)
                        f2.close()
            else: #list is just the date so no need to check
                with open(missing_rtstruct_log, 'a') as f2:
                    f2.write(f"The DICOM object {modality} with an SOPInstanceUID of {sop_uid} and a PatientID of {patient_id} is not in MIM \n")
                    f2.write(update_string)
                    f2.close()

    if modality == "CT":

        with open(missing_ct_log, 'r') as f1:
            log_date = f1.readline().strip()
            missing_ct = list(f1.read().splitlines())
            f1.close()

        if log_date != today_date:
            with open(missing_ct_log, 'w') as f2: #clear the text file since it is a new day; and then write new data to file
                f2.write(today_date + '\n')
                f2.write(f"The DICOM object {modality} with an SOPInstanceUID of {sop_uid} and a PatientID of {patient_id} is not in MIM \n")
                # f2.write(update_string)
                f2.close()
        else:
            if missing_ct: #check if list has more than just the date
                for line in missing_ct:
                    if sop_uid in line:
                        ct_bool = True
                if not ct_bool:
                    with open(missing_ct_log, 'a') as f2: #open in append mode since the log date is equal to todays date
                        f2.write(f"The DICOM object {modality} with an SOPInstanceUID of {sop_uid} and a PatientID of {patient_id} is not in MIM \n")
                        # f2.write(update_string)
                        f2.close()
            else: #list is just the date so no need to check
                with open(missing_ct_log, 'a') as f2:
                    f2.write(f"The DICOM object {modality} with an SOPInstanceUID of {sop_uid} and a PatientID of {patient_id} is not in MIM \n")
                    # f2.write(update_string)
                    f2.close()

    try:
        return moved_ds
    except UnboundLocalError:
        return 0


def series_image_query(dictionary, object_key, local_ae_title, qr_addr, qr_port, qr_ae_title):

    """
    Queries MIM at the series and image level using the datasets returned from the study level query

    Args:
        dictionary (dictionary): Dictionary containing the datasets returned from the study level query to then query at the series level
        object_key (str): Key for the dictionary containing the datasets for the objects
        patient_dictionary (dictionary): Dictionary containing the patient datasets

    Returns:
        patient_dictionary (dictionary): Dictionary containing the returned pydicom datasets from all of the MIM series and image level queries. Each patient could have multiple datasets resulting from the series level query
    """

    missing_dicom_object_dict = {}

    def query_series_level(dictionary, object_key):

        """
        Queries MIM at the series level using the datasets returned from the study level query

        Args:
            dictionary (dictionary): Dictionary containing the datasets returned from the study level query to then query at the series level

        Returns:
            query_series_level_dict (dictionary): Dictionary containing the returned pydicom datasets from all of the MIM series level queries. Each patient could have multiple datasets resulting from the series level query
        """

        def series_level_query_processing(input_dataset, prev_dict, iteration):

            """
            Queries MIM using at the SERIES level and checks if the query was successful

            Args:
                input_dataset (pydicom dataset): dataset containing the PatientID, StudyInstanceUID, Modality to query for
                prev_dict (dictionary): previous returned dictionary from the query; used to ensure we do not have duplicates
                iteration (int): iteration that we are on

            Returns:
                query_series_level_dict (dictionary): dictionary containing all the results at the series level
                prev_dict (dictionary): update dictionary containing the last successful query
                iteration (int): updated iteration
            """            

            ds = pydicom.Dataset()
            ds.QueryRetrieveLevel = 'SERIES'
            ds.PatientID = str(input_dataset.PatientID)
            ds.StudyInstanceUID = str(input_dataset.StudyInstanceUID)
            ds.SeriesInstanceUID = ''
            ds.Modality = input_dataset.Modality
            
            id_key = str("iteration_" + str(iteration) + "_key") #create a unique key for each returned dataset from the series level query, 
            
            returned_dict = mim_query_function(ds, key1, local_ae_title, qr_addr, qr_port, qr_ae_title)
            
            if bool(returned_dict) == True:
                if returned_dict != prev_dict: #make sure we do not receive the same dictionary twice from the query
                    query_series_level_dict[key1][id_key] = returned_dict
                    iteration += 1
                    prev_dict = returned_dict
            else:
                pass

            return query_series_level_dict, prev_dict, iteration
                        
        query_series_level_dict = {}

        dictionary_copy = copy.deepcopy(dictionary)
        
        for key1 in dictionary_copy.keys():
            if object_key in list(dictionary_copy[key1].keys()):
                query_series_level_dict[key1] = {}
                missing_dicom_object_dict[key1] = []
                iteration = 0
                prev_dict = {}

                query_series_level_dict, prev_dict, iteration = series_level_query_processing(dictionary[key1][object_key], prev_dict, iteration)

                if bool(query_series_level_dict[key1]) == False:
                    del query_series_level_dict[key1]
                    missing_dicom_object_dict[key1].append([dictionary[key1][object_key].Modality, dictionary[key1][object_key].SOPInstanceUID, dictionary[key1][object_key].PatientID])
                    del dictionary[key1][object_key] #delete as the series instance uid is not there
                
        return query_series_level_dict, dictionary


    def query_object_image_level(object_series_dictionary, patient_dictionary, object_key):

        """
        Queries at the image level for the rtstruct objects

        Args:
            object_series_dictionary (dictionary): Dictionary containing the results from the series level query for the rtplan objects
            patient_dictionary (dictionary): Dictionary containing the RTRECORD SOPInstanceUID for each dataset

        Returns:
            query_image_level_dict (dictionary): Dictionary containing the rtstruct objects returned from the image level query
        """

        def image_level_query_processing(input_dataset, prev_dict, iteration, object_sop_uid, bool_value):

            """
            Function to perform the image level query and handle the return value

            Args:
                input_dataset (dataset): Dataset to be queried at the image level
                prev_dict (dictionary): Previous returned dictionary from the query
                iteration (int): Iteration that we are on to generate unique identifier
                rtstruct_sop_uid: SOPInstanceUID of the rtstruct that we are querying for

            Returns:
                prev_dict (dictionary): If we use the returned dictionary; prev dict becomes this value
                iteration (int): Iteration incerased by 1
                
            """

            ds = pydicom.Dataset()
            ds.QueryRetrieveLevel = 'IMAGE'
            ds.PatientID = str(input_dataset.PatientID)
            ds.StudyInstanceUID = str(input_dataset.StudyInstanceUID)
            ds.SeriesInstanceUID = str(input_dataset.SeriesInstanceUID)
            ds.SOPInstanceUID = str(object_sop_uid)
            ds.Modality = input_dataset.Modality

            id_key = str("iteration_" + str(iteration) + "_key")
            
            returned_dict = mim_query_function(ds, key1, local_ae_title, qr_addr, qr_port, qr_ae_title)

            if bool(returned_dict) == True:
                
                patient_dictionary[key1][object_key] = ds #update rtplan dataset with correct parameters
                bool_value=True
            else:
                pass

            return prev_dict, iteration, bool_value
        
        for key1 in object_series_dictionary.keys():
            iteration = 0
            prev_dict = {}
            object_sop_uid = patient_dictionary[key1][object_key].SOPInstanceUID
            boolean=False 
            if isinstance(object_series_dictionary[key1], dict):
                for key2 in object_series_dictionary[key1].keys():
                    if isinstance(object_series_dictionary[key1][key2], dict):
                        for key3 in object_series_dictionary[key1][key2].keys():
                            pt_id = object_series_dictionary[key1][key2][key3].PatientID
                            modality = object_series_dictionary[key1][key2][key3].Modality
                            prev_dict, iteration, boolean = image_level_query_processing(object_series_dictionary[key1][key2][key3], prev_dict, iteration, object_sop_uid, boolean)
                    else:
                        pt_id = object_series_dictionary[key1][key2].PatientID
                        prev_dict, iteration, boolean = image_level_query_processing(object_series_dictionary[key1][key2], prev_dict, iteration, object_sop_uid, boolean)
            else:
                pt_id = object_series_dictionary[key1].PatientID
                prev_dict, iteration, boolean = image_level_query_processing(object_series_dictionary[key1], prev_dict, iteration, object_sop_uid, boolean)

            if boolean==False:
                missing_dicom_object_dict[key1].append([modality, object_sop_uid, pt_id])

            if not missing_dicom_object_dict[key1]: del missing_dicom_object_dict[key1]

        return patient_dictionary

    patient_dict_series_level, revised_dictionary = query_series_level(dictionary, object_key)
    return query_object_image_level(patient_dict_series_level, revised_dictionary, object_key), missing_dicom_object_dict


def query_mim_object_uid(patient_dict, object1_key, object2_key, local_ae_title, qr_addr, qr_port, qr_ae_title):

    """
    Creates the dataset to query a specific DICOM Object in MIM using its SOPInstanceUID obtained from a second DICOM object

    Args:
        patient_dict (dictionary): Dictionary containing the SOPInstanceUID for the RTRECORD for that patient
        local_ae_title (str): AE Title of the local computer
        qr_ip (str): String of the IP Address of the PACs we will query from
        qr_port (int): Port of the PACs we will associate with to query from
        qr_ae_title (str): AE Title of the PACs we will query from
        rtplan_key (str): Key in the dictionary to obtain the rtplan dataset
        rtstruct_key (str): Key for the dictionary to store the rtstruct dataset

    Returns:
        patient_dict (dictionary): Updated dictionary containing the rtstruct dataset
    """

    patient_dict_copy = copy.deepcopy(patient_dict)

    for key1 in patient_dict_copy.keys():

        if object1_key in list(patient_dict[key1].keys()):

            #Querying the RTPLAN for the referenced structure set sequence
            try:
                ds = pydicom.Dataset()
                ds.QueryRetrieveLevel = 'IMAGE'
                ds.SOPInstanceUID = patient_dict[key1][object1_key].SOPInstanceUID
                ds.StudyInstanceUID = patient_dict[key1][object1_key].StudyInstanceUID
                ds.PatientID = patient_dict[key1][object1_key].PatientID
                ds.SeriesInstanceUID = patient_dict[key1][object1_key].SeriesInstanceUID
                ds.ReferencedStructureSetSequence = [pydicom.Dataset()]
                ds.ReferencedStructureSetSequence[0].ReferencedSOPInstanceUID = ''

                object1_dict = mim_query_function(ds, key1, local_ae_title, qr_addr, qr_port, qr_ae_title)
            except AttributeError:
                print("Attribute Error returned when querying the RTPLAN for the Referenced Structure Set Sequence")
                print(f"Here are the details. PatientID: {patient_dict[key1][object1_key].PatientID}. SOPInstanceUID: {patient_dict[key1][object1_key].SOPInstanceUID}")

            for plan_key in object1_dict.keys():
                object1_dataset = object1_dict[plan_key]

                try:
                    object2_dataset = pydicom.Dataset()
                    object2_dataset.PatientID = object1_dataset.PatientID
                    object2_dataset.StudyInstanceUID = object1_dataset.StudyInstanceUID
                    object2_dataset.Modality = "RTSTRUCT"
                    object2_dataset.SOPInstanceUID = object1_dataset.ReferencedStructureSetSequence[0].ReferencedSOPInstanceUID

                    patient_dict[key1][object2_key] = object2_dataset
                except IndexError:
                    del patient_dict[key1] #delete this for now as there is no rtstruct to be referenced downstream for this patient

    return patient_dict


def add_rtrecord_uids(patient_dict, rtrecord_uids, rtrecord_key):

    """
    Adds the RTRecord UID for each patient to the patient dictionary

    Args:
        patient_dict (dictionary): Dictionary containing an entry for each patient
        rtrecord_uids (set): set containing the rtrecord_uids

    Returns:
        patient_dict (dictionary): updated patient dictionary containing the RTRecord UID for each patient
    """
    patient_dict_key_list = list(patient_dict.keys())
    for key, uid in zip(patient_dict_key_list, rtrecord_uids):
        patient_dict[key][rtrecord_key] = uid

    return patient_dict

def query_aria_rtplan_uid(patient_dict, local_ae_title, qr_ip, qr_port, qr_ae_title, rtrecord_key, rtplan_key):

    """
    Creates the dataset to query a specific RTRECORD in ARIA for the associated RTPlan

    Args:
        patient_dict (dictionary): Dictionary containing the SOPInstanceUID for the RTRECORD for that patient
        local_ae_title (str): AE Title of the local computer
        qr_ip (str): String of the IP Address of the PACs we will query from
        qr_port (int): Port of the PACs we will associate with to query from
        qr_ae_title (str): AE Title of the PACs we will query from
        rtrecord_key (str): Key in the dictionary to obtain the rtrecord SOPInstanceUID for a specific patient
        rtplan_key (str): Key for the dictionary to store the rtplan SOPInstanceUID

    Returns:
        patient_dict (dictionary): Updated dictionary containing the rtplan SOPInstanceUID
    """

    for key1 in patient_dict.keys():
        rtrecord_uid = patient_dict[key1][rtrecord_key]

        ds = pydicom.Dataset()
        ds.QueryRetrieveLevel = 'IMAGE'
        ds.Modality = 'RTRECORD'
        ds.SOPClassUID = RTBeamsTreatmentRecordStorage
        ds.SOPInstanceUID = rtrecord_uid
        ds.StudyInstanceUID = ''
        ds.PatientID = ''
        ds.ReferencedRTPlanSequence = [pydicom.Dataset()]
        ds.ReferencedRTPlanSequence[0].ReferencedSOPInstanceUID = ''

        rtrecord_dataset = aria_query_function(ds, local_ae_title, qr_ip, qr_port, qr_ae_title)

        rtplan_dataset = pydicom.Dataset()
        rtplan_dataset.PatientID = rtrecord_dataset.PatientID
        rtplan_dataset.StudyInstanceUID = rtrecord_dataset.StudyInstanceUID
        rtplan_dataset.Modality = "RTPLAN"
        rtplan_dataset.SOPInstanceUID = rtrecord_dataset.ReferencedRTPlanSequence[0].ReferencedSOPInstanceUID

        patient_dict[key1][rtplan_key] = rtplan_dataset


    return patient_dict

def rtplan_query(patient_dictionary, rtplan_key, local_ae_title, qr_addr, qr_port, qr_ae_title):
    return series_image_query(patient_dictionary, rtplan_key, local_ae_title, qr_addr, qr_port, qr_ae_title)


def rtrecord_move(patient_dictionary, missing_dicom_objects_dict, rtrecord_key, rtrecord_move_path, local_ip, local_port, local_ae, pacs_ip, pacs_port, pacs_ae, destination_ae):

    moved_rtrecord_key = 'moved_rtrecords'

    for key1 in missing_dicom_objects_dict.keys():
        ds = pydicom.Dataset()
        ds.QueryRetrieveLevel = 'IMAGE'
        ds.Modality = 'RTRECORD'
        ds.SOPInstanceUID = patient_dictionary[key1][rtrecord_key]
        returned_rtrecord_list = c_move_service(ds, 'RTRECORD', rtrecord_move_path, local_ip, local_port, local_ae, pacs_ip, pacs_port, pacs_ae, destination_ae)

        patient_dictionary[key1][moved_rtrecord_key] = returned_rtrecord_list

    return patient_dictionary, moved_rtrecord_key

def rtrecord_processing(patient_dictionary, missing_dicom_objects_dict, moved_rtrecord_key, rtplan_key, input_date):

    for key1 in missing_dicom_objects_dict.keys():
        for rtrecord_path in patient_dictionary[key1][moved_rtrecord_key]:
            rtrecord = pydicom.dcmread(rtrecord_path)
            machine_name = rtrecord.TreatmentMachineSequence[0].TreatmentMachineName

            if (machine_name == 'ViewRay' or machine_name == 'TomoTherapy'):
                pass
            else:
                for entry in missing_dicom_objects_dict[key1]:
                    rtplan_ds = missing_dicom_objects(entry[0], entry[1], entry[2], input_date)
                    if rtplan_ds:
                        patient_dictionary[key1][rtplan_key] = rtplan_ds
                    else:
                        del patient_dictionary[key1] #remove entry from dictionary as nothing downstream of the plan will be accurate -> move of the plan from ARIA to MIM failed and has been written to log

            #remember to remove file
            os.remove(rtrecord_path)

    return patient_dictionary

        

def rtstruct_query(patient_dictionary, rtstruct_key, local_ae_title, qr_addr, qr_port, qr_ae_title):
    return series_image_query(patient_dictionary, rtstruct_key, local_ae_title, qr_addr, qr_port, qr_ae_title)

def update_missing_object_log(missing_dicom_objects_dict, patient_dictionary, object_key, input_date):
    missing_dicom_objects_dict_copy = copy.deepcopy(missing_dicom_objects_dict)
    for key1 in missing_dicom_objects_dict_copy.keys():
        for entry in missing_dicom_objects_dict[key1]:
            _ = missing_dicom_objects(entry[0], entry[1], entry[2], input_date) #do not return anything for rtstruct
        del missing_dicom_objects_dict[key1] #delete as we have handled
        del patient_dictionary[key1][object_key] #delete as the object for this (partial) dataset does not exist
    return missing_dicom_objects_dict, patient_dictionary


def rtstruct_move_and_processing(patient_dictionary, rtstruct_key, ct_key, temp_move_path, local_ip, local_port, local_ae, pacs_ip, pacs_port, pacs_ae, destination_ae):

    def rtstruct_move(input_dictionary, rtstruct_key, rtstruct_move_path):

        """
        Function to take the rtstruct metadata returned from the functions above and move them to the local computer

        Args:
            input_dictionary (dictionary): Dictionary containing the queried rtstructs
            rtstruct_move_path (string): Desired path where the c-moved objects will be moved to disk

        Returns:
            rtstruct_saved_path_dict (dictionary): Dictionary containing a list of the locations on disk where the objects have been saved
        """

        rtstruct_saved_path_dict = {}

        for key1 in input_dictionary.keys():
            rtstruct_saved_path_dict[key1] = {}

            ds = pydicom.Dataset()
            ds.QueryRetrieveLevel = 'IMAGE'
            ds.PatientID = str(input_dictionary[key1][rtstruct_key].PatientID)
            ds.StudyInstanceUID = str(input_dictionary[key1][rtstruct_key].StudyInstanceUID)
            ds.SeriesInstanceUID = str(input_dictionary[key1][rtstruct_key].SeriesInstanceUID)
            ds.SOPInstanceUID = str(input_dictionary[key1][rtstruct_key].SOPInstanceUID)
            returned_rtstruct_list = c_move_service(ds, str('RTSTRUCT'), rtstruct_move_path, local_ip, local_port, local_ae, pacs_ip, pacs_port, pacs_ae, destination_ae)
            if returned_rtstruct_list: 
                rtstruct_saved_path_dict[key1][rtstruct_key] = returned_rtstruct_list
            else:
                print(f"Failed transfer for {key1}, {ds.PatientID}")
            if bool(rtstruct_saved_path_dict[key1]) == False: 
                del rtstruct_saved_path_dict[key1]

        return rtstruct_saved_path_dict
    
    def rtstruct_processing(input_dict, ct_key):

        """
        Function to find the series instance uid of the associated CT sims from the RTStruct

        Args:
            input_dict (dictionary): Dictionary containing the paths of the rtstructs saved to disk

        Returns:
            ct_series_dict (dictionary): Dictionary containing the associated ct sim from the rtstruct objects
        """
        
        ct_series_dict = {}

        for key1 in input_dict.keys():
            ds = pydicom.Dataset()
            ct_series_dict[key1] = {}
            for key2 in input_dict[key1].keys():
                ct_series_dict[key1][key2] = {}
                for key3 in input_dict[key1][key2].keys():
                    for rtstruct_path in input_dict[key1][key2][key3]:
                        rtstruct = pydicom.dcmread(rtstruct_path)
                        ds.PatientID = rtstruct.PatientID
                        ds.StudyInstanceUID = rtstruct.StudyInstanceUID
                        ds.SeriesInstanceUID = rtstruct.ReferencedFrameOfReferenceSequence[0].RTReferencedStudySequence[0].RTReferencedSeriesSequence[0].SeriesInstanceUID
                        ct_series_dict[key1][key2][key3] = ds
                if bool(ct_series_dict[key1][key2]) == False: del ct_series_dict[key1][key2]
            if bool(ct_series_dict[key1]) == False: del ct_series_dict[key1]

        return ct_series_dict
    
    rtstruct_saved_path_dict = rtstruct_move(patient_dictionary, rtstruct_key, temp_move_path)
    pass

def main():
    rtrecord_uid_set = {'', '', '', '', '', ''}
    rtrecord_key = 'rtrecord_uid'
    rtplan_key = 'rtplan_dataset'
    rtstruct_key = 'rtstruct_dataset'
    ct_key = 'ct_dataset'
    temp_move_path = r''

    raw_patient_dict = create_patient_dictionary(len(rtrecord_uid_set))
    patient_dict = add_rtrecord_uids(raw_patient_dict, rtrecord_uid_set, rtrecord_key)
    patient_dict = query_aria_rtplan_uid(patient_dict, SCU['AETitle'], QR_SCP['IP'], QR_SCP['Port'], QR_SCP['AETitle'], rtrecord_key, rtplan_key)
    patient_dict, missing_dicom_object_dict = rtplan_query(patient_dict, rtplan_key, SCU['AETitle'], PACS_QUERY_SCP['IP'], PACS_QUERY_SCP['Port'], PACS_QUERY_SCP['AETitle'])
    patient_dict, moved_rtrecord_key = rtrecord_move(patient_dict, missing_dicom_object_dict, rtrecord_key, temp_move_path, LOCAL_STORE_SCP['IP'], LOCAL_STORE_SCP['Port'], LOCAL_STORE_SCP['AETitle'], QR_SCP['IP'], QR_SCP['Port'], QR_SCP['AETitle'], SCU['AETitle'])
    patient_dict = rtrecord_processing(patient_dict, missing_dicom_object_dict, moved_rtrecord_key, rtplan_key, '20241107')
    patient_dict = query_mim_object_uid(patient_dict, rtplan_key, rtstruct_key, SCU['AETitle'], PACS_QUERY_SCP['IP'], PACS_QUERY_SCP['Port'], PACS_QUERY_SCP['AETitle']) #patient_dict now contains partial dataset containing the metadata for the rtstruct
    patient_dict, missing_dicom_object_dict = rtstruct_query(patient_dict, rtstruct_key, SCU['AETitle'], PACS_QUERY_SCP['IP'], PACS_QUERY_SCP['Port'], PACS_QUERY_SCP['AETitle'])
    missing_dicom_object_dict, patient_dict = update_missing_object_log(missing_dicom_object_dict, patient_dict, rtstruct_key, '20241107')
    pass


def run_with_scu_move(rtrecord_uid_set, rtrecord_key, rtplan_key, rtstruct_key, ct_key, temp_move_path, used_date):
    raw_patient_dict = create_patient_dictionary(len(rtrecord_uid_set))
    patient_dict = add_rtrecord_uids(raw_patient_dict, rtrecord_uid_set, rtrecord_key)
    patient_dict = query_aria_rtplan_uid(patient_dict, SCU['AETitle'], QR_SCP['IP'], QR_SCP['Port'], QR_SCP['AETitle'], rtrecord_key, rtplan_key)
    patient_dict, missing_dicom_object_dict = rtplan_query(patient_dict, rtplan_key, SCU['AETitle'], PACS_QUERY_SCP['IP'], PACS_QUERY_SCP['Port'], PACS_QUERY_SCP['AETitle'])
    patient_dict, moved_rtrecord_key = rtrecord_move(patient_dict, missing_dicom_object_dict, rtrecord_key, temp_move_path, LOCAL_STORE_SCP['IP'], LOCAL_STORE_SCP['Port'], LOCAL_STORE_SCP['AETitle'], QR_SCP['IP'], QR_SCP['Port'], QR_SCP['AETitle'], SCU['AETitle'])
    patient_dict = rtrecord_processing(patient_dict, missing_dicom_object_dict, moved_rtrecord_key, rtplan_key, used_date)
    patient_dict = query_mim_object_uid(patient_dict, rtplan_key, rtstruct_key, SCU['AETitle'], PACS_QUERY_SCP['IP'], PACS_QUERY_SCP['Port'], PACS_QUERY_SCP['AETitle']) #patient_dict now contains partial dataset containing the metadata for the rtstruct
    patient_dict, missing_dicom_object_dict = rtstruct_query(patient_dict, rtstruct_key, SCU['AETitle'], PACS_QUERY_SCP['IP'], PACS_QUERY_SCP['Port'], PACS_QUERY_SCP['AETitle'])
    missing_dicom_object_dict, patient_dict = update_missing_object_log(missing_dicom_object_dict, patient_dict, rtstruct_key, used_date)


if __name__ == "__main__":
    main()