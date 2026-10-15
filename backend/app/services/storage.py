"""Private file storage for resumes, employee documents and offer letters.

Files are written outside the web root under ``UPLOAD_FOLDER`` with random names
and are only served through authorised routes. In production point UPLOAD_FOLDER
at a persistent private disk (e.g. a Render Persistent Disk mount).
"""
from __future__ import annotations

import hashlib
import io
import secrets
import zipfile
from dataclasses import dataclass
from pathlib import Path

from flask import current_app

from app.errors import NotFound, ValidationFailed

ALLOWED_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}
RESUME_EXTENSIONS = ("pdf", "docx")
DOCUMENT_EXTENSIONS = ("pdf", "docx", "png", "jpg", "jpeg")


@dataclass
class StoredFile:
    stored_filename: str
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    extension: str


def _safe_original_name(name: str) -> str:
    name = Path(name or "file").name
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in "._- ").strip()
    return (cleaned or "file")[:200]


def detect_type(data: bytes, extension: str) -> bool:
    """Verify file content matches its extension (magic bytes / structure)."""
    if extension == "pdf":
        return data[:5] == b"%PDF-"
    if extension == "docx":
        if data[:4] != b"PK\x03\x04":
            return False
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = set(zf.namelist())
                return "word/document.xml" in names and "[Content_Types].xml" in names
        except zipfile.BadZipFile:
            return False
    if extension == "png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    if extension in ("jpg", "jpeg"):
        return data[:3] == b"\xff\xd8\xff"
    return False


def validate_upload(file_storage, allowed_extensions) -> tuple[bytes, str]:
    if file_storage is None or not getattr(file_storage, "filename", ""):
        raise ValidationFailed("Please choose a file to upload.")
    extension = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if extension not in allowed_extensions:
        raise ValidationFailed(f"Unsupported file type. Allowed: {', '.join(e.upper() for e in allowed_extensions)}.")
    data = file_storage.read()
    max_bytes = current_app.config["MAX_CONTENT_LENGTH"]
    if not data:
        raise ValidationFailed("The uploaded file is empty.")
    if len(data) > max_bytes:
        raise ValidationFailed(f"File exceeds the {max_bytes // (1024 * 1024)} MB limit.")
    if not detect_type(data, extension):
        raise ValidationFailed("The file content does not match its extension or the file is corrupted.")
    return data, extension


def _root(kind: str) -> Path:
    root = Path(current_app.config["UPLOAD_FOLDER"]) / kind
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_bytes(kind: str, data: bytes, extension: str, original_filename: str) -> StoredFile:
    stored = f"{secrets.token_hex(16)}.{extension}"
    path = _root(kind) / stored
    with open(path, "xb") as fh:  # 'x' never overwrites
        fh.write(data)
    return StoredFile(
        stored_filename=stored,
        original_filename=_safe_original_name(original_filename),
        content_type=ALLOWED_TYPES.get(extension, "application/octet-stream"),
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        extension=extension,
    )


def save_upload(kind: str, file_storage, allowed_extensions) -> tuple[StoredFile, bytes]:
    data, extension = validate_upload(file_storage, allowed_extensions)
    return save_bytes(kind, data, extension, file_storage.filename), data


def path_for(kind: str, stored_filename: str) -> Path:
    if not stored_filename or "/" in stored_filename or "\\" in stored_filename or ".." in stored_filename:
        raise NotFound("File not found.")
    path = _root(kind) / stored_filename
    if not path.is_file():
        raise NotFound("File not found.")
    return path


def delete_file(kind: str, stored_filename: str | None) -> None:
    if not stored_filename:
        return
    try:
        path_for(kind, stored_filename).unlink(missing_ok=True)
    except NotFound:
        pass
