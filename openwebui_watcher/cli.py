import os
import time
import json
import logging
import requests

UPLOAD_DB_FILE = ".upload.json"
LOG_PATH = "/tmp/openwebui_watcher.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("openwebui_watcher")

API_KEY = os.getenv("OPENWEBUI_API_KEY")
API_URL = os.getenv("OPENWEBUI_API_URL")
KNOWLEDGE_ID = os.getenv("OPENWEBUI_KNOWLEDGE_ID")
ALLOWED_FILE_EXTENSIONS = os.getenv("ALLOWED_FILE_EXTENSIONS", "")

assert API_KEY and API_URL and KNOWLEDGE_ID, "Set OPENWEBUI_API_KEY, OPENWEBUI_API_URL, OPENWEBUI_KNOWLEDGE_ID in env"

if ALLOWED_FILE_EXTENSIONS:
    ALLOWED_EXTS = set(ext.strip().lower() for ext in ALLOWED_FILE_EXTENSIONS.split(",") if ext.strip())
    logger.info(f"Will upload only files with these extensions: {sorted(ALLOWED_EXTS)}")
else:
    ALLOWED_EXTS = None
    logger.info("No ALLOWED_FILE_EXTENSIONS set, will upload all file types.")

UPLOAD_ENDPOINT = f"{API_URL}/api/v1/files/"
ADD_FILE_TO_KNOWLEDGE_ENDPOINT = f"{API_URL}/api/v1/knowledge/{KNOWLEDGE_ID}/file/add"


def has_allowed_extension(file_path):
    if ALLOWED_EXTS is None:
        return True
    ext = os.path.splitext(file_path)[1][1:].lower()
    return ext in ALLOWED_EXTS


def load_upload_db():
    if os.path.exists(UPLOAD_DB_FILE):
        try:
            with open(UPLOAD_DB_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load upload database {UPLOAD_DB_FILE}: {e}")
            return {}
    return {}


def save_upload_db(db):
    try:
        with open(UPLOAD_DB_FILE, "w") as f:
            json.dump(db, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save upload database {UPLOAD_DB_FILE}: {e}")


def build_upload_filename(file_path, root_dir):
    """
    Converts file's path to uploaded filename with full path encoding:
    e.g. 'foo/bar/baz.txt' -> 'foo__bar__baz.txt'
    Only path relative to root_dir is used.
    """
    abs_root = os.path.abspath(root_dir)
    abs_file = os.path.abspath(file_path)
    rel_path = os.path.relpath(abs_file, abs_root)
    parts = rel_path.split(os.sep)
    # Join all parts except last with '__' + filename
    if len(parts) == 1:
        # File in root folder
        upload_filename = parts[0]
    else:
        upload_filename = "__".join(parts[:-1]) + "__" + parts[-1]
    return upload_filename


def upload_file(file_path, root_dir="."):
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json"
    }
    upload_filename = build_upload_filename(file_path, root_dir)
    logger.info(f"Uploading '{file_path}' as '{upload_filename}'")

    with open(file_path, "rb") as f:
        files = {'file': (upload_filename, f)}
        resp = requests.post(UPLOAD_ENDPOINT, headers=headers, files=files)
    resp.raise_for_status()
    resp_json = resp.json()
    file_id = resp_json.get("id") or resp_json.get("data", {}).get("id")
    if not file_id:
        logger.error(f"Failed to get file id from: {resp_json}")
        return None
    logger.info(f"Uploaded {file_path} as {upload_filename} with id {file_id}")
    return file_id


def add_file_to_knowledge(file_id):
    url = ADD_FILE_TO_KNOWLEDGE_ENDPOINT
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:137.0) Gecko/20100101 Firefox/137.0",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Referer": f"{API_URL}/workspace/knowledge/{KNOWLEDGE_ID}",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
        "Origin": API_URL,
        "Connection": "keep-alive",
        "Cookie": f"token={API_KEY}",
        "Sec-GPC": "1",
        "Priority": "u=4"
    }
    data = {"file_id": file_id}

    resp = requests.post(url, headers=headers, json=data)
    logger.info(f"Add to knowledge response code: {resp.status_code}")
    logger.info(f"Add to knowledge response content: {resp.text}")
    resp.raise_for_status()
    logger.info(f"Added file {file_id} to knowledge {KNOWLEDGE_ID}")
    return resp.json()


def scan_and_sync(scan_dir=".", sleep_period=30):
    upload_db = load_upload_db()
    logger.info("Starting continuous sync loop")

    while True:
        updated = False
        for root, dirs, files in os.walk(scan_dir):
            for fname in files:
                file_path = os.path.abspath(os.path.join(root, fname))
                if not has_allowed_extension(file_path):
                    continue
                try:
                    mtime = os.path.getmtime(file_path)
                except Exception as e:
                    logger.error(f"Failed to get mtime for {file_path}: {e}")
                    continue
                record = upload_db.get(file_path)
                if not record or record.get("mtime") != mtime:
                    try:
                        file_id = upload_file(file_path, root_dir=scan_dir)
                        if file_id:
                            add_file_to_knowledge(file_id)
                            upload_db[file_path] = {"mtime": mtime, "file_id": file_id}
                            updated = True
                    except Exception as e:
                        logger.error(f"Error uploading or adding {file_path}: {e}")

        if updated:
            save_upload_db(upload_db)
        time.sleep(sleep_period)


def main():
    scan_and_sync(".")



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
