"""Storing information library files.

* :func:`detect_type` recognises PDFs and images by their content (not by
  the file name).
* :func:`sha1_of` computes the SHA-1 hash that identifies a file.
* :func:`store_file` saves a new file as ``<sha1>.<ext>`` in the library
  folder and creates its :class:`~app.models.document.InfoFile`; when a file
  with the same hash already exists it raises :class:`DuplicateFile` and
  stores nothing.
"""

from __future__ import annotations

import hashlib
import os
import re

from flask import current_app

from app.extensions import db
from app.i18n.catalog import gettext
from app.models import InfoFile
from app.models.document import FILE_TYPES

#: Default largest upload (bytes); ``LIBRARY_MAX_BYTES`` in the configuration.
DEFAULT_MAX_BYTES = 25 * 1024 * 1024
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")


class LibraryError(ValueError):
    """The upload cannot be stored (type, size, empty file)."""


class DuplicateFile(Exception):
    """A file with the same SHA-1 is already in the library.

    Attributes:
        existing: The existing :class:`InfoFile`.
    """

    def __init__(self, existing: InfoFile):
        """Remember the existing file.

        Args:
            existing: The file with the same content.
        """
        super().__init__(existing.name)
        self.existing = existing


def library_folder() -> str:
    """Folder of the stored files (``<UPLOAD_FOLDER>/library``), created when missing.

    Returns:
        Absolute path.
    """
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "library")
    os.makedirs(folder, exist_ok=True)
    return folder


def max_bytes() -> int:
    """Largest accepted file size.

    Returns:
        Bytes.
    """
    return int(current_app.config.get("LIBRARY_MAX_BYTES", DEFAULT_MAX_BYTES))


def detect_type(data: bytes) -> str | None:
    """Recognise the file type by its first bytes.

    Args:
        data: File content.

    Returns:
        Extension (``pdf``, ``png``, ``jpg``, ``gif``, ``webp``, ``bmp``) or ``None``.
    """
    head = data[:16]
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:2] == b"BM":
        return "bmp"
    return None


def sha1_of(data: bytes) -> str:
    """SHA-1 of a file's content.

    Args:
        data: File content.

    Returns:
        40 lower-case hex digits.
    """
    return hashlib.sha1(data).hexdigest()  # noqa: S324 - content identifier, not a security hash


def valid_sha1(text: str | None) -> str | None:
    """Normalise a SHA-1 text from the browser.

    Args:
        text: Hash text.

    Returns:
        The lower-case hash, or ``None`` if it is not 40 hex digits.
    """
    text = (text or "").strip().lower()
    return text if _SHA1_RE.match(text) else None


def find_by_sha1(sha1: str | None) -> InfoFile | None:
    """The library file with this hash.

    Args:
        sha1: Hash (any case).

    Returns:
        The :class:`InfoFile` or ``None``.
    """
    sha1 = valid_sha1(sha1)
    return InfoFile.query.filter_by(sha1=sha1).first() if sha1 else None


def clean_name(name: str) -> str:
    """A display name from an uploaded file name.

    Args:
        name: Original file name.

    Returns:
        Name without folders and extension (at most 200 characters).
    """
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return (stem or gettext("Document"))[:200]


def store_file(data: bytes, original_name: str, user=None, name: str | None = None,
               description: str = "") -> InfoFile:
    """Add a file to the information library (the caller commits).

    Args:
        data: File content.
        original_name: Name of the uploaded file.
        user: Uploading user.
        name: Display name (default: the file name without extension).
        description: Optional description.

    Returns:
        The new :class:`InfoFile`.

    Raises:
        LibraryError: Empty, too large or not a PDF / image.
        DuplicateFile: The same content is already in the library (nothing is stored).
    """
    if not data:
        raise LibraryError(gettext("The file is empty."))
    if len(data) > max_bytes():
        raise LibraryError(gettext("The file is larger than {0} MB.", max_bytes() // (1024 * 1024)))
    ext = detect_type(data)
    if ext is None:
        raise LibraryError(gettext("Only PDF files and images (PNG, JPEG, GIF, WebP, BMP) can be added."))
    digest = sha1_of(data)
    existing = find_by_sha1(digest)
    if existing is not None:
        raise DuplicateFile(existing)
    filename = f"{digest}.{ext}"
    path = os.path.join(library_folder(), filename)
    with open(path, "wb") as fh:
        fh.write(data)
    item = InfoFile(name=(name or clean_name(original_name)).strip()[:200] or clean_name(original_name),
                    original_name=os.path.basename((original_name or "").replace("\\", "/"))[:255] or filename,
                    filename=filename, mimetype=FILE_TYPES[ext][0], size=len(data), sha1=digest,
                    description=(description or "")[:500], uploaded_by=user)
    db.session.add(item)
    return item


def delete_file(item: InfoFile) -> str:
    """Delete a library file (detached from its PLCs; the caller commits, then removes the content).

    Args:
        item: The file.

    Returns:
        Path of the stored content, to remove with :func:`remove_content` after the commit.
    """
    path = os.path.join(library_folder(), item.filename)
    item.plcs = []
    db.session.delete(item)
    return path


def remove_content(path: str) -> None:
    """Remove a stored file (missing files are ignored).

    Args:
        path: Path returned by :func:`delete_file`.
    """
    try:
        os.remove(path)
    except OSError:
        pass
