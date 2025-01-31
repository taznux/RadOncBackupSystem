# Installation Guide
1. Clone respository to your machine and change to the cloned respository directory
2. Initialize your python environment
3. Install Python packages with
'pip install -r requirements.txt'

# Updating AETitles, IP Addresses, and Ports
1. Open the config_git_v1.toml file
    a. Update the AETitles, IP Addresses, and Ports
    b. The ports should always be integers, not strings
    c. "[local]" referes to your machine, "[rvs]" refers to the Record and Verify System, "[mim_server]" refers to the RTPACS server,   and "[mim_server_qr]" refers to the RTPACS query information
2. Logger configurations should be updated in logging_git_v1.toml. Ensure the change the SMTP email handler
3. Adjust the AETitles, IP Addresses, and Ports in scu_move_support_git_v1.python
    a. In the functions main() and run_with_scu_move(), the AE_Titles, Ports and IP Addresses in the functions have been removed and need to be updated. RVS refers to Record and Verify System. All Ports must be entered as integers

# Backup System
- Purpose: To transfer RTRecord Objects from a Record and Verify System (RVS) to a RTPACS.
- Function 1: Backup program queries RVS for all RTRecords within a given time interval and generate a list of their UIDs. These UIDs are compared against a log file 'logs/daily_backup.log' which contain the UIDs of RTRecords successfully backed-up to the RTPacs. If the backup is successfull, the log 'daily_backup.log' will the UIDs of the backed-up RTRecords. The backup will retry seven different times if it initially fails, and will be added to 'logs/daily_failures.log'
- Function 2: Backup treatment plan information (RTPlan, RTStruct, CT) corresponding to each RTRecord from the RVS to the RTPACS. Running totals of each RTPlan, RTStruct, CT are updated in log files.
- The main backup program is 'scu_move_git_v1.py'. This script calls 'scu_find_git_v1.py' to query the RVS, and calls 'scu_move_support_git_v1.py' to backup treatment plan information corresponding to the RTRecords.

#Generate Treatment Report
1. Run 'get_treatment_report_git_v1.py' (make sure to adjust the study_start_date, treatment_start_date, and end_date variables before running)
2. Purpose: To generate a report of all patients currently undergoing RT Treatments including their current fraction number using back up records from MIM in case ARIA is unavailable
3. 'treatment_start_date' and 'end_date' specifies the date range of what the user considers to be a "patient currently undergoing treatment" to be included in the report (e.g. received a fraction within the past 7 days)
4. 'study_start_date' is when the study was first created. Since MIM queries in a hierarchial fashion from Study->Series->Image, 'study_start_date' should be set to long before the 'treatment_start_date' to ensure no patients are erroneously overlooked (recommended at least 1 month prior). However, setting 'study_start_date' too far into the past increases the number of patient cases MIM has to search through, which significantly increases the workload on the system.

#Setting Up Windows Task Scheduler
1. In 'Task Scheduler', click 'Create Task'
2. In 'Trigger' tab, click 'New' and set task to repeat 10 minutes, indefinitely
3. In 'Actions' tab, clicl 'New'
4. In the 'Program Script' field, provide the absolute path to 'python.exe'. Note: this could be in the conda environment
5. In the 'Add arguments (optional)' field, provide the absolute path to 'scu_move_git_v1.py'
6. In the 'Start in (optional)' field, provide the absolute path to this project directory



