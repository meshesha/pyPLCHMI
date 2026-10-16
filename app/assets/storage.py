"""Validation and storage of uploaded image files.

Files are identified by their *content* (magic bytes), never by the file name
or the browser-supplied MIME type.  SVG files are text and are additionally
rejected when they contain scripts, event handlers, ``javascript:`` URLs,
``<foreignObject>`` or entity declarations.
"""

import os
import re
import uuid

from flask import current_app
from app.i18n.catalog import gettext

#: MIME type → file extension for every accepted format.
EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
}

_SVG_FORBIDDEN = re.compile(r"<\s*script|javascript\s*:|\son[a-z]+\s*=|<\s*foreignObject|<!ENTITY", re.IGNORECASE)


class AssetError(ValueError):
    """Raised for uploads that are empty, too large, or not a supported image."""


def detect_mimetype(data: bytes) -> str:
    """Identify an image format from its first bytes.

    Args:
        data: Complete file content.

    Returns:
        One of the keys of :data:`EXTENSIONS`.

    Raises:
        AssetError: If the content is not a supported image or is an unsafe SVG.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AssetError(gettext('Unsupported file type (use PNG, JPEG, GIF, WebP or SVG).')) from exc
    head = text.lstrip()[:200].lower()
    if (head.startswith("<?xml") or head.startswith("<svg") or head.startswith("<!--")) and "<svg" in text.lower():
        if _SVG_FORBIDDEN.search(text):
            raise AssetError(gettext('SVG files containing scripts, event handlers or entities are not allowed.'))
        return "image/svg+xml"
    raise AssetError(gettext('Unsupported file type (use PNG, JPEG, GIF, WebP or SVG).'))


def upload_folder() -> str:
    """Return (and create) the folder where asset files are stored.

    Returns:
        Absolute path from ``UPLOAD_FOLDER`` (default ``instance/uploads``).
    """
    folder = current_app.config.get("UPLOAD_FOLDER") or os.path.join(current_app.instance_path, "uploads")
    os.makedirs(folder, exist_ok=True)
    return folder


def save_upload(data: bytes) -> tuple[str, str]:
    """Validate and store uploaded bytes under a random file name.

    Args:
        data: File content.

    Returns:
        Tuple ``(stored_filename, mimetype)``.

    Raises:
        AssetError: If the file is empty, too large or not a valid image.
    """
    if not data:
        raise AssetError(gettext('The file is empty.'))
    limit = current_app.config.get("ASSET_MAX_BYTES", 5 * 1024 * 1024)
    if len(data) > limit:
        raise AssetError(gettext('File too large (max {0} MB).', limit // 1024 // 1024))
    mimetype = detect_mimetype(data)
    filename = f"{uuid.uuid4().hex}.{EXTENSIONS[mimetype]}"
    with open(os.path.join(upload_folder(), filename), "wb") as fh:
        fh.write(data)
    return filename, mimetype


def delete_file(filename: str) -> None:
    """Remove a stored file if it exists.

    Args:
        filename: Stored (random) file name.
    """
    path = os.path.join(upload_folder(), os.path.basename(filename))
    if os.path.exists(path):
        os.remove(path)
