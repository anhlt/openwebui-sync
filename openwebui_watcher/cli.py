import os
import time
import requests
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileModifiedEvent
from dotenv import load_dotenv

# Setup logging to /tmp/
log_path = "/tmp/openwebui_watcher.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("openwebui_watcher")

load_dotenv()

API_KEY = os.getenv("OPENWEBUI_API_KEY")
API_URL = os.getenv("OPENWEBUI_API_URL")
KNOWLEDGE_ID = os.getenv("OPENWEBUI_KNOWLEDGE_ID")
ALLOWED_FILE_EXTENSIONS = os.getenv("ALLOWED_FILE_EXTENSIONS", "")

assert API_KEY and API_URL and KNOWLEDGE_ID, "Set OPENWEBUI_API_KEY, OPENWEBUI_API_URL, OPENWEBUI_KNOWLEDGE_ID in env"

# Parse allowed extensions
if ALLOWED_FILE_EXTENSIONS:
    ALLOWED_EXTS = set(ext.strip().lower() for ext in ALLOWED_FILE_EXTENSIONS.split(",") if ext.strip())
    logger.info(f"Will upload only files with these extensions: {sorted(ALLOWED_EXTS)}")
else:
    ALLOWED_EXTS = None
    logger.info("No ALLOWED_FILE_EXTENSIONS set, will upload all file types.")

UPLOAD_ENDPOINT = f"{API_URL}/api/v1/files/"
ADD_FILE_TO_KNOWLEDGE_ENDPOINT = f"{API_URL}/api/v1/knowledge/{KNOWLEDGE_ID}/file/add"
REMOVE_FILE_FROM_KNOWLEDGE_ENDPOINT = f"{API_URL}/api/v1/knowledge/{KNOWLEDGE_ID}/file/remove"
GET_KNOWLEDGE_FILES_ENDPOINT = f"{API_URL}/api/v1/knowledge/{KNOWLEDGE_ID}"

CURL_HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {API_KEY}"
}

# Map: filename -> file_id
filename_to_fileid = {}

def fetch_knowledge_files():
    logger.info("Fetching file list from knowledge base.")
    try:
        resp = requests.get(GET_KNOWLEDGE_FILES_ENDPOINT, headers=CURL_HEADERS)
        resp.raise_for_status()
        data = resp.json()
        # The detailed file list is often in data['files']
        files = data.get('files', [])
        for f in files:
            filename_to_fileid[f['filename']] = f['id']
        logger.info(f"Found {len(files)} files in knowledge base.")
    except Exception as e:
        logger.error(f"Failed to fetch knowledge files: {e}")

def remove_file_from_knowledge(file_id):
    logger.info(f"Removing file from knowledge base: {file_id}")
    data = {"file_id": file_id}
    try:
        resp = requests.post(REMOVE_FILE_FROM_KNOWLEDGE_ENDPOINT, headers=CURL_HEADERS, json=data)
        logger.info(f"Remove response code: {resp.status_code}, content: {resp.text}")
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to remove file {file_id}: {e}")



def print_curl_upload(file_path):
    url = UPLOAD_ENDPOINT
    token = API_KEY
    filename = os.path.basename(file_path)
    curl_command = (
        f"curl '{url}' "
        f"-X POST "
        f"-H 'Authorization: Bearer {token}' "
        f"-H 'Accept: application/json' "
        f"-F 'file=@{file_path};filename={filename}'"
    )
    logger.info("Equivalent curl command:\n" + curl_command)

def upload_file(file_path):

    print_curl_upload(file_path)

    # Mimic: -F 'file=@raft.pdf'
    with open(file_path, "rb") as f:
        files = {'file': f}
        resp = requests.post(UPLOAD_ENDPOINT, headers=CURL_HEADERS , files=files)


    logger.info("add_file_to_knowledge: Preparing request")
    logger.info(f"URL: {ADD_FILE_TO_KNOWLEDGE_ENDPOINT}")
    logger.info(f"Headers: {CURL_HEADERS}")
    logger.info(f"files: {files}")
    logger.info(f"Response status code: {resp.status_code}")
    logger.info(f"Response headers: {dict(resp.headers)}")
    logger.info(f"Response content: {resp.text}")

    resp.raise_for_status()
    resp_json = resp.json()

    # The curl response would have a file id - get it
    file_id = resp_json.get("id") or resp_json.get("data", {}).get("id")
    if not file_id:
        logger.error(f"Failed to get file id from: {resp_json}")
        return None
    logger.info(f"Uploaded {file_path} as id {file_id}")
    return file_id

