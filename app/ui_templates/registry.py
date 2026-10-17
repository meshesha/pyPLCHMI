"""UI templates: switchable looks and layouts of the whole user interface.

A *UI template* is a folder with a ``template.json`` manifest plus static files
(CSS, JavaScript, images, fonts).  It changes how pyPLCHMI looks and which of
the built-in interface variants are used (navigation, alarm display, screen
tiles, tag table...).  It never contains server-side code: Jinja templates and
Python are not accepted, so an uploaded template cannot run code on the server.

Two kinds exist:

* **built-in** templates in ``app/ui_templates/builtin/<id>/`` (read-only);
* **uploaded** templates in ``<UI_TEMPLATES_FOLDER>/<id>/`` (default
  ``instance/ui_templates``), installed from a ZIP file by an administrator.

The active template is stored in the ``ui_template`` setting.  An administrator
can *preview* another template for his own session before activating it.

Manifest (``template.json``)::

    {
      "pyplchmi_template": 1,
      "id": "my-plant",                 # [a-z0-9][a-z0-9_-]{1,39}
      "name": "My plant",
      "version": "1.0.0",
      "author": "ACME Automation",
      "description": "Dark theme with the alarm strip.",
      "color_mode": "user",             # user | light | dark
      "features": {"alarm_ui": "banner", "nav": "sidebar"},
      "css": ["theme.css"],
      "js": ["theme.js"],
      "preview": "preview.png",
      "i18n": {"he": {"name": "...", "description": "..."}}
    }
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field

from flask import current_app, has_request_context, request, session

from app.i18n.catalog import gettext

#: Manifest format understood by this version.
FORMAT_VERSION = 1
#: Name of the manifest file.
MANIFEST = "template.json"
#: Template ids: lower case letters, digits, ``-`` and ``_``.
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")
#: Setting key of the active template.
ACTIVE_SETTING = "ui_template"
#: Session key of an administrator's preview.
PREVIEW_KEY = "ui_template_preview"
#: Template used when nothing (valid) is configured.
DEFAULT_ID = "classic"

#: Display order of the built-in templates (uploaded ones follow, by name).
BUILTIN_ORDER = {"classic": 0, "high-performance": 1, "alarm-focus": 2, "workspace": 3, "engineering": 4}

#: Interface features a template can switch: name -> allowed values (first = default).
FEATURES: dict[str, tuple] = {
    "nav": ("topbar", "sidebar"),            # icon top bar | labelled sidebar with Operate / Engineer
    "home": ("screens", "overview"),         # screen list | plant overview with live status
    "alarm_ui": ("popup", "banner"),         # alarm window | alarm strip + side drawer (never blocks)
    "tiles": ("classic", "hp"),              # grid screen tiles | high-performance tiles (ISA-101)
    "tags_ui": ("classic", "panel"),         # tag table | side panel editing + bulk actions
    "command_palette": (False, True),        # Ctrl+K: go to screen / tag / PLC / page
}
#: Short descriptions of the features (shown on the templates page).
FEATURE_LABELS = {
    "nav": "Navigation",
    "home": "Home page",
    "alarm_ui": "Alarm display",
    "tiles": "Screen tiles",
    "tags_ui": "Tag table",
    "command_palette": "Quick search (Ctrl+K)",
}
#: Human-readable feature values.
FEATURE_VALUE_LABELS = {
    "topbar": "Icon top bar", "sidebar": "Sidebar with Operate / Engineer workspaces",
    "screens": "Screen list", "overview": "Plant overview with live status",
    "popup": "Alarm window", "banner": "Alarm strip + alarm list (never blocks the page)",
    "classic": "Classic", "hp": "High-performance (ISA-101)",
    "panel": "Side panel editing + bulk actions",
    True: "On", False: "Off",
}
#: Colour modes: ``user`` = the user's own choice (Settings), else forced.
COLOR_MODES = ("user", "light", "dark")

#: File types allowed in a template package.
ALLOWED_EXTENSIONS = {".json", ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".woff",
                      ".woff2", ".ttf", ".otf", ".md", ".txt"}
#: File types that may be served to browsers (documentation files are not served).
SERVED_EXTENSIONS = ALLOWED_EXTENSIONS - {".md", ".txt"}
#: Limits for uploaded packages.
MAX_ZIP_BYTES = 10 * 1024 * 1024
MAX_FILES = 200
MAX_UNPACKED_BYTES = 30 * 1024 * 1024


class TemplateError(ValueError):
    """A template package or manifest is invalid (the message is shown to the user)."""


@dataclass
class UITemplate:
    """A loaded, validated UI template.

    Attributes:
        id: Unique id (folder name).
        name: Display name.
        version: Version text.
        author: Author text.
        description: Short description.
        color_mode: ``user``, ``light`` or ``dark``.
        features: Feature name -> value (every known feature, defaults filled in).
        css: Stylesheets (paths inside the template folder), loaded in order.
        js: Scripts (paths inside the template folder), loaded at the end of the page.
        preview: Preview image (path) or ``None``.
        builtin: ``True`` for the templates shipped with pyPLCHMI.
        path: Absolute folder.
        i18n: ``language code -> {"name", "description"}``.
    """

    id: str
    name: str
    version: str = ""
    author: str = ""
    description: str = ""
    color_mode: str = "user"
    features: dict = field(default_factory=dict)
    css: list = field(default_factory=list)
    js: list = field(default_factory=list)
    preview: str | None = None
    builtin: bool = False
    path: str = ""
    i18n: dict = field(default_factory=dict)

    def feature(self, name: str):
        """Value of a feature (the default when the template does not set it).

        Args:
            name: A key of :data:`FEATURES`.

        Returns:
            The value.
        """
        return self.features.get(name, FEATURES[name][0])

    def title(self, lang: str | None = None) -> str:
        """Display name, in the given language when the manifest translates it.

        Args:
            lang: Language code (``None`` = the current language).

        Returns:
            Name text.
        """
        return (self.i18n.get(lang or _lang(), {}) or {}).get("name") or self.name

    def summary(self, lang: str | None = None) -> str:
        """Description, translated when the manifest provides it.

        Args:
            lang: Language code (``None`` = the current language).

        Returns:
            Description text.
        """
        return (self.i18n.get(lang or _lang(), {}) or {}).get("description") or self.description

    @property
    def stamp(self) -> str:
        """Cache-busting text for the file links (``?v=``): version and newest file time."""
        newest = 0
        for rel in [MANIFEST, *self.css, *self.js]:
            try:
                newest = max(newest, int(os.path.getmtime(os.path.join(self.path, rel))))
            except OSError:
                continue
        return f"{self.version}-{newest}"

    @property
    def is_light(self) -> bool:
        """Whether the template forces the light colour mode."""
        return self.color_mode == "light"

    def changed_features(self) -> dict:
        """Features whose value differs from the default (for the templates page).

        Returns:
            ``name -> value``.
        """
        return {k: v for k, v in self.features.items() if v != FEATURES[k][0]}


def _lang() -> str:
    """Code of the current interface language (``en`` outside a request)."""
    if not has_request_context():
        return "en"
    from app.i18n.catalog import current_language  # noqa: PLC0415

    return current_language().code


def builtin_folder() -> str:
    """Folder of the built-in templates."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "builtin")


