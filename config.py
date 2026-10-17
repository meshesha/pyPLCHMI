"""Application configuration classes.

Each environment (development, testing, production) has its own class that
inherits shared defaults from :class:`Config`.  The active class is chosen by
:func:`app.create_app` from the ``config_name`` argument or from the
``FLASK_CONFIG`` environment variable.

Values that differ per installation (secret key, database URL, ...) are read
from environment variables, which may be placed in a ``.env`` file in the
project root (loaded automatically through ``python-dotenv``).
"""

import os

from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean flag from the environment.

    Args:
        name: Environment variable name.
        default: Value returned when the variable is not set.

    Returns:
        ``True`` for the strings ``1``, ``true``, ``yes`` or ``on``
        (case-insensitive), ``False`` for any other value, and ``default``
        when the variable is missing.
    """
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _sqlite_url(filename: str) -> str:
    """Build a SQLite URL pointing at a file inside the ``instance`` folder.

    Args:
        filename: Database file name, e.g. ``"dev.sqlite"``.

    Returns:
        An SQLAlchemy URL such as ``sqlite:////abs/path/instance/dev.sqlite``.
    """
    instance_dir = os.path.join(BASE_DIR, "instance")
    os.makedirs(instance_dir, exist_ok=True)
    return "sqlite:///" + os.path.join(instance_dir, filename)


class Config:
    """Settings shared by every environment."""

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-change-me")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_TIME_LIMIT = None  # tokens valid for the life of the session

    # Session cookie hardening.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True

    BCRYPT_LOG_ROUNDS = 12

    # --- PLC communication -------------------------------------------------
    #: When True, no real PLC is contacted; an in-memory simulator is used.
    PLC_SIMULATION = _env_bool("PLC_SIMULATION", False)
    #: Seconds to wait before retrying a PLC whose connection failed.
    PLC_RECONNECT_DELAY = float(os.environ.get("PLC_RECONNECT_DELAY", "5"))
    #: Largest contiguous byte block fetched in a single ``read_area`` call.
    PLC_MAX_READ_SPAN = int(os.environ.get("PLC_MAX_READ_SPAN", "200"))
    #: Maximum unused gap (bytes) between two tags that are still merged.
    PLC_MAX_READ_GAP = int(os.environ.get("PLC_MAX_READ_GAP", "32"))

    # --- Image assets (graphic dashboards) ---------------------------------
    #: Folder for uploaded images (default: ``instance/uploads``).
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER") or os.path.join(BASE_DIR, "instance", "uploads")
    #: Largest accepted image file in bytes.
    ASSET_MAX_BYTES = int(os.environ.get("ASSET_MAX_BYTES", str(5 * 1024 * 1024)))
    #: Folder for language files uploaded by administrators (default: ``instance/languages``).
    LANGUAGES_FOLDER = os.environ.get("LANGUAGES_FOLDER") or os.path.join(BASE_DIR, "instance", "languages")
    #: Folder for UI templates uploaded by administrators (default: ``instance/ui_templates``).
    UI_TEMPLATES_FOLDER = os.environ.get("UI_TEMPLATES_FOLDER") or os.path.join(BASE_DIR, "instance", "ui_templates")
    #: Upper bound for any request body (several images per upload).
    MAX_CONTENT_LENGTH = 32 * 1024 * 1024
    #: Largest file of the information library (PDF / image attached to PLCs).
    LIBRARY_MAX_BYTES = int(os.environ.get("LIBRARY_MAX_BYTES", str(25 * 1024 * 1024)))

    # --- BACnet/IP (local stack used to talk to BACnet devices) -------------
    #: Local interface as ``ip/mask`` (e.g. ``192.168.1.20/24``); empty = auto-detect.
    BACNET_LOCAL_IP = os.environ.get("BACNET_LOCAL_IP") or None
    #: Local UDP port of the BACnet stack (47808 = standard BAC0 port).
    BACNET_LOCAL_PORT = int(os.environ.get("BACNET_LOCAL_PORT", "47808"))
    #: BACnet device instance of this HMI on the network (must be unique).
    BACNET_DEVICE_ID = int(os.environ.get("BACNET_DEVICE_ID", "3056"))

    # --- Alarms ------------------------------------------------------------
    #: Run the background alarm scanner thread.
    ALARM_SCANNER = _env_bool("ALARM_SCANNER", True)
    #: Seconds between alarm scans.
    ALARM_SCAN_INTERVAL = float(os.environ.get("ALARM_SCAN_INTERVAL", "2"))

    # --- Server connection monitor (browser) -----------------------------
    #: Seconds between two heartbeats of the browser's connection monitor (Web Worker).
    SERVER_CHECK_INTERVAL = float(os.environ.get("SERVER_CHECK_INTERVAL", "5"))

    # --- Tag history (trends) --------------------------------------------
    #: Run the background history logger thread (saves tag samples).
    HISTORY_LOGGER = _env_bool("HISTORY_LOGGER", True)
    #: Seconds between two checks of the logger for tags that are due.
    HISTORY_TICK = float(os.environ.get("HISTORY_TICK", "1"))
    #: Default number of days samples are kept (changed in System settings; 0 = forever).
    HISTORY_RETENTION_DAYS = int(os.environ.get("HISTORY_RETENTION_DAYS", "90"))
    #: Time zone for dates when the browser has not reported its own (empty = server's zone).
    DISPLAY_TIMEZONE = os.environ.get("DISPLAY_TIMEZONE", "")

    #: Lowest allowed dashboard refresh interval in milliseconds.
    DASHBOARD_MIN_REFRESH_MS = 250

    @staticmethod
    def init_app(app):
        """Hook for environment-specific initialisation.

        Args:
            app: The Flask application being configured.
        """


class DevelopmentConfig(Config):
    """Local development: debug on, SQLite database in ``instance/``."""

    DEBUG = True
    BCRYPT_LOG_ROUNDS = 4
    SQLALCHEMY_DATABASE_URI = os.environ.get("DEV_DATABASE_URL") or _sqlite_url("dev.sqlite")


class TestingConfig(Config):
    """Automated tests: in-memory DB, CSRF off, simulated PLCs."""

    TESTING = True
    WTF_CSRF_ENABLED = False
    BCRYPT_LOG_ROUNDS = 4
    SQLALCHEMY_DATABASE_URI = os.environ.get("TEST_DATABASE_URL") or "sqlite://"
    PLC_SIMULATION = True
    PLC_RECONNECT_DELAY = 0
    ALARM_SCANNER = False
    ALARM_SCAN_INTERVAL = 0
    HISTORY_LOGGER = False
    SERVER_NAME = "localhost"


class ProductionConfig(Config):
    """Production deployment.

    ``SECRET_KEY`` and ``DATABASE_URL`` should be provided by the environment.
    """

    DEBUG = False
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or _sqlite_url("plchmi.sqlite")
    # Enable when the app is served over HTTPS.
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", False)
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE

    @classmethod
    def init_app(cls, app):
        """Refuse to start production with the default development secret.

        Args:
            app: The Flask application being configured.

        Raises:
            RuntimeError: If ``SECRET_KEY`` was not set in the environment.
        """
        Config.init_app(app)
        if app.config["SECRET_KEY"] == "dev-insecure-change-me":
            raise RuntimeError("SECRET_KEY must be set in the environment for production.")


config = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