def add_file_to_knowledge(file_id):
    data = {"file_id": file_id}
    logger.info("add_file_to_knowledge: Preparing request")
    logger.info(f"URL: {ADD_FILE_TO_KNOWLEDGE_ENDPOINT}")
    logger.info(f"Headers: {CURL_HEADERS}")
    logger.info(f"JSON body: {data}")

    resp = requests.post(ADD_FILE_TO_KNOWLEDGE_ENDPOINT, headers=CURL_HEADERS, json=data)

    # Log response info
    logger.info(f"Response status code: {resp.status_code}")
    logger.info(f"Response headers: {dict(resp.headers)}")
    logger.info(f"Response content: {resp.text}")


    logger.info(repr)
    resp.raise_for_status()
    logger.info(f"Added file {file_id} to knowledge {KNOWLEDGE_ID}")
    return resp.json()

def has_allowed_extension(file_path):
    if ALLOWED_EXTS is None:
        return True
    ext = os.path.splitext(file_path)[1][1:].lower()
    return ext in ALLOWED_EXTS


def remove_file_from_knowledge(file_id):
    logger.info(f"Removing file from knowledge base: {file_id}")
    data = {"file_id": file_id}
    try:
        resp = requests.post(REMOVE_FILE_FROM_KNOWLEDGE_ENDPOINT, headers=CURL_HEADERS, json=data)
        logger.info(f"Remove-from-knowledge response code: {resp.status_code}, content: {resp.text}")
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to remove file {file_id} from knowledge: {e}")

def delete_file_from_system(file_id):
    url = f"{API_URL}/api/v1/files/{file_id}"
    try:
        resp = requests.delete(url, headers=CURL_HEADERS)
        logger.info(f"Delete-file response code: {resp.status_code}, content: {resp.text}")
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to delete file {file_id} from system: {e}")


class FileChangeHandler(FileSystemEventHandler):
    def on_any_event(self, event):
        if event.is_directory:
            return
        if isinstance(event, (FileModifiedEvent, FileCreatedEvent)):
            file_path = event.src_path
            if not has_allowed_extension(file_path):
                logger.debug(f"Skipping {file_path} due to extension not in ALLOWED_FILE_EXTENSIONS")
                return
            logger.info(f"Detected change: {file_path}")
            try:
                file_id = upload_file(file_path)
                if file_id:
                    add_file_to_knowledge(file_id)
            except Exception as e:
                logger.error(f"Failed to handle {file_path}: {e}")

def scan_and_upload_changes(scan_dir, interval=60):
    now = time.time()
    cutoff = now - interval

    for root, dirs, files in os.walk(scan_dir):
        for fname in files:
            file_path = os.path.join(root, fname)
            if not has_allowed_extension(file_path):
                continue
            try:
                mtime = os.path.getmtime(file_path)
            except Exception as e:
                logger.error(f"Error getting mtime for {file_path}: {e}")
                continue
            if mtime >= cutoff:
                logger.info(f"File changed in last {interval} seconds: {file_path}")
                try:
                    # --- Duplicate check and removal ---
                    base_name = os.path.basename(file_path)
                    if base_name in filename_to_fileid:
                        logger.info(f"Duplicate found, removing old file for: {base_name}")
                        remove_file_from_knowledge(filename_to_fileid[base_name])
                        delete_file_from_system(filename_to_fileid[base_name])
                        del filename_to_fileid[base_name]  # Remove old mapping

                    # --- Upload and update mapping ---
                    file_id = upload_file(file_path)
                    if file_id:
                        add_file_to_knowledge(file_id)
                        filename_to_fileid[base_name] = file_id
                except Exception as e:
                    logger.error(f"Failed to upload/sync {file_path}: {e}")

def main():
    watch_dir = "."
    logger.info(f"Begin looped watch on {os.path.abspath(watch_dir)}")
    while True:
        scan_and_upload_changes(watch_dir, interval=6)
        time.sleep(3)

# def main():
#     path = "."
#     event_handler = FileChangeHandler()
#     observer = Observer()
#     observer.schedule(event_handler, path, recursive=True)
#     observer.start()
#     logger.info(f"Watching {os.path.abspath(path)} for changes...")
#     try:
#         while True:
#             time.sleep(1)
#     except KeyboardInterrupt:
#         observer.stop()
#     observer.join()
