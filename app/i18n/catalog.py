"""Language files (JSON catalogs) and the translation functions.

Every language is one JSON file::

    {
      "language": {"code": "he", "name": "עברית", "english_name": "Hebrew", "direction": "rtl"},
      "messages": {"Screens": "מסכים", "Tag '{0}' saved.": "התג '{0}' נשמר.", ...}
    }

Keys are the **English texts** of the user interface (``en.json`` lists them
all); a message missing from a language simply shows in English.  Texts with
values use ``str.format`` placeholders (``{0}``, ``{name}``) that every
translation must keep.

Built-in languages live in ``app/i18n/languages``; files uploaded by an
administrator are stored in ``LANGUAGES_FOLDER`` (default
``instance/languages``) and override a built-in file with the same code.
English is the default language unless an administrator chooses another
default (setting ``default_language``).
"""

from __future__ import annotations

import json
import os
import re
import string
import threading
from dataclasses import dataclass, field

from flask import current_app, has_request_context, request

#: Code of the primary language (its texts are the message keys).
DEFAULT_LANGUAGE = "en"
#: Folder of the language files shipped with the application.
BUILTIN_DIR = os.path.join(os.path.dirname(__file__), "languages")
#: Accepted language codes: ``he``, ``en``, ``pt-BR``, ``zh-Hans``…
CODE_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
#: Largest accepted language file.
MAX_FILE_BYTES = 2 * 1024 * 1024
#: Largest number of messages in one file.
MAX_MESSAGES = 20000

_cache: dict = {}
_cache_lock = threading.Lock()


class LanguageFileError(ValueError):
    """Raised for language files that cannot be used."""


@dataclass
class Language:
    """One loaded language.

    Attributes:
        code: Language code (``en``, ``he``…).
        name: Name in the language itself (``עברית``).
        english_name: Name in English (``Hebrew``).
        direction: ``ltr`` or ``rtl``.
        messages: English text → translation.
        source: ``built-in`` or ``uploaded``.
        path: File the language was read from.
        version: Changes whenever the file changes (cache busting for the JS catalog).
        overrides_builtin: An uploaded file replaces a built-in one with the same code.
    """

    code: str
    name: str
    english_name: str
    direction: str
    messages: dict = field(default_factory=dict)
    source: str = "built-in"
    path: str = ""
    version: str = ""
    overrides_builtin: bool = False

    @property
    def rtl(self) -> bool:
        """``True`` for right-to-left languages (Hebrew, Arabic…)."""
        return self.direction == "rtl"

    def meta(self) -> dict:
        """The ``language`` block of the JSON file.

        Returns:
            Dict with ``code``, ``name``, ``english_name`` and ``direction``.
        """
        return {"code": self.code, "name": self.name, "english_name": self.english_name, "direction": self.direction}


#: The primary language (its texts are the keys, so it needs no messages).
_ENGLISH = Language(DEFAULT_LANGUAGE, "English", "English", "ltr")


def uploads_dir() -> str:
    """Folder for uploaded language files (created on demand).

    Returns:
        ``LANGUAGES_FOLDER`` or ``<instance>/languages``.
    """
    folder = current_app.config.get("LANGUAGES_FOLDER") or os.path.join(current_app.instance_path, "languages")
    os.makedirs(folder, exist_ok=True)
    return folder


def placeholders(text: str) -> set:
    """Names of the ``str.format`` fields in a text.

    Args:
        text: Message.

    Returns:
        Set such as ``{"0", "name"}`` (empty when the text has none or is not a valid format string).
    """
    try:
        return {f.split(".")[0].split("[")[0] for _, f, _, _ in string.Formatter().parse(text) if f is not None}
    except ValueError:
        return set()


