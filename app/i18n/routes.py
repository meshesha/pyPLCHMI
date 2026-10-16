"""Views of the language support: switcher, JavaScript catalog, administration."""

from __future__ import annotations

import json

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.i18n.catalog import (
    DEFAULT_LANGUAGE,
    LanguageFileError,
    _,
    available_languages,
    coverage,
    default_language,
    delete_uploaded,
    english_keys,
    language_document,
    save_uploaded,
)
from app.models.setting import set_setting
from app.utils.decorators import admin_required

bp = Blueprint("i18n", __name__)
from app.utils.security import is_safe_redirect


def _back(default_endpoint: str = "main.index"):
    """Redirect to the ``next`` URL of the form (same site only) or a default page.

    Args:
        default_endpoint: Endpoint used when ``next`` is missing or unsafe.

    Returns:
        A redirect response.
    """
    target = request.form.get("next") or request.args.get("next") or request.referrer
    if target and is_safe_redirect(target):
        return redirect(target)
    return redirect(url_for(default_endpoint))


@bp.route("/language", methods=["POST"])
@login_required
def set_language():
    """Choose the user interface language of the logged-in user (form field ``code``).

    The choice is stored in the account only (``code`` empty = the default
    language).  Visitors who are not logged in always see the default
    language set in *Admin → Languages*, so nothing is kept in the browser
    session – a language chosen before logging out must not stick to the
    login page.

    Returns:
        Redirect back to the page the form was sent from.
    """
    code = (request.form.get("code") or "").strip()
    langs = available_languages()
    if code and code not in langs:
        abort(400)
    current_user.language = code or None
    db.session.commit()
    session.pop("lang", None)  # left over from older versions
    return _back()


@bp.route("/i18n/<code>.js")
def messages_js(code: str):
    """The messages of a language for the browser (``window.I18N``).

    Cached by the browser; the page links it with ``?v=<version>`` so a new
    file is loaded after an upload.

    Args:
        code: Language code.

    Returns:
        JavaScript response.
    """
    lang = available_languages().get(code)
    if lang is None:
        abort(404)
    messages = {} if code == DEFAULT_LANGUAGE else lang.messages
    payload = json.dumps({"code": lang.code, "dir": lang.direction, "messages": messages}, ensure_ascii=False)
    payload = payload.replace("</", "<\\/")
    resp = Response(f"window.I18N = {payload};\n", mimetype="application/javascript")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@bp.route("/admin/languages")
@admin_required
def admin_languages():
    """List the languages with their coverage; upload form.

    Returns:
        The rendered page.
    """
    langs = available_languages()
    rows = [(lang, *coverage(lang)) for lang in langs.values()]
    return render_template("admin/languages.html", rows=rows, default=default_language(),
                           total_keys=len(english_keys()))


@bp.route("/admin/languages/upload", methods=["POST"])
@admin_required
def admin_language_upload():
    """Upload one or more language files (``file``).

    Returns:
        Redirect to the language list.
    """
    files = [f for f in request.files.getlist("file") if f and f.filename]
    if not files:
        flash(_("Choose a language file (.json)."), "warning")
    for storage in files:
        try:
            lang, warnings = save_uploaded(storage.read())
        except LanguageFileError as exc:
            flash(_("{0}: {1}", storage.filename, _(str(exc))), "danger")
            continue
        done, total = coverage(lang)
        flash(_("Language '{0}' ({1}) saved: {2} of {3} texts translated.", lang.name, lang.code, done, total),
              "success")
        if warnings:
            more = _(" (and {0} more)", len(warnings) - 5) if len(warnings) > 5 else ""
            flash(_("{0} message(s) ignored: {1}", len(warnings), "; ".join(warnings[:5])) + more, "warning")
    return redirect(url_for("i18n.admin_languages"))


@bp.route("/admin/languages/<code>/download")
@admin_required
def admin_language_download(code: str):
    """Download a language file (``?template=1``: translation template).

    Args:
        code: Language code.

    Returns:
        JSON attachment.
    """
    lang = available_languages().get(code)
    if lang is None:
        abort(404)
    template = request.args.get("template") == "1"
    doc = language_document(lang, template=template)
    if code == DEFAULT_LANGUAGE and not template:
        doc["messages"] = {k: k for k in english_keys()}
    name = f"pyplchmi-{'template' if template and code == DEFAULT_LANGUAGE else code}.json"
    body = json.dumps(doc, ensure_ascii=False, indent=1)
    return Response(body, mimetype="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@bp.route("/admin/languages/<code>/delete", methods=["POST"])
@admin_required
def admin_language_delete(code: str):
    """Delete an uploaded language file (a built-in file with the same code is used again).

    Args:
        code: Language code.

    Returns:
        Redirect to the language list.
    """
    if delete_uploaded(code):
        flash(_("Uploaded language file '{0}' deleted.", code), "success")
    else:
        flash(_("Only uploaded language files can be deleted."), "warning")
    return redirect(url_for("i18n.admin_languages"))


@bp.route("/admin/languages/default", methods=["POST"])
@admin_required
def admin_language_default():
    """Set the default language (visitors and users without a choice).

    Returns:
        Redirect to the language list.
    """
    code = request.form.get("code") or DEFAULT_LANGUAGE
    if code not in available_languages():
        abort(400)
    set_setting("default_language", code)
    db.session.commit()
    flash(_("Default language saved."), "success")
    return redirect(url_for("i18n.admin_languages"))
