"""
Google Drive API utilities for Claudius.

Provides file upload, folder creation/lookup for Drive.
"""

import json
import base64
import urllib.request
import urllib.error
import urllib.parse
from typing import Optional

from lib.google_auth import get_access_token

DRIVE_BASE = "https://www.googleapis.com/drive/v3"
UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"


def drive_request(
    endpoint: str,
    account_email: str,
    method: str = "GET",
    body: dict = None
) -> dict:
    """Make a Drive API request."""
    token = get_access_token(account_email)
    url = f"{DRIVE_BASE}/{endpoint}"

    if body:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
    else:
        req = urllib.request.Request(url, method=method)

    req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"[Drive API] Error {e.code}: {error_body[:200]}")
        return {}


def upload_file(
    account_email: str,
    file_data: bytes,
    filename: str,
    folder_id: str,
    mime_type: str = "application/octet-stream"
) -> Optional[str]:
    """Upload a file to Drive using multipart upload. Returns file ID or None."""
    token = get_access_token(account_email)
    boundary = "----ClaudiusBoundary"

    metadata = json.dumps({
        "name": filename,
        "parents": [folder_id]
    })

    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{metadata}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: {mime_type}\r\n"
        f"Content-Transfer-Encoding: base64\r\n\r\n"
    ).encode("utf-8")

    body += base64.b64encode(file_data)
    body += f"\r\n--{boundary}--".encode("utf-8")

    url = f"{UPLOAD_BASE}/files?uploadType=multipart"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", f"multipart/related; boundary={boundary}")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
            return result.get("id")
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"[Drive Upload] Error {e.code}: {error_body[:200]}")
        return None


def find_folder(
    account_email: str,
    name: str,
    parent_id: str = None
) -> Optional[str]:
    """Find a folder by name, optionally within a parent. Returns folder ID."""
    query = (
        f"name='{name}' and mimeType='application/vnd.google-apps.folder' "
        f"and trashed=false"
    )
    if parent_id:
        query += f" and '{parent_id}' in parents"

    encoded_query = urllib.parse.quote(query)
    result = drive_request(
        f"files?q={encoded_query}&fields=files(id,name)", account_email
    )
    files = result.get("files", [])
    return files[0]["id"] if files else None


def create_folder(
    account_email: str,
    name: str,
    parent_id: str = None
) -> Optional[str]:
    """Create a folder on Drive. Returns folder ID."""
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder"
    }
    if parent_id:
        metadata["parents"] = [parent_id]

    result = drive_request("files", account_email, method="POST", body=metadata)
    return result.get("id")


def find_or_create_folder(
    account_email: str,
    name: str,
    parent_id: str = None
) -> Optional[str]:
    """Find a folder or create it if it doesn't exist."""
    folder_id = find_folder(account_email, name, parent_id)
    if folder_id:
        return folder_id
    return create_folder(account_email, name, parent_id)
