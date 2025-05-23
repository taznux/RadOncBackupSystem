from . import BackupSystem
import requests

class Orthanc(BackupSystem):
    def store(self, data):
        url = "http://localhost:8042/instances"
        headers = {"Content-Type": "application/dicom"}
        response = requests.post(url, headers=headers, data=data)
        if response.status_code == 200:
            print("Data stored successfully in Orthanc")
        else:
            print("Failed to store data in Orthanc")

    def verify(self, data):
        url = "http://localhost:8042/instances"
        headers = {"Content-Type": "application/dicom"}
        response = requests.post(url, headers=headers, data=data)
        if response.status_code == 200:
            print("Data verified successfully in Orthanc")
        else:
            print("Failed to verify data in Orthanc")
