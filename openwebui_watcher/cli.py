import os
import time
import json
import logging
import requests
import mimetypes

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
UPDATE_FILE_CONTENT_ENDPOINT_TEMPLATE = f"{API_URL}/api/v1/files/{{file_id}}/data/content/update"


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


def build_upload_filename(file_path, root_dir, upload_db):
    """
    Returns upload filename.
    Uses plain filename if unique in upload_db,
    otherwise prefixes immediate parent directory with __.
    """
    abs_root = os.path.abspath(root_dir)
    abs_file = os.path.abspath(file_path)
    rel_path = os.path.relpath(abs_file, abs_root)
    filename = os.path.basename(file_path)

    # Check for duplicates
    duplicates = [
        path for path in upload_db.keys()
        if os.path.basename(path) == filename and path != abs_file
    ]

    if not duplicates:
        return filename
    else:
        parts = rel_path.split(os.sep)
        if len(parts) >= 2:
            parent_folder = parts[-2]
            return f"{parent_folder}__{filename}"
        else:
            return filename


def upload_file(file_path, root_dir=".", upload_db=None):
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json"
    }

    if upload_db is None:
        upload_db = {}

    upload_filename = build_upload_filename(file_path, root_dir, upload_db)
    logger.info(f"Uploading '{file_path}' as '{upload_filename}'")

    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        mime_type = "application/octet-stream"

    with open(file_path, "rb") as f:
        files = {'file': (upload_filename, f, mime_type, {})}
        # The last dict is extra headers per file part; empty dict here
        resp = requests.post(UPLOAD_ENDPOINT, headers=headers, files=files)

    resp.raise_for_status()
    resp_json = resp.json()

    file_id = resp_json.get("id") or resp_json.get("data", {}).get("id")
    if not file_id:
        logger.error(f"Failed to get file id from upload response: {resp_json}")
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


def update_file_content(file_id, file_path):
    url = UPDATE_FILE_CONTENT_ENDPOINT_TEMPLATE.format(file_id=file_id)
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        # Could not decode as text, fallback to binary update is not possible via this API
        logger.error(f"Failed to read file {file_path} as UTF-8 text for content update; skipping update")
        return False
    except Exception as e:
        logger.error(f"Failed to read file {file_path} for content update: {e}")
        return False

    data = {"content": content}

    logger.info(f"Updating content for file id {file_id} from {file_path}")
    resp = requests.post(url, headers=headers, json=data)
    logger.info(f"Update content response code: {resp.status_code}")
    logger.info(f"Update content response content: {resp.text}")
    try:
        resp.raise_for_status()
        logger.info(f"Successfully updated content for file id {file_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to update content for file id {file_id}: {e}")
        return False


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

                # New file or modified file
                if not record or record.get("mtime") != mtime:

                    try:
                        if record and "file_id" in record:
                            # Update existing file content
                            success = update_file_content(record["file_id"], file_path)
                            if not success:
                                # On failure fallback to reupload
                                file_id = upload_file(file_path, root_dir=scan_dir, upload_db=upload_db)
                                if file_id:
                                    add_file_to_knowledge(file_id)
                                    upload_db[file_path] = {"mtime": mtime, "file_id": file_id}
                                    updated = True
                            else:
                                upload_db[file_path]["mtime"] = mtime
                                updated = True
                        else:
                            # New file: upload and add to knowledge
                            file_id = upload_file(file_path, root_dir=scan_dir, upload_db=upload_db)
                            if file_id:
                                add_file_to_knowledge(file_id)
                                upload_db[file_path] = {"mtime": mtime, "file_id": file_id}
                                updated = True
                    except Exception as e:
                        logger.error(f"Error updating or uploading {file_path}: {e}")

        if updated:
            save_upload_db(upload_db)
        time.sleep(sleep_period)


def main():
    scan_and_sync(".")