def parse_language(raw: bytes | str, source: str = "uploaded", path: str = "") -> tuple[Language, list]:
    """Validate the content of a language file.

    Args:
        raw: File content (UTF-8 JSON).
        source: ``built-in`` or ``uploaded``.
        path: File path (informational).

    Returns:
        Tuple ``(language, warnings)``.  Messages whose translation is empty,
        not text, or does not keep the placeholders of the English text are
        left out (and reported in ``warnings``).

    Raises:
        LanguageFileError: If the file is not usable.
    """
    if isinstance(raw, bytes):
        if len(raw) > MAX_FILE_BYTES:
            raise LanguageFileError(f"File too large (max {MAX_FILE_BYTES // 1024 // 1024} MB).")
        try:
            raw = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise LanguageFileError("The file is not UTF-8 text.") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise LanguageFileError(f"Not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("language"), dict) \
            or not isinstance(data.get("messages"), dict):
        raise LanguageFileError('The file needs a "language" object and a "messages" object.')
    meta = data["language"]
    code = str(meta.get("code") or "").strip()
    if not CODE_RE.match(code):
        raise LanguageFileError(f"Invalid language code '{code}' (use e.g. he, fr, pt-BR).")
    name = str(meta.get("name") or "").strip()[:60]
    if not name:
        raise LanguageFileError('"language.name" is required (the name of the language in itself, e.g. עברית).')
    direction = str(meta.get("direction") or "ltr").strip().lower()
    if direction not in ("ltr", "rtl"):
        raise LanguageFileError('"language.direction" must be "ltr" or "rtl".')
    messages = data["messages"]
    if len(messages) > MAX_MESSAGES:
        raise LanguageFileError(f"Too many messages (max {MAX_MESSAGES}).")
    clean, warnings = {}, []
    for key, value in messages.items():
        if not isinstance(value, str) or not isinstance(key, str):
            warnings.append(f"'{str(key)[:60]}': the translation is not text – ignored")
            continue
        if not value.strip():
            continue  # untranslated: English is shown
        if placeholders(key) != placeholders(value):
            warnings.append(f"'{key[:60]}': the translation must keep the placeholders "
                            f"{', '.join('{' + p + '}' for p in sorted(placeholders(key))) or '(none)'} – ignored")
            continue
        clean[key] = value
    lang = Language(code=code, name=name, english_name=str(meta.get("english_name") or name).strip()[:60],
                    direction=direction, messages=clean, source=source, path=path)
    return lang, warnings


def _load_file(path: str, source: str) -> Language | None:
    """Read one language file, using a cache keyed by modification time.

    Args:
        path: File path.
        source: ``built-in`` or ``uploaded``.

    Returns:
        The language, or ``None`` if the file is broken (a warning is logged).
    """
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key = (path, stat.st_mtime_ns, stat.st_size)
    with _cache_lock:
        cached = _cache.get(path)
        if cached and cached[0] == key:
            return cached[1]
    try:
        with open(path, "rb") as fh:
            lang, _ = parse_language(fh.read(), source=source, path=path)
    except (OSError, LanguageFileError) as exc:
        current_app.logger.warning("Language file %s ignored: %s", path, exc)
        lang = None
    if lang is not None:
        lang.version = f"{stat.st_mtime_ns:x}{stat.st_size:x}"
    with _cache_lock:
        _cache[path] = (key, lang)
    return lang


def _scan(folder: str, source: str) -> dict:
    """Load every ``*.json`` language file of a folder.

    Args:
        folder: Folder.
        source: ``built-in`` or ``uploaded``.

    Returns:
        ``code -> Language``.
    """
    found = {}
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return found
    for name in names:
        if name.endswith(".json"):
            lang = _load_file(os.path.join(folder, name), source)
            if lang is not None:
                found[lang.code] = lang
    return found


def available_languages() -> dict:
    """All usable languages, English first.

    Returns:
        Ordered ``code -> Language`` (uploaded files replace built-in ones with
        the same code).  English is always present.
    """
    builtin = _scan(BUILTIN_DIR, "built-in")
    uploaded = _scan(uploads_dir(), "uploaded")
    langs = dict(builtin)
    for code, lang in uploaded.items():
        lang.overrides_builtin = code in builtin
        langs[code] = lang
    if DEFAULT_LANGUAGE not in langs:
        langs[DEFAULT_LANGUAGE] = Language(DEFAULT_LANGUAGE, "English", "English", "ltr")
    english = langs.pop(DEFAULT_LANGUAGE)
    others = sorted(langs.values(), key=lambda lang: lang.english_name.lower())
    return {DEFAULT_LANGUAGE: english, **{lang.code: lang for lang in others}}


def english_keys() -> list:
    """All message keys of the application (from the built-in ``en.json``).

    Returns:
        List of English texts.
    """
    lang = _load_file(os.path.join(BUILTIN_DIR, "en.json"), "built-in")
    return list(lang.messages) if lang else []


def default_language() -> str:
    """The language for visitors and users who did not choose one.

    Returns:
        The ``default_language`` setting when that language exists, else ``en``.
    """
    from app.models.setting import get_setting  # noqa: PLC0415

    try:
        code = get_setting("default_language", DEFAULT_LANGUAGE) or DEFAULT_LANGUAGE
    except Exception:  # noqa: BLE001 - e.g. settings table missing before "flask db upgrade"
        code = DEFAULT_LANGUAGE
    return code if code in available_languages() else DEFAULT_LANGUAGE


def select_language() -> str:
    """Language of the current request.

    The logged-in user's choice, otherwise the default language (*Admin →
    Languages*).  Visitors who are not logged in – the login page, public
    screens – always get the default language.

    Returns:
        Language code.
    """
    from flask_login import current_user  # noqa: PLC0415

    langs = available_languages()
    choice = getattr(current_user, "language", None) if current_user and current_user.is_authenticated else None
    return choice if choice in langs else default_language()


def current_language() -> Language:
    """The :class:`Language` used for the current request (cached for the request).

    Returns:
        Language (English outside a request, e.g. in the alarm scanner or the CLI).
    """
    if not has_request_context():
        return _ENGLISH
    # Cached per request (not on ``g``: the application context can outlive one request, e.g. in tests).
    lang = request.environ.get("pyplchmi.language")
    if lang is None:
        langs = available_languages()
        lang = langs.get(select_language()) or langs[DEFAULT_LANGUAGE]
        request.environ["pyplchmi.language"] = lang
    return lang


def gettext(text: str, *args, **kwargs) -> str:
    """Translate an English UI text into the current language.

    Args:
        text: English text (the message key).
        *args: Values for ``{0}``, ``{1}``… placeholders.
        **kwargs: Values for ``{name}`` placeholders.

    Returns:
        The translation (English when missing), formatted with the values.
        A translation that cannot be formatted falls back to English.
    """
    if not isinstance(text, str):
        return text
    translated = current_language().messages.get(text, text) if text else text
    if not args and not kwargs:
        return translated
    try:
        return translated.format(*args, **kwargs)
    except (IndexError, KeyError, ValueError, AttributeError):
        try:
            return text.format(*args, **kwargs)
        except (IndexError, KeyError, ValueError, AttributeError):
            return text


#: Short alias used in Python code and templates: ``_("Screens")``.
_ = gettext


def translate_message(message) -> str:
    """Translate a stored message such as a flash (only exact keys match).

    Args:
        message: Text.

    Returns:
        Translated text.
    """
    return gettext(message) if isinstance(message, str) else message


def save_uploaded(raw: bytes) -> tuple[Language, list]:
    """Validate and store an uploaded language file.

    Args:
        raw: File content.

    Returns:
        ``(language, warnings)`` as from :func:`parse_language`.

    Raises:
        LanguageFileError: If the file is not usable.
    """
    lang, warnings = parse_language(raw)
    path = os.path.join(uploads_dir(), f"{lang.code}.json")
    doc = {"language": lang.meta(), "messages": lang.messages}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1, sort_keys=False)
    os.replace(tmp, path)
    return lang, warnings


