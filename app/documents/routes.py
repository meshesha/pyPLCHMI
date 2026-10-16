"""Information library pages and JSON API.

* Viewing the library and opening files: every logged-in user.
* Uploading, renaming, deleting and attaching files to PLCs: ``manage_plcs``.

Uploads are checked by SHA-1: the browser hashes the file first and asks
``/library/api/check``; the server checks again when the file arrives.  A
duplicate is never stored – the response names the existing file (HTTP 409)
so the user can choose it instead or cancel.
"""

from __future__ import annotations

import os

from flask import abort, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from app.documents import bp
from app.documents.storage import (
    DuplicateFile,
    LibraryError,
    delete_file,
    find_by_sha1,
    library_folder,
    max_bytes,
    remove_content,
    store_file,
    valid_sha1,
)
from app.extensions import db
from app.i18n.catalog import gettext
from app.models import PLC, InfoFile, Perm
from app.utils.decorators import permission_required
from app.utils.timezones import format_local, user_zone


def _can_manage() -> bool:
    """Whether the current user may change the library."""
    return current_user.is_authenticated and current_user.can(Perm.MANAGE_PLCS)


def _search(query, text: str):
    """Filter files by name, original name, description or hash.

    Args:
        query: Base query.
        text: Search text.

    Returns:
        The filtered query.
    """
    if not text:
        return query
    like = f"%{text}%"
    return query.filter(or_(InfoFile.name.ilike(like), InfoFile.original_name.ilike(like),
                            InfoFile.description.ilike(like), InfoFile.sha1.ilike(like)))


@bp.route("/")
@login_required
def index():
    """The information library: all files with search, kind filter and upload.

    Returns:
        The rendered page.
    """
    q = request.args.get("q", "").strip()[:100]
    kind = request.args.get("kind", "")
    query = _search(InfoFile.query, q)
    if kind == "pdf":
        query = query.filter(InfoFile.mimetype == "application/pdf")
    elif kind == "image":
        query = query.filter(InfoFile.mimetype != "application/pdf")
    files = query.order_by(InfoFile.name).all()
    return render_template("documents/index.html", files=files, q=q, kind=kind, can_manage=_can_manage(),
                           total=InfoFile.query.count(), max_mb=max_bytes() // (1024 * 1024),
                           format_local=format_local, zone=user_zone())


@bp.route("/<int:file_id>/file")
@login_required
def file(file_id: int):
    """Open (or with ``?download=1`` download) a library file.

    Args:
        file_id: File id.

    Returns:
        The file.
    """
    item = db.get_or_404(InfoFile, file_id)
    path = os.path.join(library_folder(), item.filename)
    if not os.path.isfile(path):
        abort(404)
    download = bool(request.args.get("download"))
    ext = item.extension
    name = item.original_name if item.original_name.lower().endswith("." + ext) else f"{item.name}.{ext}"
    resp = send_file(path, mimetype=item.mimetype, as_attachment=download, download_name=name, max_age=3600)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@bp.route("/<int:file_id>/edit", methods=["POST"])
@permission_required(Perm.MANAGE_PLCS)
def edit(file_id: int):
    """Rename a file / change its description (form fields ``name``, ``description``).

    Args:
        file_id: File id.

    Returns:
        Redirect to the library.
    """
    item = db.get_or_404(InfoFile, file_id)
    name = (request.form.get("name") or "").strip()[:200]
    if not name:
        flash(gettext("Enter a name."), "danger")
    else:
        item.name = name
        item.description = (request.form.get("description") or "").strip()[:500]
        db.session.commit()
        flash(gettext("File '{0}' saved.", item.name), "success")
    return redirect(request.referrer or url_for("documents.index"))


@bp.route("/<int:file_id>/delete", methods=["POST"])
@permission_required(Perm.MANAGE_PLCS)
def delete(file_id: int):
    """Delete a file from the library (it is detached from all PLCs).

    Args:
        file_id: File id.

    Returns:
        Redirect to the library.
    """
    item = db.get_or_404(InfoFile, file_id)
    name = item.name
    path = delete_file(item)
    db.session.commit()
    remove_content(path)
    flash(gettext("File '{0}' deleted.", name), "success")
    return redirect(url_for("documents.index"))


