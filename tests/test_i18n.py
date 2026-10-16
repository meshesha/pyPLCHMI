"""Tests for the multi-language support (JSON language files) and the SweetAlert2 integration."""

import importlib.util
import io
import json
import os

import pytest

from app.extensions import db
from app.i18n.catalog import (
    BUILTIN_DIR,
    LanguageFileError,
    available_languages,
    coverage,
    english_keys,
    gettext,
    parse_language,
    placeholders,
)
from app.models import User
from app.models.setting import set_setting


def lang_file(code="fr", name="Français", direction="ltr", **messages) -> bytes:
    """Build a language file.

    Args:
        code: Language code.
        name: Name.
        direction: ltr / rtl.
        **messages: Ignored (use the ``messages`` key of the returned doc).

    Returns:
        UTF-8 JSON bytes.
    """
    doc = {"language": {"code": code, "name": name, "english_name": "French", "direction": direction},
           "messages": {"Screens": "Écrans", "Tag '{0}' saved.": "Tag « {0} » enregistré.", "Log in": "",
                        "Delete user {0}?": "Supprimer l'utilisateur ?"}}
    return json.dumps(doc, ensure_ascii=False).encode()


def upload(client, data: bytes, name="fr.json"):
    """POST a language file to the admin upload endpoint."""
    return client.post("/admin/languages/upload", data={"file": (io.BytesIO(data), name)},
                       content_type="multipart/form-data", follow_redirects=True)


# ---------------------------------------------------------------- file parsing
def test_parse_language_cleans_messages():
    """Empty translations are skipped, broken placeholders are ignored with a warning."""
    lang, warnings = parse_language(lang_file())
    assert lang.code == "fr" and lang.direction == "ltr" and not lang.rtl
    assert lang.messages == {"Screens": "Écrans", "Tag '{0}' saved.": "Tag « {0} » enregistré."}
    assert len(warnings) == 1 and "Delete user {0}?" in warnings[0]


@pytest.mark.parametrize("raw,error", [
    (b"not json", "Not valid JSON"),
    (b"[]", '"language"'),
    (json.dumps({"language": {"code": "x!", "name": "X"}, "messages": {}}).encode(), "Invalid language code"),
    (json.dumps({"language": {"code": "xx", "name": ""}, "messages": {}}).encode(), "language.name"),
    (json.dumps({"language": {"code": "xx", "name": "X", "direction": "up"}, "messages": {}}).encode(), "direction"),
    (b"\xff\xfe\x00", "UTF-8"),
])
def test_parse_language_rejects(raw, error):
    """Unusable files raise LanguageFileError with a clear message."""
    with pytest.raises(LanguageFileError, match=error):
        parse_language(raw)


def test_placeholders():
    """Named and positional format fields are found."""
    assert placeholders("{0} of {1} – {name}") == {"0", "1", "name"}
    assert placeholders("no fields") == set()


def test_builtin_languages_complete(app):
    """English and Hebrew are shipped; Hebrew translates every application text."""
    langs = available_languages()
    assert list(langs)[0] == "en" and "he" in langs
    assert langs["he"].rtl and langs["he"].name == "עברית"
    done, total = coverage(langs["he"])
    assert total > 1000 and done == total


