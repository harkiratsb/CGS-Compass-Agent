"""
Google Drive import via a service account (no interactive OAuth).

Setup (one time, ~20 min):
  1. console.cloud.google.com -> create project -> APIs & Services -> Enable "Google Drive API"
  2. IAM & Admin -> Service Accounts -> Create -> Keys -> Add key (JSON) -> download
  3. Save it as  service_account.json  next to app.py  (or set GOOGLE_SERVICE_ACCOUNT_FILE)
  4. In Drive, share the process-materials folder with the service account email (Viewer)
  5. Put the folder ID (the part of the folder URL after /folders/) in .env as DRIVE_FOLDER_ID

Google Docs are exported as plain text; Word/PDF/text files are downloaded as-is.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
from typing import List, Tuple

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
EXPORT = {  # Google-native types -> export mime, filename extension
    "application/vnd.google-apps.document": ("text/plain", ".txt"),
    "application/vnd.google-apps.presentation": ("text/plain", ".txt"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
}
DOWNLOADABLE = {
    "text/plain", "text/markdown", "text/vtt", "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def credentials_path() -> Path | None:
    p = Path(os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"))
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p
    return p if p.exists() else None


def available() -> bool:
    return credentials_path() is not None


def _service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(str(credentials_path()), scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def list_folder(folder_id: str) -> List[dict]:
    """Return [{id, name, mimeType, modifiedTime}] for supported files in the folder."""
    svc = _service()
    q = f"'{folder_id}' in parents and trashed = false"
    res = svc.files().list(q=q, fields="files(id,name,mimeType,modifiedTime,size)",
                           orderBy="name", pageSize=100,
                           supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    files = res.get("files", [])
    return [f for f in files if f["mimeType"] in EXPORT or f["mimeType"] in DOWNLOADABLE
            or f["name"].lower().endswith((".md", ".txt", ".vtt", ".srt", ".docx", ".pdf"))]


def fetch(file: dict) -> Tuple[str, bytes]:
    """Download or export one file. Returns (filename, bytes)."""
    from googleapiclient.http import MediaIoBaseDownload
    svc = _service()
    name, mime = file["name"], file["mimeType"]
    if mime in EXPORT:
        export_mime, ext = EXPORT[mime]
        req = svc.files().export_media(fileId=file["id"], mimeType=export_mime)
        if not name.lower().endswith(ext):
            name = name + ext
    else:
        req = svc.files().get_media(fileId=file["id"], supportsAllDrives=True)
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = dl.next_chunk()
    return name, buf.getvalue()