def upload_folder() -> str:
    """Folder of uploaded templates (created when missing)."""
    folder = current_app.config.get("UI_TEMPLATES_FOLDER") or os.path.join(current_app.instance_path, "ui_templates")
    os.makedirs(folder, exist_ok=True)
    return folder


def _safe_rel(path: str) -> str:
    """Normalise a relative path inside a template and reject escapes.

    Args:
        path: Path from the manifest or the ZIP file.

    Returns:
        The normalised ``a/b.css`` path.

    Raises:
        TemplateError: For absolute paths, ``..`` or empty names.
    """
    rel = str(path or "").replace("\\", "/").strip()
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel) or any(p == ".." for p in parts):
        raise TemplateError(gettext("Invalid file path in the template: {0}", path))
    return "/".join(parts)


def parse_manifest(data: dict, folder: str, builtin: bool = False) -> UITemplate:
    """Validate a manifest and build the :class:`UITemplate`.

    Args:
        data: Parsed ``template.json``.
        folder: Template folder (files named by the manifest must exist there).
        builtin: Whether it is a built-in template.

    Returns:
        The template.

    Raises:
        TemplateError: With a message describing the first problem.
    """
    if not isinstance(data, dict):
        raise TemplateError(gettext("template.json must contain a JSON object."))
    if data.get("pyplchmi_template") != FORMAT_VERSION:
        raise TemplateError(gettext('template.json needs "pyplchmi_template": {0}.', FORMAT_VERSION))
    tid = str(data.get("id") or "")
    if not ID_RE.match(tid):
        raise TemplateError(gettext("Invalid template id '{0}': use 2–40 lower-case letters, digits, - or _.", tid))
    name = str(data.get("name") or "").strip()
    if not name:
        raise TemplateError(gettext("The template needs a name."))
    color_mode = str(data.get("color_mode") or "user")
    if color_mode not in COLOR_MODES:
        raise TemplateError(gettext("color_mode must be one of: {0}.", ", ".join(COLOR_MODES)))
    raw_features = data.get("features") or {}
    if not isinstance(raw_features, dict):
        raise TemplateError(gettext('"features" must be an object.'))
    features = {name_: values[0] for name_, values in FEATURES.items()}
    for key, value in raw_features.items():
        if key not in FEATURES:
            raise TemplateError(gettext("Unknown feature '{0}'. Known features: {1}.", key, ", ".join(FEATURES)))
        if value not in FEATURES[key]:
            allowed = ", ".join(json.dumps(v) for v in FEATURES[key])
            raise TemplateError(gettext("Feature '{0}' cannot be {1}. Allowed: {2}.", key, json.dumps(value), allowed))
        features[key] = value

    def files(key: str, ext: tuple) -> list:
        """Validate a list of package files of one kind."""
        items = data.get(key) or []
        if isinstance(items, str):
            items = [items]
        if not isinstance(items, list):
            raise TemplateError(gettext('"{0}" must be a list of file names.', key))
        out = []
        for item in items:
            rel = _safe_rel(item)
            if not rel.lower().endswith(ext):
                raise TemplateError(gettext("'{0}' in \"{1}\" has the wrong file type.", rel, key))
            if not os.path.isfile(os.path.join(folder, rel)):
                raise TemplateError(gettext("File '{0}' named in template.json is missing.", rel))
            out.append(rel)
        return out

    css = files("css", (".css",))
    js = files("js", (".js",))
    preview = None
    if data.get("preview"):
        preview = files("preview", (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"))[0]
    i18n = data.get("i18n") or {}
    if not isinstance(i18n, dict):
        raise TemplateError(gettext('"i18n" must be an object.'))
    i18n = {str(k): {"name": str((v or {}).get("name") or ""), "description": str((v or {}).get("description") or "")}
            for k, v in i18n.items() if isinstance(v, dict)}
    return UITemplate(id=tid, name=name[:80], version=str(data.get("version") or "")[:20],
                      author=str(data.get("author") or "")[:80], description=str(data.get("description") or "")[:500],
                      color_mode=color_mode, features=features, css=css, js=js, preview=preview,
                      builtin=builtin, path=folder, i18n=i18n)


def load_folder(folder: str, builtin: bool = False) -> UITemplate:
    """Load and validate the template in a folder.

    Args:
        folder: Folder holding ``template.json``.
        builtin: Whether it is a built-in template.

    Returns:
        The template.

    Raises:
        TemplateError: If the manifest is missing or invalid.
    """
    manifest = os.path.join(folder, MANIFEST)
    if not os.path.isfile(manifest):
        raise TemplateError(gettext("template.json is missing."))
    try:
        with open(manifest, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise TemplateError(gettext("template.json is not valid JSON: {0}", exc)) from exc
    tpl = parse_manifest(data, folder, builtin)
    if os.path.basename(os.path.normpath(folder)) != tpl.id:
        raise TemplateError(gettext("The folder name must equal the template id '{0}'.", tpl.id))
    return tpl


#: Per-request caches (kept in the WSGI environ, not on ``g``: the application
#: context can outlive one request, e.g. in tests).
_ENV_TEMPLATES = "pyplchmi.ui_templates"
_ENV_ACTIVE = "pyplchmi.ui_active"

#: Loaded templates: folder -> (manifest modification time, template).
_CACHE: dict = {}


def _cached_load(folder: str, builtin: bool) -> UITemplate:
    """Load a template folder, reusing the last result while ``template.json`` is unchanged.

    Args:
        folder: Template folder.
        builtin: Whether it is a built-in template.

    Returns:
        The template.

    Raises:
        TemplateError: If the template is invalid.
    """
    try:
        mtime = os.path.getmtime(os.path.join(folder, MANIFEST))
    except OSError:
        mtime = None
    hit = _CACHE.get(folder)
    if hit and mtime is not None and hit[0] == mtime:
        return hit[1]
    tpl = load_folder(folder, builtin)
    if mtime is not None:
        _CACHE[folder] = (mtime, tpl)
    return tpl


def all_templates() -> dict:
    """Every valid template: built-in ones first, then uploaded ones (by name).

    Invalid uploaded folders are skipped (and logged).  An uploaded template can
    never replace a built-in one with the same id.  Cached per request.

    Returns:
        ``id -> UITemplate`` in display order.
    """
    if has_request_context() and _ENV_TEMPLATES in request.environ:
        return request.environ[_ENV_TEMPLATES]
    found: dict = {}
    for base, builtin in ((builtin_folder(), True), (upload_folder(), False)):
        items = []
        for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
            folder = os.path.join(base, name)
            if name.startswith(".") or not os.path.isdir(folder) or name in found:
                continue
            try:
                items.append(_cached_load(folder, builtin))
            except TemplateError as exc:
                current_app.logger.warning("UI template %s skipped: %s", folder, exc)
        for tpl in sorted(items, key=lambda t: (BUILTIN_ORDER.get(t.id, len(BUILTIN_ORDER)), t.name.lower())):
            found.setdefault(tpl.id, tpl)
    if has_request_context():
        request.environ[_ENV_TEMPLATES] = found
    return found


def get_template(tid: str | None) -> UITemplate | None:
    """The template with this id, or ``None``."""
    return all_templates().get(tid or "")


def configured_id() -> str:
    """Id stored in the ``ui_template`` setting (``classic`` when unset)."""
    from app.models.setting import get_setting  # noqa: PLC0415

    return get_setting(ACTIVE_SETTING, DEFAULT_ID)


def active_template() -> UITemplate:
    """The template used for the current request.

    An administrator's preview (session) wins over the configured template; a
    missing or broken template falls back to ``classic``.

    Returns:
        The :class:`UITemplate`.
    """
    if has_request_context() and _ENV_ACTIVE in request.environ:
        return request.environ[_ENV_ACTIVE]
    templates = all_templates()
    tid = configured_id()
    if has_request_context():
        preview = session.get(PREVIEW_KEY)
        if preview and preview in templates and _may_manage():
            tid = preview
    tpl = templates.get(tid) or templates.get(DEFAULT_ID) or UITemplate(id=DEFAULT_ID, name="Classic", builtin=True,
                                                                     features={k: v[0] for k, v in FEATURES.items()})
    if has_request_context():
        request.environ[_ENV_ACTIVE] = tpl
    return tpl


def previewing() -> UITemplate | None:
    """The template an administrator is previewing (``None`` when not previewing)."""
    if not has_request_context():
        return None
    preview = session.get(PREVIEW_KEY)
    if preview and preview != configured_id() and _may_manage():
        return get_template(preview)
    return None


def _may_manage() -> bool:
    """Whether the current user may manage UI templates (administrators)."""
    from flask_login import current_user  # noqa: PLC0415

    from app.models import Perm  # noqa: PLC0415

    return bool(current_user and current_user.is_authenticated and current_user.can(Perm.MANAGE_ROLES))


def activate(tid: str) -> UITemplate:
    """Make a template the active one for everybody (the caller commits).

    Args:
        tid: Template id.

    Returns:
        The template.

    Raises:
        TemplateError: If no such template exists.
    """
    from app.models.setting import set_setting  # noqa: PLC0415

    tpl = get_template(tid)
    if tpl is None:
        raise TemplateError(gettext("Template '{0}' does not exist.", tid))
    set_setting(ACTIVE_SETTING, tpl.id)
    if has_request_context():
        request.environ.pop(_ENV_ACTIVE, None)
    return tpl


def _members(zf: zipfile.ZipFile) -> tuple[str, list]:
    """Check the members of a package ZIP and find the folder holding ``template.json``.

    The manifest may be at the top of the ZIP or inside one top folder (as
    when a folder is zipped).

    Args:
        zf: The open ZIP file.

    Returns:
        ``(prefix, [(info, relative path)])`` for every file below the prefix.

    Raises:
        TemplateError: On unsafe paths, forbidden types or size limits.
    """
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if not infos:
        raise TemplateError(gettext("The ZIP file is empty."))
    if len(infos) > MAX_FILES:
        raise TemplateError(gettext("Too many files in the template (at most {0}).", MAX_FILES))
    if sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
        raise TemplateError(gettext("The template is too large when unpacked (at most {0} MB).",
                                    MAX_UNPACKED_BYTES // (1024 * 1024)))
    names = [_safe_rel(i.filename) for i in infos]
    if MANIFEST in names:
        prefix = ""
    else:
        tops = {n.split("/", 1)[0] for n in names}
        if len(tops) == 1 and f"{next(iter(tops))}/{MANIFEST}" in names:
            prefix = next(iter(tops)) + "/"
        else:
            raise TemplateError(gettext("template.json was not found at the top of the ZIP file."))
    members = []
    for info, name in zip(infos, names):
        if not name.startswith(prefix):
            continue
        rel = name[len(prefix):]
        if os.path.basename(rel).startswith(".") or "__MACOSX" in rel.split("/"):
            continue  # editor / OS clutter
        ext = os.path.splitext(rel)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise TemplateError(gettext("File type not allowed in a template: {0} (allowed: {1}).",
                                        rel, " ".join(sorted(ALLOWED_EXTENSIONS))))
        members.append((info, rel))
    return prefix, members


def install_zip(stream, replace: bool = False) -> tuple[UITemplate, bool]:
    """Install an uploaded template package.

    The ZIP is checked completely (paths, file types, sizes, manifest) in a
    temporary folder before anything is put in place.

    Args:
        stream: File object with the ZIP data.
        replace: Allow replacing an uploaded template with the same id.

    Returns:
        ``(template, replaced)`` – ``replaced`` is ``True`` when an existing
        uploaded template was updated.

    Raises:
        TemplateError: If the package is invalid or the id is taken.
    """
    data = stream.read(MAX_ZIP_BYTES + 1)
    if len(data) > MAX_ZIP_BYTES:
        raise TemplateError(gettext("The ZIP file is too large (at most {0} MB).", MAX_ZIP_BYTES // (1024 * 1024)))
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise TemplateError(gettext("This is not a ZIP file.")) from exc
    with zf:
        _prefix, members = _members(zf)
        try:
            manifest = json.loads(zf.read(next(i for i, rel in members if rel == MANIFEST)).decode("utf-8"))
        except (StopIteration, ValueError, UnicodeDecodeError) as exc:
            raise TemplateError(gettext("template.json is not valid JSON: {0}", exc)) from exc
        tid = str((manifest or {}).get("id") or "") if isinstance(manifest, dict) else ""
        if not ID_RE.match(tid):
            raise TemplateError(gettext("Invalid template id '{0}': use 2–40 lower-case letters, digits, - or _.", tid))
        if os.path.isdir(os.path.join(builtin_folder(), tid)):
            raise TemplateError(gettext("'{0}' is a built-in template and cannot be replaced. Use another id.", tid))
        target = os.path.join(upload_folder(), tid)
        if os.path.exists(target) and not replace:
            raise TemplateError(gettext("A template with the id '{0}' already exists. Tick 'Replace' to update it.", tid))
        replaced = os.path.exists(target)
        tmp = tempfile.mkdtemp(prefix=".uitpl-", dir=upload_folder())
        work = os.path.join(tmp, tid)
        try:
            os.makedirs(work)
            for info, rel in members:
                dest = os.path.join(work, *rel.split("/"))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(info) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
            load_folder(work)  # validates the manifest and the files it names
            if os.path.exists(target):
                shutil.rmtree(target)
            shutil.move(work, target)
            _CACHE.pop(target, None)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    if has_request_context():
        request.environ.pop(_ENV_TEMPLATES, None)
        request.environ.pop(_ENV_ACTIVE, None)
    return load_folder(target), replaced


def export_zip(tpl: UITemplate) -> bytes:
    """Pack a template folder as a ZIP (a starting point for developers).

    Args:
        tpl: The template.

    Returns:
        ZIP bytes with the files inside a ``<id>/`` folder.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for base, dirs, files in os.walk(tpl.path):
            dirs.sort()
            for name in sorted(files):
                full = os.path.join(base, name)
                rel = os.path.relpath(full, tpl.path).replace(os.sep, "/")
                if os.path.splitext(name)[1].lower() in ALLOWED_EXTENSIONS:
                    zf.write(full, f"{tpl.id}/{rel}")
    return buf.getvalue()


def delete_template(tid: str) -> UITemplate:
    """Delete an uploaded template (built-in ones cannot be deleted).

    If it was the active template, ``classic`` becomes active (the caller commits).

    Args:
        tid: Template id.

    Returns:
        The deleted template.

    Raises:
        TemplateError: For unknown or built-in templates.
    """
    tpl = get_template(tid)
    if tpl is None:
        raise TemplateError(gettext("Template '{0}' does not exist.", tid))
    if tpl.builtin:
        raise TemplateError(gettext("Built-in templates cannot be deleted."))
    shutil.rmtree(tpl.path)
    _CACHE.pop(tpl.path, None)
    if configured_id() == tid:
        from app.models.setting import set_setting  # noqa: PLC0415

        set_setting(ACTIVE_SETTING, DEFAULT_ID)
    if has_request_context():
        request.environ.pop(_ENV_TEMPLATES, None)
        request.environ.pop(_ENV_ACTIVE, None)
        if session.get(PREVIEW_KEY) == tid:
            session.pop(PREVIEW_KEY, None)
    return tpl
