"""Database models.

Importing this package registers every model with SQLAlchemy, which is
required for Flask-Migrate to detect schema changes.
"""

from app.models.alarm import Alarm, AlarmEvent
from app.models.asset import Asset, AssetFolder
from app.models.automation import Automation, AutomationRun
from app.models.dashboard import Dashboard, DataPoint, ScreenTag
from app.models.audit import AuditLog
from app.models.document import InfoFile
from app.models.dummy_value import DummyValue
from app.models.history import TagSample, TrendChart
from app.models.plc import PLC
from app.models.setting import Setting
from app.models.user import AnonymousUser, Perm, Permission, Role, User

__all__ = ["Alarm", "AlarmEvent", "Automation", "AutomationRun", "AuditLog", "InfoFile", "AnonymousUser", "Asset", "AssetFolder", "Dashboard", "DataPoint", "DummyValue", "PLC", "Perm", "Permission", "Role", "ScreenTag", "Setting", "TagSample", "TrendChart", "User"]
