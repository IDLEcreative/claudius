"""Serialised, content-based filing shared by the router and daily archiver.

Drive failures are never interpreted as absence. The per-account lock covers
listing, upload and readback; legacy files are recognised by Drive MD5 + size.
MD5 here is a duplicate hint for trusted provider files, not a security signature.
"""

import fcntl
import hashlib
import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

PAGE_SIZE = 1000
MAX_PAGES = 20
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_ATTACHMENTS = 100
DRIVE_BASE = "https://www.googleapis.com/drive/v3/"


def _read(endpoint: str, token: str) -> dict:
    request = urllib.request.Request(DRIVE_BASE + endpoint)
    request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def _content_key(file: dict) -> tuple[str, int]:
    return file["md5Checksum"], int(file["size"])


def folder_contents(folder_id: str, token: str) -> set[tuple[str, int]]:
    """Read every bounded page; abort instead of treating a partial list as complete."""
    if not folder_id:
        raise ValueError("Missing financial archive folder")
    escaped = folder_id.replace("\\", "\\\\").replace("'", "\\'")
    params = {"q": f"'{escaped}' in parents and trashed = false",
              "fields": "nextPageToken,files(id,mimeType,md5Checksum,size)",
              "pageSize": PAGE_SIZE}
    keys = set()
    for _ in range(MAX_PAGES):
        response = _read("files?" + urllib.parse.urlencode(params), token)
        if "files" not in response:
            raise ValueError("Drive returned no file listing")
        for file in response["files"]:
            if file.get("mimeType", "").startswith("application/vnd.google-apps."):
                continue
            keys.add(_content_key(file))
        if not response.get("nextPageToken"):
            return keys
        params["pageToken"] = response["nextPageToken"]
    raise ValueError("Financial archive exceeds bounded listing; manual review required")


def archive_files(
    attachments: list[dict], folder_id: str | Callable[[], str], lock_path: str,
    get_token: Callable[[], str], download: Callable[[dict], bytes | None],
    upload: Callable[[bytes, str, str, str], str | None],
    name_for: Callable[[dict], str],
) -> dict:
    """Save only new contents and count success only after Drive readback.

No state is marked here. Callers must retain failed messages for retry. Both
collectors must use the same account-scoped lock path on the same host.
"""
    result: dict[str, list[str]] = {"saved": [], "duplicate": [], "failed": []}
    if not attachments:
        return result
    if len(attachments) > MAX_ATTACHMENTS:
        raise ValueError("Too many financial attachments; manual review required")
    if not folder_id:
        result["failed"] = [a["filename"] for a in attachments]
        return result
    # The configured state directory already exists; never create a global lock.
    with Path(lock_path).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            folder_id = folder_id() if callable(folder_id) else folder_id
            if not folder_id:
                raise ValueError("Missing financial archive folder")
            token = get_token()
            existing = folder_contents(folder_id, token)
            for index, attachment in enumerate(attachments):
                filename = attachment["filename"]
                try:
                    if int(attachment.get("size", 0)) > MAX_ATTACHMENT_BYTES:
                        raise ValueError("Attachment exceeds capture limit")
                    data = download(attachment)
                    if not data or len(data) > MAX_ATTACHMENT_BYTES:
                        raise ValueError("Missing or oversized attachment")
                    key = hashlib.md5(data).hexdigest(), len(data)
                    if key in existing:
                        result["duplicate"].append(filename)
                        continue
                    name = name_for(attachment)
                    file_id = upload(data, name, folder_id, attachment["mime_type"])
                    if not file_id:
                        raise ValueError("Upload did not return a file ID")
                    fields = urllib.parse.urlencode({"fields": "md5Checksum,size,parents,trashed"})
                    persisted = _read(f"files/{urllib.parse.quote(file_id, safe='')}?{fields}", get_token())
                    if (persisted.get("trashed") or folder_id not in persisted.get("parents", [])
                            or _content_key(persisted) != key):
                        raise ValueError("Uploaded attachment readback did not match")
                    existing.add(key)
                    result["saved"].append(name)
                except (OSError, ValueError, KeyError, TypeError) as error:
                    print(f"[Financial archive] Attachment failed: {type(error).__name__}")
                    # A timed-out write may already exist remotely. Stop this batch
                    # so retry re-reads Drive before considering identical later parts.
                    result["failed"].extend(a["filename"] for a in attachments[index:])
                    break
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    return result
