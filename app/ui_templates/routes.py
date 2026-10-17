"""Views of the UI templates: static files and the administration page."""

from __future__ import annotations

import os

from flask import (
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

from app.audit.recorder import record
from app.extensions import db
from app.i18n.catalog import _
from app.ui_templates import bp
from app.ui_templates.registry import (
    ALLOWED_EXTENSIONS,
    FEATURE_LABELS,
    FEATURE_VALUE_LABELS,
    FEATURES,
    MAX_ZIP_BYTES,
    PREVIEW_KEY,
    SERVED_EXTENSIONS,
    TemplateError,
    _safe_rel,
    activate,
    active_template,
    all_templates,
    configured_id,
    delete_template,
    export_zip,
    get_template,
    install_zip,
)
from app.utils.decorators import admin_required


@bp.route("/ui-templates/<tid>/<path:filename>")
def asset(tid: str, filename: str):
    """Serve a static file (CSS, JavaScript, image, font) of a UI template.

    Available without logging in (the login page uses the template too).
    Only the file types in :data:`SERVED_EXTENSIONS` are served; SVG images
    get a restrictive Content-Security-Policy so scripts inside them never run.

    Args:
        tid: Template id.
        filename: Path inside the template folder.

    Returns:
        The file, or 404.
    """
    tpl = get_template(tid)
    if tpl is None:
        abort(404)
    try:
        rel = _safe_rel(filename)
    except TemplateError:
        abort(404)
    ext = os.path.splitext(rel)[1].lower()
    if ext not in SERVED_EXTENSIONS or os.path.basename(rel) == "template.json":
        abort(404)
    response = send_from_directory(tpl.path, rel, max_age=86400)
    response.headers["X-Content-Type-Options"] = "nosniff"
    if ext == ".svg":
        response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
    return response


@bp.route("/admin/ui-templates")
@admin_required
def admin_index():
    """List the UI templates with their features; upload form.

    Returns:
        The rendered page.
    """
    return render_template("admin/ui_templates.html", templates=list(all_templates().values()),
                           active_id=configured_id(), shown=active_template(), features=FEATURES,
                           feature_labels=FEATURE_LABELS, value_labels=FEATURE_VALUE_LABELS,
                           max_mb=MAX_ZIP_BYTES // (1024 * 1024), extensions=sorted(ALLOWED_EXTENSIONS))


def _template_or_404(tid: str):
    """The template with this id, or abort with 404."""
    tpl = get_template(tid)
    if tpl is None:
        abort(404)
    return tpl


@bp.route("/admin/ui-templates/<tid>/activate", methods=["POST"])
@admin_required
def admin_activate(tid: str):
    """Make a template the active one for every user (also ends a preview).

    Args:
        tid: Template id.

    Returns:
        Redirect to the template list.
    """
    tpl = _template_or_404(tid)
    activate(tpl.id)
    db.session.commit()
    session.pop(PREVIEW_KEY, None)
    flash(_("UI template '{0}' is now active for all users.", tpl.title()), "success")
    return redirect(url_for("ui_templates.admin_index"))


@bp.route("/admin/ui-templates/<tid>/preview", methods=["POST"])
@admin_required
def admin_preview(tid: str):
    """Show a template to this administrator only (in this browser session).

    Args:
        tid: Template id.

    Returns:
        Redirect to the page given in ``next`` (the screen list by default).
    """
    tpl = _template_or_404(tid)
    session[PREVIEW_KEY] = tpl.id
    flash(_("Previewing '{0}'. Only you see it; other users keep the active template.", tpl.title()), "info")
    return redirect(url_for("main.index"))


@bp.route("/admin/ui-templates/preview/stop", methods=["POST"])
@admin_required
def admin_preview_stop():
    """End the preview (go back to the active template).

    Returns:
        Redirect to the template list.
    """
    session.pop(PREVIEW_KEY, None)
    return redirect(url_for("ui_templates.admin_index"))


@bp.route("/admin/ui-templates/upload", methods=["POST"])
@admin_required
def admin_upload():
    """Install a template package (ZIP file in ``file``; ``replace=1`` updates one).

    Returns:
        Redirect to the template list.
    """
    storage = request.files.get("file")
    if not storage or not storage.filename:
        flash(_("Choose a template package (.zip)."), "warning")
        return redirect(url_for("ui_templates.admin_index"))
    replace = request.form.get("replace") == "1"
    try:
        tpl, replaced = install_zip(storage.stream, replace=replace)
    except TemplateError as exc:
        flash(_("{0}: {1}", storage.filename, str(exc)), "danger")
        return redirect(url_for("ui_templates.admin_index"))
    record("update" if replaced else "create", "UI template", None, tpl.id,
           changes=[{"field": "version", "before": None, "after": tpl.version}])
    flash(_("UI template '{0}' ({1}) installed. Use Preview to try it, then Activate.", tpl.title(), tpl.id), "success")
    if tpl.js:
        flash(_("This template contains JavaScript that runs on every page. Install templates only from developers you trust."),
              "warning")
    return redirect(url_for("ui_templates.admin_index"))


@bp.route("/admin/ui-templates/<tid>/download")
@admin_required
def admin_download(tid: str):
    """Download a template as a ZIP package (a starting point for your own).

    Args:
        tid: Template id.

    Returns:
        ZIP attachment.
    """
    tpl = _template_or_404(tid)
    return Response(export_zip(tpl), mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="pyplchmi-template-{tpl.id}.zip"'})


@bp.route("/admin/ui-templates/<tid>/delete", methods=["POST"])
@admin_required
def admin_delete(tid: str):
    """Delete an uploaded template (the classic template becomes active if it was active).

    Args:
        tid: Template id.

    Returns:
        Redirect to the template list.
    """
    try:
        tpl = delete_template(tid)
    except TemplateError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("ui_templates.admin_index"))
    record("delete", "UI template", None, tpl.id, commit=False)
    db.session.commit()
    flash(_("UI template '{0}' deleted.", tpl.title()), "success")
    return redirect(url_for("ui_templates.admin_index"))
