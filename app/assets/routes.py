"""Image library views: folders, upload, move, file serving, delete, built-in animations."""

import os

from flask import (
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_login import current_user

from app.assets import bp
from app.assets.folders import (
    DEFAULT_FOLDER,
    FolderError,
    all_folder_paths,
    builtin_animations,
    delete_folder,
    ensure_folder,
    folder_tree,
    import_builtin_animations,
    normalize_path,
    rename_folder,
    store_asset,
    subfolders,
)
from app.assets.storage import AssetError, delete_file, upload_folder
from app.dashboards.layout import referenced_assets
from app.extensions import db, login_manager
from app.models import Asset, Dashboard, Perm
from app.utils.decorators import permission_required
from app.i18n.catalog import gettext


def _wants_json() -> bool:
    """Tell whether the client expects a JSON answer (AJAX upload from the designer).

    Returns:
        ``True`` when the ``Accept`` header prefers JSON.
    """
    return request.accept_mimetypes.best == "application/json"


def asset_usage() -> dict:
    """Map every asset id to the image dashboards that use it.

    Returns:
        Dict ``{asset_id: [Dashboard, ...]}``.
    """
    usage: dict = {}
    for dash in Dashboard.query.filter_by(kind="image").all():
        for asset_id in referenced_assets(dash.layout_data):
            usage.setdefault(asset_id, []).append(dash)
    return usage


def _back(folder: str | None = None):
    """Redirect to the library, opened at ``folder``.

    Args:
        folder: Folder path, or ``None`` for the folder given in the form.

    Returns:
        A redirect response.
    """
    if folder is None:
        folder = request.form.get("back") or request.args.get("folder") or ""
    return redirect(url_for("assets.index", folder=folder) if folder else url_for("assets.index"))


@bp.route("/")
@permission_required(Perm.MANAGE_DASHBOARDS)
def index():
    """Show the image library: folder tree, current folder and its images.

    Query string: ``folder`` (path; empty = library root showing the
    top-level folders) and ``q`` (search image names in all folders).

    Returns:
        The rendered library page.
    """
    q = (request.args.get("q") or "").strip()
    try:
        folder = normalize_path(request.args.get("folder"), default="")
    except FolderError:
        folder = ""
    paths = all_folder_paths()
    if folder and folder not in paths:
        flash(gettext("Folder '{0}' does not exist.", folder), "warning")
        return redirect(url_for("assets.index"))
    if q:
        assets = Asset.query.filter(Asset.name.ilike(f"%{q}%")).order_by(Asset.category, Asset.name).all()
    elif folder:
        assets = Asset.query.filter_by(category=folder).order_by(Asset.name).all()
    else:
        assets = []
    crumbs = []
    if folder:
        parts = folder.split("/")
        crumbs = [("/".join(parts[: i + 1]), part) for i, part in enumerate(parts)]
    builtin = builtin_animations()
    return render_template(
        "assets/index.html", assets=assets, usage=asset_usage(), folder=folder, q=q, crumbs=crumbs,
        tree=folder_tree(), children=[] if q else subfolders(folder), folders=paths,
        total=Asset.query.count(), builtin_count=len(builtin),
        builtin_categories=sorted({str(a.get("category")) for a in builtin}),
    )


@bp.route("/list")
@permission_required(Perm.MANAGE_DASHBOARDS)
def list_json():
    """List all assets as JSON (used by the designer palette).

    Returns:
        JSON list of :meth:`Asset.to_dict` objects.
    """
    return jsonify([a.to_dict() for a in Asset.query.order_by(Asset.category, Asset.name)])


@bp.route("/upload", methods=["POST"])
@permission_required(Perm.MANAGE_DASHBOARDS)
def upload():
    """Upload one or more image files into a library folder.

    Form fields: ``file`` (one or many), optional ``name`` and ``folder``
    (path, created when missing; ``category`` is accepted as an older
    alias).  Answers JSON (``{"assets": [...]}``) for AJAX callers, otherwise
    redirects back to the folder with a flash message.

    Returns:
        JSON or a redirect.
    """
    files = [f for f in request.files.getlist("file") if f and f.filename]
    created, errors = [], []
    try:
        folder = normalize_path(request.form.get("folder") or request.form.get("category"))
    except FolderError as exc:
        folder, files = DEFAULT_FOLDER, []
        errors.append(str(exc))
    for storage in files:
        default_name = os.path.splitext(os.path.basename(storage.filename))[0]
        name = (request.form.get("name") or "").strip() if len(files) == 1 else ""
        try:
            asset, _new = store_asset(storage.read(), (name or default_name)[:100] or "image", folder,
                                      current_user, reuse=False)
        except AssetError as exc:
            errors.append(gettext('{0}: {1}', storage.filename, exc))
            continue
        asset.size = os.path.getsize(os.path.join(upload_folder(), asset.filename))
        created.append(asset)
    if not files and not errors:
        errors.append(gettext('No file selected.'))
    db.session.commit()
    if _wants_json():
        status = 200 if created else 400
        return jsonify(assets=[a.to_dict() for a in created], errors=errors), status
    for err in errors:
        flash(err, "danger")
    if created:
        flash(gettext('{0} image(s) uploaded to {1}.', len(created), folder), "success")
    return _back(folder)


@bp.route("/folders/create", methods=["POST"])
@permission_required(Perm.MANAGE_DASHBOARDS)
def create_folder():
    """Create a folder (form fields ``parent`` and ``name``; ``/`` in the name creates several levels).

    Returns:
        Redirect to the new folder.
    """
    parent = request.form.get("parent") or ""
    name = request.form.get("name") or ""
    try:
        normalize_path(name, default=None)
        path = ensure_folder(f"{parent}/{name}" if parent else name)
    except FolderError as exc:
        flash(str(exc), "danger")
        return _back(parent)
    db.session.commit()
    flash(gettext("Folder '{0}' ready.", path), "success")
    return _back(path)


@bp.route("/folders/rename", methods=["POST"])
@permission_required(Perm.MANAGE_DASHBOARDS)
def rename_folder_view():
    """Rename or move a folder (form fields ``path`` and ``new_path``).

    The sub-folders and images move along; a folder with the new path that
    exists already is merged.

    Returns:
        Redirect to the renamed folder.
    """
    old = request.form.get("path") or ""
    try:
        new = rename_folder(old, request.form.get("new_path") or "")
    except FolderError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return _back(old)
    db.session.commit()
    flash(gettext("Folder '{0}' is now '{1}'.", old, new), "success")
    return _back(new)


@bp.route("/folders/delete", methods=["POST"])
@permission_required(Perm.MANAGE_DASHBOARDS)
def delete_folder_view():
    """Delete an empty folder (form field ``path``) together with its empty sub-folders.

    Returns:
        Redirect to the parent folder.
    """
    path = request.form.get("path") or ""
    try:
        delete_folder(path)
    except FolderError as exc:
        flash(str(exc), "danger")
        return _back(path)
    db.session.commit()
    flash(gettext("Folder '{0}' deleted.", path), "success")
    return _back(path.rsplit("/", 1)[0] if "/" in path else "")


@bp.route("/move", methods=["POST"])
@permission_required(Perm.MANAGE_DASHBOARDS)
def move():
    """Move images to another folder.

    Form fields: ``asset_ids`` (one or many) and ``folder`` (target path,
    created when missing).  Answers JSON for AJAX callers (drag and drop).

    Returns:
        JSON or a redirect back to the current folder.
    """
    ids = [int(i) for i in request.form.getlist("asset_ids") if str(i).isdigit()]
    try:
        target = ensure_folder(normalize_path(request.form.get("folder"), default=None))
    except FolderError as exc:
        if _wants_json():
            return jsonify(error=str(exc)), 400
        flash(str(exc), "danger")
        return _back()
    assets = Asset.query.filter(Asset.id.in_(ids)).all() if ids else []
    for asset in assets:
        asset.category = target
    db.session.commit()
    if _wants_json():
        return jsonify(ok=True, moved=len(assets), folder=target)
    if assets:
        flash(gettext('{0} image(s) moved to {1}.', len(assets), target), "success")
    else:
        flash(gettext('Select the images to move first.'), "warning")
    return _back()


@bp.route("/animations", methods=["POST"])
@permission_required(Perm.MANAGE_DASHBOARDS)
def add_animations():
    """Add the built-in animated GIFs to the folders ``Animations/<Category>``.

    Animations already in the library are skipped, so the button can be
    used again after an update to get only the new ones.

    Returns:
        Redirect to the ``Animations`` folder.
    """
    result = import_builtin_animations(current_user)
    db.session.commit()
    if result["added"]:
        flash(gettext('{0} animation(s) added to Animations ({1} already in the library).', result['added'], result['skipped']),
              "success")
    elif result["skipped"]:
        flash(gettext('All built-in animations are already in the library.'), "info")
    else:
        flash(gettext('No built-in animations found (app/library/animations is missing).'), "warning")
    return _back("Animations" if "Animations" in all_folder_paths() else "")


def asset_is_public(asset_id: int) -> bool:
    """Tell whether an image appears on a screen that visitors may open.

    Args:
        asset_id: Asset id.

    Returns:
        ``True`` if a public Image screen (visible to anonymous visitors)
        uses the asset as an element or background.
    """
    for screen in Dashboard.query.filter_by(kind="image", is_public=True).all():
        if current_user.can_view_dashboard(screen) and asset_id in referenced_assets(screen.layout_data):
            return True
    return False


@bp.route("/<int:asset_id>/file")
def file(asset_id: int):
    """Serve an asset's file.

    Logged-in users may load any asset.  Visitors who are not logged in may
    only load images used by public screens (while public access is on).
    A strict ``Content-Security-Policy`` with ``sandbox`` is added so that an
    SVG opened directly in the browser can never run scripts.

    Args:
        asset_id: Asset id.

    Returns:
        The file response, or a redirect to the login page.
    """
    asset = db.get_or_404(Asset, asset_id)
    if not current_user.is_authenticated and not asset_is_public(asset.id):
        return login_manager.unauthorized()
    resp = send_from_directory(upload_folder(), asset.filename, mimetype=asset.mimetype, max_age=3600)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"
    return resp


@bp.route("/<int:asset_id>/rename", methods=["POST"])
@permission_required(Perm.MANAGE_DASHBOARDS)
def rename(asset_id: int):
    """Change an image's display name and, optionally, its folder (``folder`` or ``category`` field).

    Args:
        asset_id: Asset id.

    Returns:
        Redirect to the library.
    """
    asset = db.get_or_404(Asset, asset_id)
    asset.name = (request.form.get("name") or asset.name).strip()[:100] or asset.name
    new_folder = request.form.get("folder") or request.form.get("category")
    if new_folder:
        try:
            asset.category = ensure_folder(new_folder)
        except FolderError as exc:
            flash(str(exc), "danger")
            return _back()
    db.session.commit()
    flash(gettext('Image updated.'), "success")
    return _back(asset.category if request.form.get("back") is None else None)


@bp.route("/<int:asset_id>/delete", methods=["POST"])
@permission_required(Perm.MANAGE_DASHBOARDS)
def delete(asset_id: int):
    """Delete an asset that no dashboard uses any more.

    Args:
        asset_id: Asset id.

    Returns:
        Redirect to the library (or 409 JSON for AJAX when still in use).
    """
    asset = db.get_or_404(Asset, asset_id)
    users = asset_usage().get(asset.id, [])
    if users:
        names = ", ".join(d.name for d in users)
        if _wants_json():
            return jsonify(error=gettext('Image is used by: {0}', names)), 409
        flash(gettext("'{0}' is still used by: {1}.", asset.name, names), "danger")
        return _back(asset.category)
    delete_file(asset.filename)
    db.session.delete(asset)
    db.session.commit()
    if _wants_json():
        return jsonify(ok=True)
    flash(gettext("'{0}' deleted.", asset.name), "success")
    return _back(asset.category)


@bp.app_template_global()
def asset_url(asset_id) -> str:
    """Template helper returning the file URL of an asset id.

    Args:
        asset_id: Asset id (may be ``None``).

    Returns:
        URL string, or ``""`` for ``None``.
    """
    if not asset_id:
        return ""
    return url_for("assets.file", asset_id=asset_id)