def test_english_catalog_up_to_date():
    """en.json lists exactly the texts found in the code (run tools/i18n_extract.py after UI changes)."""
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools", "i18n_extract.py")
    spec = importlib.util.spec_from_file_location("i18n_extract", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with open(os.path.join(BUILTIN_DIR, "en.json"), encoding="utf-8") as fh:
        current = list(json.load(fh)["messages"])
    assert current == mod.collect()


# ---------------------------------------------------------------- translation
def test_gettext_english_and_formatting(app):
    """Outside a request English is used; values fill the placeholders."""
    assert gettext("Screens") == "Screens"
    assert gettext("Tag '{0}' saved.", "T1") == "Tag 'T1' saved."
    assert gettext("Unknown text {0}", 5) == "Unknown text 5"


def test_gettext_hebrew_request(app, client, login_as, users):
    """A user's language is used for templates, forms, flashes and JSON errors."""
    users["admin"].language = "he"
    db.session.commit()
    login_as("admin")
    html = client.get("/screens/").get_data(as_text=True)
    assert 'lang="he" dir="rtl"' in html and "bootstrap.rtl.min.css" in html and "מסכים" in html
    page = client.get("/admin/users/new").get_data(as_text=True)
    assert "שם משתמש" in page  # form label
    resp = client.post("/admin/users/new", data={"username": "dana", "role_id": 2, "password": "secret-pass-1",
                                                 "confirm": "secret-pass-1", "active": "y"}, follow_redirects=True)
    assert "המשתמש &#39;dana&#39; נוצר." in resp.get_data(as_text=True)


def test_broken_translation_falls_back(app, client, tmp_path):
    """A translation that cannot be formatted shows the English text."""
    with app.test_request_context("/"):
        from app.i18n import catalog  # noqa: PLC0415

        lang = catalog.Language("xx", "X", "X", "ltr", messages={"Tag '{0}' saved.": "Tag {1}"})
        from flask import request  # noqa: PLC0415

        request.environ["pyplchmi.language"] = lang
        assert gettext("Tag '{0}' saved.", "A") == "Tag 'A' saved."


# ---------------------------------------------------------------- choosing a language
def test_language_switch_logged_in(client, login_as, users):
    """The switcher stores the choice in the account."""
    login_as("viewer")
    resp = client.post("/language", data={"code": "he", "next": "/screens/"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/screens/")
    assert db.session.get(User, users["viewer"].id).language == "he"
    client.post("/language", data={"code": ""})
    assert db.session.get(User, users["viewer"].id).language is None
    assert client.post("/language", data={"code": "zz"}).status_code == 400


def test_language_switch_unsafe_next_and_visitors(client, login_as):
    """External 'next' URLs are ignored; visitors cannot choose a language."""
    assert client.post("/language", data={"code": "he"}).status_code == 302  # to the login page
    assert 'dir="rtl"' not in client.get("/auth/login").get_data(as_text=True)
    login_as("viewer")
    resp = client.post("/language", data={"code": "he", "next": "https://evil.example/"})
    assert "evil.example" not in resp.headers["Location"]


def test_language_does_not_stick_after_logout(client, login_as, users):
    """After logout the login page uses the default language, not the last user's."""
    login_as("viewer")
    client.post("/language", data={"code": "he", "next": "/screens/"})
    assert 'lang="he"' in client.get("/screens/").get_data(as_text=True)
    client.post("/auth/logout")
    html = client.get("/auth/login").get_data(as_text=True)
    assert 'lang="en"' in html and 'dir="ltr"' in html
    with client.session_transaction() as sess:  # also with a value left by an older version
        sess["lang"] = "he"
    assert 'lang="en"' in client.get("/auth/login").get_data(as_text=True)


def test_default_language_setting(client, app):
    """Visitors without a choice get the default language."""
    set_setting("default_language", "he")
    db.session.commit()
    html = client.get("/auth/login").get_data(as_text=True)
    assert 'lang="he"' in html and "התחברות" in html


def test_messages_js(client):
    """The browser catalog is JavaScript, cached, and empty for English."""
    he = client.get("/i18n/he.js")
    assert he.status_code == 200 and he.mimetype == "application/javascript"
    assert "window.I18N" in he.get_data(as_text=True) and "max-age" in he.headers["Cache-Control"]
    body = json.loads(he.get_data(as_text=True).split("=", 1)[1].rstrip().rstrip(";"))
    assert body["dir"] == "rtl" and body["messages"]["Screens"] == "מסכים"
    en = json.loads(client.get("/i18n/en.js").get_data(as_text=True).split("=", 1)[1].rstrip().rstrip(";"))
    assert en["messages"] == {}
    assert client.get("/i18n/zz.js").status_code == 404


# ---------------------------------------------------------------- administration
def test_admin_languages_page(client, login_as):
    """Admins see the languages with coverage and the upload form."""
    login_as("admin")
    html = client.get("/admin/languages").get_data(as_text=True)
    assert 'data-lang="en"' in html and 'data-lang="he"' in html and 'id="language-upload"' in html


def test_admin_upload_override_and_delete(client, login_as, app):
    """Uploading adds or replaces a language; deleting the upload restores the built-in file."""
    login_as("admin")
    html = upload(client, lang_file()).get_data(as_text=True)
    assert "Français" in html and "ignored" in html
    assert "fr" in available_languages()
    assert os.path.isfile(os.path.join(app.config["LANGUAGES_FOLDER"], "fr.json"))
    # override Hebrew with a tiny file
    upload(client, lang_file(code="he", name="עברית", direction="rtl"))
    he = available_languages()["he"]
    assert he.source == "uploaded" and he.overrides_builtin and coverage(he)[0] < 10
    client.post("/admin/languages/he/delete")
    assert available_languages()["he"].source == "built-in"
    html = client.post("/admin/languages/en/delete", follow_redirects=True).get_data(as_text=True)
    assert "Only uploaded language files can be deleted." in html


def test_admin_upload_invalid(client, login_as):
    """A broken file is reported and not stored."""
    login_as("admin")
    html = upload(client, b"{broken", "x.json").get_data(as_text=True)
    assert "Not valid JSON" in html and list(available_languages()) == ["en", "he"]


def test_admin_download(client, login_as):
    """Language files and translation templates can be downloaded."""
    login_as("admin")
    resp = client.get("/admin/languages/he/download")
    doc = json.loads(resp.data)
    assert resp.headers["Content-Disposition"].endswith('pyplchmi-he.json"') and doc["language"]["code"] == "he"
    tpl = json.loads(client.get("/admin/languages/en/download?template=1").data)
    assert tpl["language"]["code"] == "xx" and set(tpl["messages"]) == set(english_keys())
    assert all(v == "" for v in tpl["messages"].values())
    en = json.loads(client.get("/admin/languages/en/download").data)
    assert en["messages"]["Screens"] == "Screens"


def test_admin_default_language(client, login_as):
    """The default language is saved; unknown codes are refused."""
    login_as("admin")
    client.post("/admin/languages/default", data={"code": "he"})
    from app.models.setting import get_setting  # noqa: PLC0415

    assert get_setting("default_language") == "he"
    assert client.post("/admin/languages/default", data={"code": "zz"}).status_code == 400


def test_language_admin_requires_admin(client, login_as):
    """Viewers cannot manage languages."""
    login_as("viewer")
    assert client.get("/admin/languages").status_code == 403
    assert upload(client, lang_file()).status_code == 403


def test_user_form_language(client, login_as, users):
    """Administrators can set a user's language."""
    login_as("admin")
    uid = users["viewer"].id
    client.post(f"/admin/users/{uid}/edit", data={"username": "viewer", "role_id": users["viewer"].role_id,
                                                  "active": "y", "language": "he"})
    assert db.session.get(User, uid).language == "he"
    html = client.get("/admin/users").get_data(as_text=True)
    assert "עברית" in html


# ---------------------------------------------------------------- SweetAlert2
def test_dialog_assets_and_data_confirm(client, login_as):
    """Pages load SweetAlert2 and confirmations use data-confirm instead of confirm()."""
    login_as("admin")
    html = client.get("/admin/users").get_data(as_text=True)
    assert "sweetalert2.min.js" in html and "hmi-dialogs.js" in html
    assert 'data-confirm="Delete user' in html and "return confirm(" not in html


def test_flash_messages_rendered_for_toast(client, login_as):
    """Flash messages are rendered in #hmi-flashes (shown as a SweetAlert2 toast)."""
    login_as("admin")
    html = client.post("/admin/settings", data={"public_home_screen_id": 0}, follow_redirects=True).get_data(
        as_text=True)
    assert 'id="hmi-flashes"' in html and 'data-flash="success"' in html


def test_static_vendor_files(client):
    """The vendored SweetAlert2 and Bootstrap RTL files are served."""
    for path in ("/static/vendor/sweetalert2/sweetalert2.min.js", "/static/vendor/sweetalert2/sweetalert2.min.css",
                 "/static/vendor/bootstrap/bootstrap.rtl.min.css", "/static/js/hmi-dialogs.js"):
        assert client.get(path).status_code == 200
