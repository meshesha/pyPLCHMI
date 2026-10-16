"""Multi-language support (JSON language files).

* :mod:`app.i18n.catalog` – loading the language files, choosing the
  language of a request and the translation function ``_()``;
* :mod:`app.i18n.forms` – a ``FlaskForm`` whose labels, descriptions, choices
  and errors are translated;
* :mod:`app.i18n.routes` – the blueprint ``i18n``: language switcher, the
  JavaScript catalog and the administration page (*Admin → Languages*:
  upload, download, delete, default language).

This package module stays free of view imports so that models and forms can
import :func:`gettext` without import cycles.
"""

from app.i18n.catalog import _, current_language, gettext  # noqa: F401  (re-exported)