# ---------------------------------------------------------------------------
# JSON API (library page and PLC page)
# ---------------------------------------------------------------------------
@bp.route("/api/files")
@login_required
def api_files():
    """Library files for the "Add from library" picker (``?q=`` search, at most 500).

    Returns:
        JSON ``{"files": [...]}``.
    """
    query = _search(InfoFile.query, request.args.get("q", "").strip()[:100])
    return jsonify(files=[f.to_dict() for f in query.order_by(InfoFile.name).limit(500)])


@bp.route("/api/check")
@permission_required(Perm.MANAGE_PLCS)
def api_check():
    """Is a file with this SHA-1 already in the library? (``?sha1=``)

    Returns:
        JSON ``{"exists": bool, "file": {...} | null}``.
    """
    sha1 = valid_sha1(request.args.get("sha1"))
    if sha1 is None:
        return jsonify(error=gettext("Invalid SHA-1.")), 400
    existing = find_by_sha1(sha1)
    return jsonify(exists=existing is not None, file=existing.to_dict() if existing else None)


@bp.route("/api/files", methods=["POST"])
@permission_required(Perm.MANAGE_PLCS)
def api_upload():
    """Upload one file (multipart field ``file``; optional ``name``, ``description``).

    Returns:
        201 with the new file, 409 with the existing file when the content is
        already in the library (nothing is stored), or 400.
    """
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify(error=gettext("Choose a file.")), 400
    data = upload.read(max_bytes() + 1)
    try:
        item = store_file(data, upload.filename, current_user, request.form.get("name") or None,
                          request.form.get("description") or "")
        db.session.commit()
    except DuplicateFile as dup:
        db.session.rollback()
        return jsonify(error=gettext("This file is already in the library as '{0}'.", dup.existing.name),
                       duplicate=dup.existing.to_dict()), 409
    except LibraryError as exc:
        db.session.rollback()
        return jsonify(error=str(exc)), 400
    return jsonify(file=item.to_dict()), 201


def _plc_files_json(plc: PLC) -> dict:
    """The files of a PLC as JSON.

    Args:
        plc: The PLC.

    Returns:
        ``{"plc": id, "files": [...]}``.
    """
    return {"plc": plc.id, "files": [f.to_dict() for f in plc.files]}


@bp.route("/api/plcs/<int:plc_id>/files")
@login_required
def api_plc_files(plc_id: int):
    """Files attached to a PLC.

    Args:
        plc_id: PLC id.

    Returns:
        JSON list.
    """
    return jsonify(_plc_files_json(db.get_or_404(PLC, plc_id)))


@bp.route("/api/plcs/<int:plc_id>/files", methods=["POST"])
@permission_required(Perm.MANAGE_PLCS)
def api_attach(plc_id: int):
    """Attach a library file to a PLC (JSON ``{"file_id": n}``).

    Args:
        plc_id: PLC id.

    Returns:
        JSON list of the PLC's files.
    """
    plc = db.get_or_404(PLC, plc_id)
    payload = request.get_json(silent=True) or {}
    try:
        item = db.session.get(InfoFile, int(payload.get("file_id")))
    except (TypeError, ValueError):
        item = None
    if item is None:
        return jsonify(error=gettext("Unknown file.")), 404
    if item not in plc.files:
        plc.files.append(item)
        db.session.commit()
    return jsonify(_plc_files_json(plc))


@bp.route("/api/plcs/<int:plc_id>/files/<int:file_id>", methods=["DELETE"])
@permission_required(Perm.MANAGE_PLCS)
def api_detach(plc_id: int, file_id: int):
    """Remove a file from a PLC (the file stays in the library).

    Args:
        plc_id: PLC id.
        file_id: File id.

    Returns:
        JSON list of the PLC's files.
    """
    plc = db.get_or_404(PLC, plc_id)
    item = db.session.get(InfoFile, file_id)
    if item is not None and item in plc.files:
        plc.files.remove(item)
        db.session.commit()
    return jsonify(_plc_files_json(plc))
