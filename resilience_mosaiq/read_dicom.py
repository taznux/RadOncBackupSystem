import pydicom
import sys

# Check if an argument was provided
if len(sys.argv) < 2:
    print("Usage: python read_dicom.py <input_file>")
    sys.exit(1)

# Get the first argument (index 1, because index 0 is the script name)
file_name = sys.argv[1]

# Try to open and read the file
try:
    with open(file_name, 'r') as file:
        contents = file.read()
        print("File contents:\n")
        print(contents)
except FileNotFoundError:
    print(f"File '{file_name}' not found.")
except Exception as e:
    print(f"An error occurred: {e}")

# Step 1: Load the RTRECORD DICOM file
ds = pydicom.dcmread(file_name)

# Step 2: Check basic patient and modality info
print("Patient:", ds.PatientName)
print("Modality:", ds.Modality)  # Should be "RTRECORD"
print("\n")

# Optional: View structure of the file
print(ds)  # Full dump

# Setup Notes
print(f"\nBelow are the information from private tags:")
block = ds.private_block(0x3261, 'TJU RadOnc Customized Data')
elem1 = block[(0x01)]
elem2 = block[(0x02)]
elem3 = block[(0x03)]
elem4 = block[(0x04)]
print(f"Site Name:  {elem1.value}")
print(f"Setup Notes:  {elem2.value}")
print(f"Activity:  {elem3.value}")
print(f"Plan_label:  {elem4.value}")
