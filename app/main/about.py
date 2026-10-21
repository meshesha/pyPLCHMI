"""Content of the *About* page: project, author, open-source credits and system information.

The author and project links are fixed here (one place to change them).  The
system information (versions, database, schema revision) is shown to
administrators only – it helps support, but tells strangers more than they
need to know about the server.
"""

from __future__ import annotations

import platform
import sys
from importlib import metadata

def N_(text: str) -> str:
    """Mark a text for translation; it is translated where it is shown (``_(…)`` in the template)."""
    return text


#: The author of pyPLCHMI (GitHub profile).
AUTHOR = {
    "name": "Tady",
    "login": "meshesha",
    "github": "https://github.com/meshesha",
    "website": "https://meshesha.js.org",
}

#: The project on GitHub.
PROJECT = {
    "name": "pyPLCHMI",
    "repository": "https://github.com/meshesha/pyPLCHMI",
    "issues": "https://github.com/meshesha/pyPLCHMI/issues",
    "license": "MIT",
    "license_url": "https://github.com/meshesha/pyPLCHMI/blob/main/LICENSE",
}

#: Python packages credited on the page: (distribution name, what it does here, licence).
PYTHON_COMPONENTS = [
    ("Flask", N_("Web framework"), "BSD-3-Clause"),
    ("SQLAlchemy", N_("Database toolkit"), "MIT"),
    ("Flask-SQLAlchemy", N_("Database integration"), "BSD-3-Clause"),
    ("Flask-Migrate", N_("Database migrations (Alembic)"), "MIT"),
    ("Flask-Login", N_("Log-in sessions"), "MIT"),
    ("Flask-Bcrypt", N_("Password hashing"), "BSD-3-Clause"),
    ("Flask-WTF", N_("Forms and CSRF protection"), "BSD-3-Clause"),
    ("python-snap7", N_("Siemens S7 communication"), "MIT"),
    ("pymodbus", N_("Modbus TCP / RTU"), "BSD-3-Clause"),
    ("pyModbusTCP", N_("Modbus TCP client and server"), "MIT"),
    ("BAC0", N_("BACnet/IP"), "LGPL-3.0"),
    ("openpyxl", N_("Excel import and export"), "MIT"),
    ("PyYAML", N_("Automations in YAML"), "MIT"),
    ("pytablericons", N_("Tabler icons for graphic screens"), "MIT"),
    ("waitress", N_("Production web server"), "ZPL-2.1"),
]

#: Browser libraries bundled with pyPLCHMI (no internet needed): (name, version, what it does here, licence).
WEB_COMPONENTS = [
    ("Bootstrap", "5.3.3", N_("Page layout and controls"), "MIT"),
    ("Bootstrap Icons", "1.11.3", N_("Icons"), "MIT"),
    ("Chart.js", "4.4.7", N_("Trend charts"), "MIT"),
    ("chartjs-adapter-date-fns", "3.0.0", N_("Time axes of the charts"), "MIT"),
    ("SweetAlert2", "11.26.25", N_("Dialogs and notifications"), "MIT"),
]


def package_version(name: str) -> str | None:
    """Installed version of a Python package, or ``None`` when it is not installed."""
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def python_components() -> list[dict]:
    """The credited Python packages with their installed versions."""
    return [{"name": name, "version": package_version(name), "role": role, "license": lic}
            for name, role, lic in PYTHON_COMPONENTS]


def schema_revision() -> str | None:
    """The database schema revision (Alembic), or ``None`` when it cannot be read."""
    from sqlalchemy import text  # noqa: PLC0415

    from app.extensions import db  # noqa: PLC0415

    try:
        return db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001 - e.g. a database created without migrations (tests)
        db.session.rollback()
        return None


def system_info(app) -> list[tuple[str, str]]:
    """Facts about this installation for administrators: ``[(label, value)]``."""
    from app.extensions import db  # noqa: PLC0415

    engine = db.engine
    url = engine.url
    database = url.get_backend_name()
    if database == "sqlite":
        database += f" ({url.database or 'memory'})"
    elif url.host:
        database += f" ({url.host}{':' + str(url.port) if url.port else ''}/{url.database or ''})"
    return [
        ("Python", f"{platform.python_version()} ({platform.python_implementation()}, {sys.platform})"),
        (N_("Operating system"), f"{platform.system()} {platform.release()} ({platform.machine()})"),
        (N_("Database"), database),
        (N_("Database schema revision"), schema_revision() or "—"),
        (N_("Mode"), N_("testing") if app.config.get("TESTING") else
         (N_("development (debug)") if app.debug else N_("production"))),
        (N_("PLC simulation"), N_("on") if app.config.get("PLC_SIMULATION") else N_("off")),
    ]