def delete_uploaded(code: str) -> bool:
    """Remove an uploaded language file.

    Args:
        code: Language code.

    Returns:
        ``True`` if a file was removed.
    """
    if not CODE_RE.match(code or ""):
        return False
    path = os.path.join(uploads_dir(), f"{code}.json")
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


def language_document(lang: Language, template: bool = False) -> dict:
    """The JSON document of a language for download.

    Args:
        lang: Language.
        template: Produce a translation template instead: every English key
            with an empty value (keys already translated keep their text) and
            placeholder language details to fill in.

    Returns:
        Dict ready for :func:`json.dumps`.
    """
    if not template:
        return {"language": lang.meta(), "messages": dict(lang.messages)}
    keys = english_keys()
    meta = lang.meta() if lang.code != DEFAULT_LANGUAGE else {
        "code": "xx", "name": "Name of the language in itself", "english_name": "Name in English",
        "direction": "ltr"}
    messages = {k: ("" if lang.code == DEFAULT_LANGUAGE else lang.messages.get(k, "")) for k in keys}
    return {"language": meta, "messages": messages}


def coverage(lang: Language) -> tuple[int, int]:
    """How many application texts a language translates.

    Args:
        lang: Language.

    Returns:
        ``(translated, total)``.
    """
    keys = english_keys()
    if lang.code == DEFAULT_LANGUAGE:
        return len(keys), len(keys)
    return sum(1 for k in keys if k in lang.messages), len(keys)
