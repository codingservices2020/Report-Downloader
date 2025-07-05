from googleapiclient.discovery import build
from google.oauth2 import service_account

# Load credentials from JSON file (Replace with your own file path)
SERVICE_ACCOUNT_FILE = "service_account.json"

# Define the scope
SCOPES = ["https://www.googleapis.com/auth/drive"]

# Authenticate and create the Drive API service
credentials = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
drive_service = build("drive", "v3", credentials=credentials)

def list_drive_files():
    """Lists all files in Google Drive."""
    results = drive_service.files().list().execute()
    files = results.get("files", [])

    if not files:
        print("No files found.")
    else:
        print("📂 Google Drive Files:")
        for file in files:
            print(f"📄 {file['name']} (ID: {file['id']})")

def delete_file_by_id(file_id):
    """Deletes a single file by its ID."""
    try:
        drive_service.files().delete(fileId=file_id).execute()
        print(f"✅ File with ID {file_id} has been deleted.")
    except Exception as e:
        print(f"❌ Error deleting file with ID {file_id}: {e}")

def delete_all_files():
    """Deletes all files in Google Drive."""
    results = drive_service.files().list().execute()
    files = results.get("files", [])

    if not files:
        print("No files found to delete.")
    else:
        for file in files:
            try:
                drive_service.files().delete(fileId=file['id']).execute()
                print(f"✅ Deleted file: {file['name']}")
            except Exception as e:
                print(f"❌ Error deleting file {file['name']}: {e}")

# Example usage of delete_file_by_id
# Uncomment the line below and replace 'your_file_id' with the actual file ID to delete a specific file
# delete_file_by_id('189zbqJc9_FOrevX44UuJQzg5BtqVp0bM')

# Example usage of delete_all_files
# Uncomment the line below to delete all files in Google Drive
# delete_all_files()


# Call the functions
print("📂 Listing Google Drive Files:")
list_drive_files()