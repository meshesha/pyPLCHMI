"""Folder structure of the image library and shared image storage helpers.

Folders are plain paths (``"Animations/HVAC"``).  Each image stores the path
of its folder in :attr:`Asset.category`; :class:`AssetFolder` rows make every
folder (and each of its parents) exist even while it is empty.

This module also provides :func:`store_asset` (add an image to the library,
re-using an identical existing file) and :func:`import_builtin_animations`
(the animated GIFs shipped in ``app/library/animations``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re

from app.assets.storage import save_upload, upload_folder
from app.extensions import db
from app.models import Asset, AssetFolder
from app.i18n.catalog import gettext

#: Folder used when no folder is given.
DEFAULT_FOLDER = "Images"
#: Root folder of the built-in animations.
ANIMATIONS_FOLDER = "Animations"
#: Maximum length of a full folder path / of one folder name.
MAX_PATH = 200
MAX_NAME = 60
#: Folder of the built-in animated GIFs and their ``manifest.json``.
BUILTIN_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "library", "animations")

_BAD_CHARS = re.compile(r"[\\<>:\"|?*\x00-\x1f]")


class FolderError(ValueError):
    """Raised for invalid folder names or impossible folder operations."""


def normalize_path(text: str | None, default: str | None = DEFAULT_FOLDER) -> str:
    """Clean a user supplied folder path.

    Leading/trailing slashes and spaces are removed, repeated separators are
    collapsed (``" a // b "`` → ``"a/b"``).

    Args:
        text: Raw path, ``/`` separated.
        default: Value returned for an empty path; ``None`` raises instead.

    Returns:
        The normalised path.

    Raises:
        FolderError: If a name contains forbidden characters, is ``.``/``..``,
            or the path or a name is too long (or empty without a default).
    """
    parts = [p.strip() for p in (text or "").replace("\\", "/").split("/")]
    parts = [p for p in parts if p]
    if not parts:
        if default is None:
            raise FolderError(gettext('Folder name is required.'))
        return default
    for part in parts:
        if part in (".", "..") or _BAD_CHARS.search(part):
            raise FolderError(gettext('Invalid folder name \'{0}\' (not allowed: \\ < > : " | ? *).', part))
        if len(part) > MAX_NAME:
            raise FolderError(gettext("Folder name '{0}…' is too long (max {1} characters).", part[:20], MAX_NAME))
    path = "/".join(parts)
    if len(path) > MAX_PATH:
        raise FolderError(gettext('Folder path is too long (max {0} characters).', MAX_PATH))
    return path


def ancestors(path: str) -> list[str]:
    """List a path and all its parent paths, top-level first.

    Args:
        path: Normalised folder path.

    Returns:
        E.g. ``["Animations", "Animations/HVAC"]`` for ``"Animations/HVAC"``.
    """
    parts = path.split("/")
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


def is_within(path: str, folder: str) -> bool:
    """Tell whether ``path`` is ``folder`` itself or one of its sub-folders.

    Args:
        path: Path to test.
        folder: Folder path.

    Returns:
        ``True`` if inside (or equal).
    """
    return path == folder or path.startswith(folder + "/")


def ensure_folder(path: str) -> str:
    """Create a folder and its parents if they do not exist yet (no commit).

    Args:
        path: Folder path (normalised by this function).

    Returns:
        The normalised path.

    Raises:
        FolderError: If the path is invalid.
    """
    path = normalize_path(path)
    existing = {f.path for f in AssetFolder.query.filter(AssetFolder.path.in_(ancestors(path))).all()}
    pending = {o.path for o in db.session.new if isinstance(o, AssetFolder)}
    for p in ancestors(path):
        if p not in existing and p not in pending:
            db.session.add(AssetFolder(path=p))
    return path


def all_folder_paths() -> list[str]:
    """Return every folder path (from the folder table and from the images), sorted.

    Images whose folder row is missing (e.g. created by an older version)
    still make their folder appear.

    Returns:
        Sorted list of paths, parents included.
    """
    paths = {f.path for f in AssetFolder.query.all()}
    for (cat,) in db.session.query(Asset.category).distinct():
        if cat:
            paths.update(ancestors(cat))
    return sorted(paths, key=lambda p: [s.lower() for s in p.split("/")])


def folder_tree() -> list[dict]:
    """Describe all folders for the library sidebar.

    Returns:
        Depth-first ordered list of dicts with ``path``, ``name``, ``depth``,
        ``parent``, ``count`` (images directly inside) and ``total`` (images
        in the folder and its sub-folders).
    """
    counts = dict(db.session.query(Asset.category, db.func.count(Asset.id)).group_by(Asset.category).all())
    nodes = []
    for path in all_folder_paths():
        total = sum(n for cat, n in counts.items() if is_within(cat, path))
        nodes.append({"path": path, "name": path.rsplit("/", 1)[-1], "depth": path.count("/"),
                      "parent": path.rsplit("/", 1)[0] if "/" in path else "",
                      "count": counts.get(path, 0), "total": total})
    return nodes


def subfolders(path: str) -> list[dict]:
    """Return the direct sub-folders of a folder (``""`` = top level).

    Args:
        path: Parent folder path, or ``""`` for the library root.

    Returns:
        Nodes from :func:`folder_tree` whose parent is ``path``.
    """
    return [n for n in folder_tree() if n["parent"] == path]


def rename_folder(old: str, new: str) -> str:
    """Rename or move a folder together with its sub-folders and images (no commit).

    ``new`` is a full path, so ``"HVAC"`` → ``"Animations/HVAC"`` moves the
    folder.  When the target exists already the contents are merged.

    Args:
        old: Existing folder path.
        new: New folder path.

    Returns:
        The normalised new path.

    Raises:
        FolderError: If ``old`` does not exist, ``new`` is invalid, or ``new``
            lies inside ``old``.
    """
    old = normalize_path(old, default=None)
    new = normalize_path(new, default=None)
    if old not in all_folder_paths():
        raise FolderError(gettext("Folder '{0}' does not exist.", old))
    if new == old:
        return new
    if is_within(new, old):
        raise FolderError(gettext('A folder cannot be moved into itself.'))
    existing = {f.path: f for f in AssetFolder.query.all()}
    for path, folder in existing.items():
        if is_within(path, old):
            target = new + path[len(old):]
            if target in existing:
                db.session.delete(folder)
            else:
                folder.path = target
    for asset in Asset.query.all():
        if is_within(asset.category, old):
            asset.category = new + asset.category[len(old):]
    db.session.flush()
    ensure_folder(new)
    return new


def delete_folder(path: str) -> int:
    """Delete an empty folder and its (empty) sub-folders (no commit).

    Args:
        path: Folder path.

    Returns:
        Number of folder rows removed.

    Raises:
        FolderError: If the folder or a sub-folder still contains images.
    """
    path = normalize_path(path, default=None)
    if any(is_within(a.category, path) for a in Asset.query.all()):
        raise FolderError(gettext("Folder '{0}' is not empty – move or delete its images first.", path))
    removed = 0
    for folder in AssetFolder.query.all():
        if is_within(folder.path, path):
            db.session.delete(folder)
            removed += 1
    return removed


def file_digest(data: bytes) -> str:
    """Return the SHA-256 hex digest of some bytes.

    Args:
        data: Content.

    Returns:
        Hex digest.
    """
    return hashlib.sha256(data).hexdigest()


def find_identical(data: bytes) -> Asset | None:
    """Find a library image whose file has exactly this content.

    Args:
        data: File content.

    Returns:
        The matching :class:`Asset`, or ``None``.
    """
    digest = file_digest(data)
    for asset in Asset.query.filter_by(size=len(data)).all():
        try:
            with open(os.path.join(upload_folder(), asset.filename), "rb") as fh:
                if file_digest(fh.read()) == digest:
                    return asset
        except OSError:
            continue
    return None


def store_asset(data: bytes, name: str, folder: str, user=None, reuse: bool = True) -> tuple[Asset, bool]:
    """Add an image to the library (no commit).

    Args:
        data: File content (validated by :func:`save_upload`).
        name: Display name.
        folder: Folder path (created when missing).
        user: Uploading user or ``None``.
        reuse: Return an existing image with identical content instead of
            storing a duplicate.

    Returns:
        Tuple ``(asset, created)``; ``created`` is ``False`` when an identical
        image was re-used.

    Raises:
        AssetError: If the content is not an accepted image.
        FolderError: If the folder path is invalid.
    """
    folder = normalize_path(folder)
    if reuse:
        existing = find_identical(data)
        if existing is not None:
            return existing, False
    ensure_folder(folder)
    filename, mimetype = save_upload(data)
    asset = Asset(name=(name or "image")[:100], filename=filename, mimetype=mimetype, size=len(data),
                  category=folder, uploaded_by=user)
    db.session.add(asset)
    db.session.flush()
    return asset, True


def builtin_animations() -> list[dict]:
    """Read the manifest of the shipped animated GIFs.

    Returns:
        List of dicts ``category``, ``name``, ``file`` (relative path) –
        empty when the manifest is missing.
    """
    try:
        with open(os.path.join(BUILTIN_DIR, "manifest.json"), encoding="utf-8") as fh:
            return list(json.load(fh).get("animations", []))
    except (OSError, ValueError):
        return []


def import_builtin_animations(user=None) -> dict:
    """Copy the shipped animations into the library folders ``Animations/<Category>`` (no commit).

    Safe to run again: animations already in the library (identical file,
    in any folder) are skipped, so renamed or moved copies are left alone.

    Args:
        user: User recorded as uploader, or ``None``.

    Returns:
        Dict with ``added``, ``skipped`` and ``missing`` counts and the list
        of ``folders`` used.
    """
    result = {"added": 0, "skipped": 0, "missing": 0, "folders": []}
    for item in builtin_animations():
        rel = str(item.get("file", ""))
        path = os.path.normpath(os.path.join(BUILTIN_DIR, rel))
        if not path.startswith(os.path.normpath(BUILTIN_DIR) + os.sep) or not os.path.isfile(path):
            result["missing"] += 1
            continue
        with open(path, "rb") as fh:
            data = fh.read()
        folder = normalize_path(f"{ANIMATIONS_FOLDER}/{item.get('category') or 'Misc'}")
        _asset, created = store_asset(data, str(item.get("name") or os.path.splitext(os.path.basename(rel))[0]),
                                      folder, user)
        result["added" if created else "skipped"] += 1
        if folder not in result["folders"]:
            result["folders"].append(folder)
    return result
