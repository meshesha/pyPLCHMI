"""Navigation data shared by the sidebar layout and the quick search (Ctrl+K).

Both are built from one list of pages so the sidebar, the top bar and the
command palette always offer the same pages with the same permission checks.
"""

from __future__ import annotations

from dataclasses import dataclass

from flask import url_for

from app.i18n.catalog import gettext

#: Workspaces of the sidebar layout.
OPERATE = "operate"
ENGINEER = "engineer"


@dataclass
class NavItem:
    """One page in the navigation.

    Attributes:
        key: Stable id (used for HTML ids: ``side-<key>``).
        label: Translated label.
        icon: Bootstrap icon name (without ``bi-``).
        url: Link.
        workspace: ``operate`` or ``engineer``.
        group: Translated group heading.
    """

    key: str
    label: str
    icon: str
    url: str
    workspace: str
    group: str


def nav_items(user, home_overview: bool = False) -> list[NavItem]:
    """Pages the user may open, in display order.

    Args:
        user: The current user (``current_user``).
        home_overview: Whether the active UI template uses the plant overview.

    Returns:
        List of :class:`NavItem` (empty for visitors).
    """
    from app.models import Perm  # noqa: PLC0415

    if not user.is_authenticated:
        return []
    op, tools, eng, sysg = gettext("Operate"), gettext("Tools"), gettext("Engineering"), gettext("System")
    items: list[NavItem] = []
    if home_overview:
        items.append(NavItem("overview", gettext("Plant overview"), "speedometer2", url_for("main.overview"), OPERATE, op))
    items.append(NavItem("screens", gettext("Screens"), "grid-1x2", url_for("main.screens"), OPERATE, op))
    items.append(NavItem("events", gettext("Event History"), "clock-history", url_for("alarms.history"), OPERATE, tools))
    if user.can(Perm.VIEW_HISTORY):
        items.append(NavItem("charts", gettext("Trend charts"), "graph-up", url_for("history.charts"), OPERATE, tools))
        items.append(NavItem("samples", gettext("Tag history"), "table", url_for("history.index"), OPERATE, tools))
    items.append(NavItem("documents", gettext("Information library"), "file-earmark-richtext", url_for("documents.index"),
                         OPERATE, tools))
    if user.can(Perm.MANAGE_ROLES):
        items.append(NavItem("tags", gettext("Tag table"), "tags", url_for("tags.index"), ENGINEER, eng))
        items.append(NavItem("alarms", gettext("Alarms"), "bell", url_for("alarms.index"), ENGINEER, eng))
    if user.can(Perm.MANAGE_AUTOMATIONS):
        items.append(NavItem("automations", gettext("Automations"), "robot", url_for("automations.index"), ENGINEER, eng))
    if _has_devices():
        items.append(NavItem("devices", gettext("Devices"), "window-stack", url_for("faceplates.devices"), OPERATE, tools))
    if user.can(Perm.MANAGE_SCREENS):
        items.append(NavItem("faceplates", gettext("Faceplates"), "window-stack", url_for("faceplates.index"),
                             ENGINEER, eng))
    if user.can(Perm.MANAGE_PLCS):
        items.append(NavItem("plcs", gettext("PLCs"), "hdd-network", url_for("admin.plcs"), ENGINEER, eng))
        items.append(NavItem("modbus-server", gettext("Modbus TCP server"), "broadcast-pin",
                             url_for("modbus_server.index"), ENGINEER, eng))
    if user.can(Perm.MANAGE_SCREENS):
        items.append(NavItem("images", gettext("Images"), "images", url_for("assets.index"), ENGINEER, eng))
    if user.can(Perm.MANAGE_USERS):
        items.append(NavItem("users", gettext("Users"), "people", url_for("admin.users"), ENGINEER, sysg))
    if user.can(Perm.MANAGE_ROLES):
        items.append(NavItem("roles", gettext("Roles & permissions"), "shield-lock", url_for("admin.roles"), ENGINEER, sysg))
        items.append(NavItem("settings", gettext("System settings"), "sliders", url_for("admin.settings"), ENGINEER, sysg))
        items.append(NavItem("languages", gettext("Languages"), "translate", url_for("i18n.admin_languages"), ENGINEER, sysg))
        items.append(NavItem("ui-templates", gettext("UI templates"), "palette", url_for("ui_templates.admin_index"),
                             ENGINEER, sysg))
    if user.can(Perm.VIEW_AUDIT):
        items.append(NavItem("audit", gettext("Audit log"), "journal-text", url_for("audit.index"), ENGINEER, sysg))
    items.append(NavItem("account", gettext("Settings"), "person-gear", url_for("auth.profile"), OPERATE, sysg))
    items.append(NavItem("about", gettext("About"), "info-circle", url_for("main.about"), OPERATE, sysg))
    return items


def grouped(items: list[NavItem]) -> list[tuple[str, str, list[NavItem]]]:
    """Group navigation items for the sidebar, keeping their order.

    Args:
        items: From :func:`nav_items`.

    Returns:
        ``[(workspace, group label, [items])]``.
    """
    groups: list = []
    for item in items:
        if groups and groups[-1][0] == item.workspace and groups[-1][1] == item.group:
            groups[-1][2].append(item)
        else:
            groups.append((item.workspace, item.group, [item]))
    return groups


def _has_devices() -> bool:
    """``True`` when faceplate devices exist (see ``has_devices`` in the app factory)."""
    from flask import current_app  # noqa: PLC0415

    fn = current_app.jinja_env.globals.get("has_devices")
    return bool(fn and fn())
