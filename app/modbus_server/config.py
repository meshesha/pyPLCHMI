"""Settings of the Modbus TCP server (stored in the ``settings`` table)."""

from __future__ import annotations

import ipaddress
from dataclasses import asdict, dataclass

from app.i18n.catalog import gettext
from app.modbus_server.encoding import ORDERS

#: Setting key prefix.
PREFIX = "modbus_server."
#: What a tag without a valid value publishes.
BAD_VALUE = {
    "hold": "Keep the last good value",
    "zero": "Send 0 (OFF / empty)",
}
#: Shortest and longest update interval (seconds).
MIN_INTERVAL, MAX_INTERVAL = 0.2, 60.0


@dataclass
class ServerConfig:
    """Modbus TCP server settings.

    Attributes:
        enabled: Run the server.
        host: Network address to listen on (``0.0.0.0`` = every network card).
        port: TCP port (502 is the Modbus standard).
        unit_id: Answer only this unit id (0 = any unit id).
        order: Value order of 32/64-bit values (``ABCD`` …).
        interval: Seconds between two updates of the published values.
        bad_value: ``hold`` or ``zero`` (see :data:`BAD_VALUE`).
        allowed: Client IP addresses / networks allowed to connect (empty = all),
            separated by commas, spaces or new lines (``10.0.0.5, 10.1.0.0/24``).
        heartbeat: Register with a counter that grows by 1 on every update
            (``None`` = no heartbeat) – lets the SCADA detect a frozen server.
        quality_base: First bit of the *quality bits* (``None`` = none): bit
            ``quality_base + n`` is ON while the *n*-th published tag (in
            address order) has a valid value.
    """

    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 502
    unit_id: int = 0
    order: str = "ABCD"
    interval: float = 1.0
    bad_value: str = "hold"
    allowed: str = ""
    heartbeat: int | None = None
    quality_base: int | None = None

    # -- storage ---------------------------------------------------------------
    @classmethod
    def load(cls) -> "ServerConfig":
        """Read the settings (defaults for missing values)."""
        from app.models.setting import get_setting  # noqa: PLC0415

        cfg = cls()
        for name, default in asdict(cls()).items():
            raw = get_setting(PREFIX + name)
            if raw is None:
                continue
            try:
                if name == "enabled":
                    value = raw == "1"
                elif name in ("port", "unit_id", "heartbeat", "quality_base"):
                    value = int(raw)
                elif name == "interval":
                    value = float(raw)
                else:
                    value = raw
            except ValueError:
                value = default
            setattr(cfg, name, value)
        return cfg

    def save(self) -> None:
        """Store the settings (the caller commits)."""
        from app.models.setting import set_setting  # noqa: PLC0415

        for name, value in asdict(self).items():
            if name == "enabled":
                value = "1" if value else "0"
            set_setting(PREFIX + name, "" if value is None else value)

    def endpoint_key(self) -> tuple:
        """Settings that need a new listening socket when they change."""
        return (self.enabled, self.host, int(self.port))

    # -- checks --------------------------------------------------------------
    def networks(self) -> list:
        """The allowed client networks (empty list = everybody)."""
        out = []
        for part in self.allowed.replace(",", " ").split():
            out.append(ipaddress.ip_network(part, strict=False))
        return out

    def client_allowed(self, address: str) -> bool:
        """Tell whether a client IP may connect."""
        nets = self.networks()
        if not nets:
            return True
        try:
            ip = ipaddress.ip_address(address.split("%")[0])
        except ValueError:
            return False
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        return any(ip in net for net in nets if net.version == ip.version)

    def validate(self) -> dict[str, str]:
        """Check the settings.

        Returns:
            ``field -> message`` (empty = valid).
        """
        errors = {}
        try:
            ipaddress.ip_address(self.host)
        except ValueError:
            errors["host"] = gettext("Enter an IP address of this computer, or 0.0.0.0 for every network card.")
        if not 1 <= int(self.port) <= 65535:
            errors["port"] = gettext("Enter a port from 1 to 65535 (Modbus standard: 502).")
        if not 0 <= int(self.unit_id) <= 255:
            errors["unit_id"] = gettext("Enter a unit id from 0 to 255 (0 = answer every unit id).")
        if self.order not in ORDERS:
            errors["order"] = gettext("Choose a value order.")
        if not MIN_INTERVAL <= float(self.interval) <= MAX_INTERVAL:
            errors["interval"] = gettext("Enter a number from {0} to {1}.", MIN_INTERVAL, MAX_INTERVAL)
        if self.bad_value not in BAD_VALUE:
            errors["bad_value"] = gettext("Choose what to send for tags without a valid value.")
        try:
            self.networks()
        except ValueError as exc:
            errors["allowed"] = gettext("Invalid IP address or network: {0}", exc)
        for name in ("heartbeat", "quality_base"):
            value = getattr(self, name)
            if value is not None and not 0 <= int(value) <= 65535:
                errors[name] = gettext("Enter an address from 0 to 65535, or leave it empty.")
        return errors
