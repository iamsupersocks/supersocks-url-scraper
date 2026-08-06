"""Content-first document format detection (magic / MIME / extension / disposition)."""

from __future__ import annotations

import io
import re
import zipfile
from email.message import Message
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .models import DocumentContent  # noqa: F401 — re-exported convenience

# AnyDoc-supported office formats. PDF stays a first-class content_type.
DOCUMENT_FORMATS = frozenset(
    {"doc", "docx", "odt", "ppt", "pptx", "rtf", "epub", "xlsx", "ods", "odp", "csv"}
)
ALL_DOCUMENT_FORMATS = DOCUMENT_FORMATS | {"pdf"}

_MIME_TO_DOCUMENT_FORMAT: dict[str, str] = {
    "application/pdf": "pdf",
    "application/x-pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.ms-word": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.ms-excel": "xlsx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.presentation": "odp",
    "application/rtf": "rtf",
    "text/rtf": "rtf",
    "application/epub+zip": "epub",
    "text/csv": "csv",
    "application/csv": "csv",
    "application/vnd.ms-excel.sheet.macroenabled.12": "xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template": "docx",
}

_ODF_MIMETYPE_TO_FORMAT = {
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.presentation": "odp",
    "application/epub+zip": "epub",
}

_ZIP_MIMETYPE_MAX_DECLARED = 256
_ZIP_MIMETYPE_READ_LIMIT = 256

_EXT_MANUAL = {
    "pdf": "pdf",
    "doc": "doc",
    "docx": "docx",
    "docm": "docx",
    "odt": "odt",
    "rtf": "rtf",
    "epub": "epub",
    "ppt": "ppt",
    "pptx": "pptx",
    "pptm": "pptx",
    "pps": "ppt",
    "ppsx": "pptx",
    "xls": "xlsx",
    "xlsx": "xlsx",
    "xlsm": "xlsx",
    "xlsb": "xlsx",
    "ods": "ods",
    "odp": "odp",
    "csv": "csv",
}


def is_image_magic(head: bytes) -> bool:
    return (
        head.startswith(b"\x89PNG\r\n\x1a\n")
        or head.startswith(b"\xff\xd8\xff")
        or head.startswith(b"GIF87a")
        or head.startswith(b"GIF89a")
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    )


def is_html_magic(head: bytes) -> bool:
    sniff = head.lstrip()[:64]
    return any(sig in sniff for sig in (b"<!doctype", b"<html", b"<HTML", b"<!DOCTYPE"))


def _read_zip_mimetype_limited(zf: zipfile.ZipFile) -> str | None:
    try:
        info = zf.getinfo("mimetype")
    except KeyError:
        return None
    if info.file_size > _ZIP_MIMETYPE_MAX_DECLARED:
        return None
    with zf.open(info, "r") as fp:
        raw = fp.read(_ZIP_MIMETYPE_READ_LIMIT + 1)
    if len(raw) > _ZIP_MIMETYPE_READ_LIMIT:
        return None
    return raw.decode("ascii", errors="ignore").strip().split(";")[0].strip().lower()


def format_from_zip_package(data: bytes) -> str | None:
    if len(data) < 4 or data[:2] != b"PK":
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
            if "word/document.xml" in names or any(name.startswith("word/") for name in names):
                return "docx"
            if "ppt/presentation.xml" in names or any(name.startswith("ppt/") for name in names):
                return "pptx"
            if "xl/workbook.xml" in names or any(name.startswith("xl/") for name in names):
                return "xlsx"
            if "mimetype" in names:
                mime = _read_zip_mimetype_limited(zf)
                if mime is None:
                    return None
                return _ODF_MIMETYPE_TO_FORMAT.get(mime)
    except (zipfile.BadZipFile, OSError, KeyError):
        return None
    return None


def format_from_content_bytes(data: bytes) -> str | None:
    if not data:
        return None
    try:
        import anydoc  # type: ignore[import-not-found]

        detected = anydoc.format_from_bytes(data)
        if detected in ALL_DOCUMENT_FORMATS:
            return str(detected)
    except Exception:
        pass
    head = data[:512]
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.lstrip().startswith(b"{\\rtf"):
        return "rtf"
    return format_from_zip_package(data)


def format_from_mime(content_type: str) -> str | None:
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    if not ctype:
        return None
    return _MIME_TO_DOCUMENT_FORMAT.get(ctype)


def format_from_extension_value(value: str | None) -> str | None:
    if not value:
        return None
    name = value.strip().split("?")[0].split("#")[0]
    if "/" in name or "\\" in name:
        name = Path(name).name
    if not name:
        return None
    ext = Path(name).suffix.lower().lstrip(".")
    if not ext:
        return None
    try:
        import anydoc  # type: ignore[import-not-found]

        detected = anydoc.format_from_extension(ext)
        if detected in ALL_DOCUMENT_FORMATS:
            return str(detected)
    except Exception:
        pass
    return _EXT_MANUAL.get(ext)


def filename_from_content_disposition(headers: dict[str, str] | None) -> str | None:
    if not headers:
        return None
    raw = ""
    for key, value in headers.items():
        if key.lower() == "content-disposition":
            raw = value
            break
    if not raw:
        return None
    message = Message()
    message["content-disposition"] = raw
    filename = message.get_filename()
    if filename:
        return unquote(filename.strip())
    match = re.search(
        r"filename\*\s*=\s*(?:UTF-8''|utf-8'')([^;]+)|filename\s*=\s*\"([^\"]+)\"|filename\s*=\s*([^;\s]+)",
        raw,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return unquote((match.group(1) or match.group(2) or match.group(3) or "").strip().strip("\"'"))


def detect_document_format(
    *,
    content: bytes = b"",
    content_type: str = "",
    url: str = "",
    headers: dict[str, str] | None = None,
) -> str | None:
    """Detect an AnyDoc/PDF format: content first, then MIME / URL / Content-Disposition."""
    fmt = format_from_content_bytes(content)
    if fmt:
        return fmt
    fmt = format_from_mime(content_type)
    if fmt:
        return fmt
    fmt = format_from_extension_value(urlsplit(url).path)
    if fmt:
        return fmt
    return format_from_extension_value(filename_from_content_disposition(headers))


def title_from_markdown(markdown: str) -> str | None:
    for line in (markdown or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            return heading or None
        if len(stripped) > 180:
            return stripped[:179].rstrip() + "…"
        return stripped
    return None
