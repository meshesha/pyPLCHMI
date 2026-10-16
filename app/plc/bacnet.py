"""BACnet/IP support based on **BAC0** (bacpypes3).

Overview
--------
* :class:`BACnetAddress` / :func:`parse_bacnet_address` – tag addresses such as
  ``AV3``, ``BO12``, ``MV2``, ``AI1.units``, ``AV5@10`` (write priority 10) or
  the long form ``analogValue:3``.
* :class:`BACnetNetwork` – the process-wide BAC0 stack.  BAC0 is asyncio
  based while Flask is threaded, so the stack runs in its own event-loop
  thread and requests are submitted with ``run_coroutine_threadsafe``.  Only
  one stack exists per process (it owns the local UDP port, 47808 by default).
* Adapters with one interface (``ping``, ``read_many``, ``write``,
  ``device_info``, ``object_list``):

  - :class:`BAC0Adapter`              – real devices through :class:`BACnetNetwork`
  - :class:`SimulatedBACnetAdapter`   – in-memory device for ``PLC_SIMULATION``

* :class:`BACnetConnection` – used by the connection manager through
  ``read_tags`` / ``write_tag`` (like Modbus).

Local stack settings come from the configuration (``.env``):
``BACNET_LOCAL_IP`` (e.g. ``192.168.1.20/24``; empty = auto-detect),
``BACNET_LOCAL_PORT`` (47808) and ``BACNET_DEVICE_ID`` (this HMI's own
BACnet device instance).
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import math
import re
import threading
import time
from dataclasses import dataclass

from app.plc.errors import ConnectionLostError, DeviceError
from app.i18n.catalog import gettext

log = logging.getLogger(__name__)

#: Short object codes -> (BAC0 object type name, description, writable, default data type)
BACNET_OBJECTS = {
    "AI": ("analogInput", "Analog input", False, "REAL"),
    "AO": ("analogOutput", "Analog output", True, "REAL"),
    "AV": ("analogValue", "Analog value", True, "REAL"),
    "BI": ("binaryInput", "Binary input", False, "BOOL"),
    "BO": ("binaryOutput", "Binary output", True, "BOOL"),
    "BV": ("binaryValue", "Binary value", True, "BOOL"),
    "MI": ("multiStateInput", "Multi-state input", False, "UINT"),
    "MO": ("multiStateOutput", "Multi-state output", True, "UINT"),
    "MV": ("multiStateValue", "Multi-state value", True, "UINT"),
    "CS": ("characterstringValue", "Character string value", True, "STRING"),
    "IV": ("integerValue", "Integer value", True, "DINT"),
    "PI": ("positiveIntegerValue", "Positive integer value", True, "UDINT"),
}

#: Aliases accepted in addresses (lower-case) -> short code.
_ALIASES = {
    **{code.lower(): code for code in BACNET_OBJECTS},
    **{spec[0].lower(): code for code, spec in BACNET_OBJECTS.items()},
    "msi": "MI", "mso": "MO", "msv": "MV", "csv": "CS",
    "analog-input": "AI", "analog-output": "AO", "analog-value": "AV",
    "binary-input": "BI", "binary-output": "BO", "binary-value": "BV",
    "multi-state-input": "MI", "multi-state-output": "MO", "multi-state-value": "MV",
    "multistateinput": "MI", "multistateoutput": "MO", "multistatevalue": "MV",
    "characterstring-value": "CS", "integer-value": "IV", "positive-integer-value": "PI",
}

#: Reverse map from bacpypes3 object-type names (``analog-value``) to short codes.
_BACPYPES_TYPES = {
    "analog-input": "AI", "analog-output": "AO", "analog-value": "AV",
    "binary-input": "BI", "binary-output": "BO", "binary-value": "BV",
    "multi-state-input": "MI", "multi-state-output": "MO", "multi-state-value": "MV",
    "characterstring-value": "CS", "integer-value": "IV", "positive-integer-value": "PI",
}

#: Data types that can be used for BACnet tags.
BACNET_TYPES = ("BOOL", "REAL", "LREAL", "INT", "UINT", "DINT", "UDINT", "STRING")

MAX_INSTANCE = 4194302
DEFAULT_PROPERTY = "presentValue"

_ADDR_RE = re.compile(
    r"^(?P<obj>[A-Za-z][A-Za-z-]*?)[:,]?(?P<inst>\d+)"
    r"(?:\.(?P<prop>[A-Za-z][A-Za-z0-9]*))?"
    r"(?:@(?P<prio>\d{1,2}))?"
    r"(?::(?P<type>[A-Za-z]+)(?:\[(?P<len>\d+)\])?)?$"
)


class BACnetAddressError(ValueError):
    """Raised for invalid BACnet tag addresses."""


class BACnetConnectionLost(ConnectionLostError):
    """The device (or the local BACnet stack) does not answer."""


class BACnetDeviceError(DeviceError):
    """The device answered with an error (unknown object, write access denied...)."""


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------
@dataclass
class BACnetAddress:
    """A BACnet object property used as a tag.

    Attributes:
        area: Short object code (``AV``, ``BO``...; see :data:`BACNET_OBJECTS`).
        start: Object instance number.
        property_name: BACnet property (camelCase, default ``presentValue``).
        priority: Write priority 1..16 (0 = use the device default).
        data_type: Value type (see :data:`BACNET_TYPES`).
        string_length: Kept for interface compatibility (unused).
        bit: Always 0 (interface compatibility).
        db_number: Always 0 (interface compatibility).
    """

    area: str
    start: int = 0
    property_name: str = DEFAULT_PROPERTY
    priority: int = 0
    data_type: str = "REAL"
    string_length: int = 0
    bit: int = 0
    db_number: int = 0

    #: BACnet addresses never refer to S7 timers/counters.
    is_timer_or_counter = False

    @property
    def object_type(self) -> str:
        """BAC0 object-type name, e.g. ``analogValue``."""
        return BACNET_OBJECTS[self.area][0]

    @property
    def writable_area(self) -> bool:
        """``True`` when the operator may write this tag (outputs/values, presentValue)."""
        return BACNET_OBJECTS[self.area][2] and self.property_name == DEFAULT_PROPERTY

    @property
    def size(self) -> int:
        """Always 1 (one property)."""
        return 1

    def validate(self) -> "BACnetAddress":
        """Check all fields.

        Returns:
            ``self``.

        Raises:
            BACnetAddressError: If anything is out of range.
        """
        if self.area not in BACNET_OBJECTS:
            raise BACnetAddressError(gettext("Unknown BACnet object type '{0}'", self.area))
        if not 0 <= self.start <= MAX_INSTANCE:
            raise BACnetAddressError(gettext('Instance must be between 0 and {0}', MAX_INSTANCE))
        if not 0 <= self.priority <= 16:
            raise BACnetAddressError(gettext('Write priority must be between 1 and 16'))
        if self.data_type not in BACNET_TYPES:
            raise BACnetAddressError(gettext('{0} is not supported on BACnet (use {1})', self.data_type, ', '.join(BACNET_TYPES)))
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,62}", self.property_name or ""):
            raise BACnetAddressError(gettext('Invalid property name'))
        self.bit = self.db_number = self.string_length = 0
        return self

    def format(self, with_type: bool = False) -> str:
        """Canonical text such as ``AV3``, ``AI1.units`` or ``BO5@8``.

        Args:
            with_type: Append ``:TYPE``.

        Returns:
            Address text parseable by :func:`parse_bacnet_address`.
        """
        text = f"{self.area}{self.start}"
        if self.property_name != DEFAULT_PROPERTY:
            text += f".{self.property_name}"
        if self.priority:
            text += f"@{self.priority}"
        # A non-default type is always kept in the text so it survives re-parsing.
        if with_type or self.data_type != default_type_for(self.area, self.property_name):
            text += f":{self.data_type}"
        return text

    def __str__(self) -> str:
        """Return :meth:`format` with the type appended."""
        return self.format(with_type=True)


def parse_bacnet_address(text: str, data_type: str | None = None, string_length: int | None = None) -> BACnetAddress:
    """Parse a BACnet tag address.

    Accepted forms::

        AV3   AI1   BO12   MV2   CS4      short object codes (MSV/MSI/MSO also accepted)
        analogValue:3   analog-value,3     long object-type names
        AI1.units   AV3.outOfService       other properties (default presentValue)
        AV5@10                             write with priority 10 (1..16)
        AV3:LREAL   MV2:INT                explicit value type

    Default types: analog → REAL, binary → BOOL, multi-state → UINT,
    character string → STRING; properties other than ``presentValue`` → STRING
    unless a type is given.

    Args:
        text: Address text.
        data_type: Ignored – the object type (or a ``:TYPE`` suffix) decides.
        string_length: Unused (interface compatibility).

    Returns:
        A validated :class:`BACnetAddress`.

    Raises:
        BACnetAddressError: If the text is invalid.
    """
    if not text or not text.strip():
        raise BACnetAddressError(gettext('Address is empty'))
    m = _ADDR_RE.match(re.sub(r"\s+", "", text))
    if not m:
        raise BACnetAddressError(gettext("'{0}' is not a valid BACnet address (e.g. AV3, BO12, AI1.units, analogValue:3)", text))
    code = _ALIASES.get(m["obj"].lower())
    if code is None:
        raise BACnetAddressError(gettext("Unknown BACnet object type '{0}' (use AI, AO, AV, BI, BO, BV, MI, MO, MV, CS...)", m['obj']))
    prop = m["prop"] or DEFAULT_PROPERTY
    # The object type decides the value type; only an explicit ":TYPE" suffix overrides it
    # (the form's data-type drop-down is ignored for BACnet).
    dtype = (m["type"] or "").upper() or default_type_for(code, prop)
    return BACnetAddress(area=code, start=int(m["inst"]), property_name=prop, priority=int(m["prio"] or 0),
                         data_type=dtype).validate()


def default_type_for(code: str, prop: str) -> str:
    """Default value type of an object's property.

    Args:
        code: Short object code.
        prop: Property name.

    Returns:
        ``REAL`` (analog), ``BOOL`` (binary), ``UINT`` (multi-state),
        ``STRING`` (character string and non-presentValue properties)...
    """
    return BACNET_OBJECTS[code][3] if prop == DEFAULT_PROPERTY else "STRING"


# ---------------------------------------------------------------------------
# Value conversion
# ---------------------------------------------------------------------------
def convert_value(value, data_type: str):
    """Convert a bacpypes3 value into the tag's Python type.

    Args:
        value: Value returned by BAC0 (``Real``, ``BinaryPV``, ``Unsigned``,
            ``CharacterString``, enumerations...).
        data_type: Target data type.

    Returns:
        ``bool``, ``int``, ``float`` or ``str``.

    Raises:
        BACnetDeviceError: If the property is missing (``None``).
    """
    if value is None:
        raise BACnetDeviceError("unknown object or property")
    text = str(value)
    if data_type == "BOOL":
        if text.lower() in ("active", "true", "1", "on"):
            return True
        if text.lower() in ("inactive", "false", "0", "off"):
            return False
        return bool(value)
    if data_type in ("REAL", "LREAL"):
        return float(value)
    if data_type in ("INT", "UINT", "DINT", "UDINT"):
        return int(float(value))
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return text


def wire_value(value, address: BACnetAddress) -> str:
    """Format a raw value for a BAC0 write request.

    Args:
        value: Raw value (already reverse-scaled).
        address: Target address.

    Returns:
        Text understood by BAC0 (``active``/``inactive``, number, string).

    Raises:
        ValueError: If the value cannot be converted.
    """
    if address.data_type == "BOOL":
        return "active" if value in (True, 1, "1", "true", "True", "on", "active") else "inactive"
    if address.data_type in ("REAL", "LREAL"):
        return repr(float(value))
    if address.data_type in ("INT", "UINT", "DINT", "UDINT"):
        number = int(round(float(value)))
        if address.data_type in ("UINT", "UDINT") and number < 0:
            raise ValueError("Value must not be negative")
        return str(number)
    text = str(value)
    if not text or any(ch.isspace() for ch in text):
        raise ValueError("BACnet string writes must be a single word without spaces")
    return text


# ---------------------------------------------------------------------------
# The process-wide BAC0 stack
# ---------------------------------------------------------------------------
class BACnetNetwork:
    """Owns the BAC0 ``Lite`` stack and its asyncio event loop thread."""

    _instance: "BACnetNetwork | None" = None
    _lock = threading.Lock()
    _atexit_registered = False

    def __init__(self, local_ip: str | None = None, local_port: int = 47808, device_id: int = 3056):
        """Create (but do not start) the network.

        Args:
            local_ip: Local interface as ``ip/mask`` (``None`` = auto-detect).
            local_port: Local UDP port.
            device_id: BACnet device instance of this HMI.
        """
        self.local_ip = local_ip or None
        self.local_port = int(local_port)
        self.device_id = int(device_id)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.bacnet = None
        self.error: str | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    # -- singleton -----------------------------------------------------------
    @classmethod
    def get(cls, local_ip=None, local_port=47808, device_id=3056) -> "BACnetNetwork":
        """Return the running stack, starting it on first use.

        Args:
            local_ip: Local interface (first call only).
            local_port: Local UDP port (first call only).
            device_id: Local device instance (first call only).

        Returns:
            The started :class:`BACnetNetwork`.

        Raises:
            BACnetConnectionLost: If BAC0 is missing or the stack cannot start.
        """
        with cls._lock:
            if cls._instance is None or not cls._instance.running:
                net = cls(local_ip, local_port, device_id)
                net.start()
                if not cls._atexit_registered:
                    atexit.register(cls.shutdown)  # release the UDP port cleanly on exit
                    cls._atexit_registered = True
                cls._instance = net
            return cls._instance

    @classmethod
    def shutdown(cls) -> None:
        """Stop the running stack (used by tests and at exit)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.stop()
                cls._instance = None

    # -- lifecycle -----------------------------------------------------------
    @property
    def running(self) -> bool:
        """``True`` while the event loop thread and the BAC0 stack are up."""
        return self.bacnet is not None and self._thread is not None and self._thread.is_alive()

    def start(self, timeout: float = 15.0) -> None:
        """Start the event loop thread and the BAC0 stack.

        Args:
            timeout: Seconds to wait for the stack.

        Raises:
            BACnetConnectionLost: If the stack does not come up.
        """
        try:
            import BAC0  # noqa: F401, PLC0415
        except ImportError as exc:  # pragma: no cover
            raise BACnetConnectionLost("BAC0 is not installed (pip install BAC0)") from exc
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="bacnet-loop", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout) or self.bacnet is None:
            self.stop()
            raise BACnetConnectionLost(f"BACnet stack could not start: {self.error or 'timeout'}")

    def _run(self) -> None:
        """Event loop thread: start BAC0, then serve submitted coroutines forever."""
        asyncio.set_event_loop(self.loop)

        async def boot():
            """Create the BAC0 Lite application inside the loop."""
            import BAC0  # noqa: PLC0415

            try:
                BAC0.log_level("silence")
            except Exception:  # pragma: no cover
                pass
            kwargs = {"port": self.local_port, "deviceId": self.device_id, "ping": False,
                      "localObjName": "pyPLCHMI"}
            if self.local_ip:
                kwargs["ip"] = self.local_ip
            try:
                self.bacnet = BAC0.lite(**kwargs)
                await asyncio.sleep(0.5)
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc)
                self.bacnet = None
            finally:
                self._ready.set()

        self.loop.create_task(boot())
        self.loop.run_forever()

    def stop(self) -> None:
        """Disconnect BAC0 and stop the loop thread."""
        if self.loop is None:
            return
        if self.bacnet is not None:
            try:
                fut = asyncio.run_coroutine_threadsafe(self._disconnect(), self.loop)
                fut.result(5)
            except Exception:  # pragma: no cover
                pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        if self._thread is not None:
            self._thread.join(5)
        self.bacnet = None

    async def _disconnect(self) -> None:
        """Disconnect the BAC0 application (inside the loop)."""
        result = self.bacnet.disconnect()
        if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
            await result

    # -- request helpers -------------------------------------------------------
    def run(self, coro, timeout: float):
        """Run a coroutine in the BACnet loop and wait for its result.

        Args:
            coro: Coroutine object.
            timeout: Seconds to wait.

        Returns:
            The coroutine's result.

        Raises:
            BACnetConnectionLost: On timeout or when the stack is down.
        """
        if not self.running:
            coro.close()
            raise BACnetConnectionLost("BACnet stack is not running")
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout)
        except TimeoutError as exc:
            future.cancel()
            raise BACnetConnectionLost("No answer (timeout)") from exc


def _classify(exc: Exception) -> Exception:
    """Map a BAC0 exception to :class:`BACnetDeviceError` or :class:`BACnetConnectionLost`.

    Only a missing answer counts as a lost connection.  BAC0 raises
    ``NoResponseFromController`` both for real timeouts and for error answers
    ("APDU Abort Reason : object: unknown-object"), and a ``TypeError('apdu')``
    when a ReadPropertyMultiple contains an unknown property – those mean the
    device is alive.

    Args:
        exc: The original exception.

    Returns:
        The translated exception.
    """
    if isinstance(exc, BACnetDeviceError | BACnetConnectionLost):
        return exc
    text = str(exc)
    answered = "Reason" in text or "unknown-" in text or "denied" in text
    no_answer = type(exc).__name__ in ("NoResponseFromController", "TimeoutError", "CancelledError") \
        or isinstance(exc, (OSError, asyncio.TimeoutError))
    if no_answer and not answered:
        return BACnetConnectionLost(text or "No answer from device")
    return BACnetDeviceError(text.replace("APDU Abort Reason : ", "") or type(exc).__name__)


def _error_value(value):
    """Turn a BACnet ``ErrorType`` entry of a ReadPropertyMultiple result into an exception.

    Args:
        value: One element of the result list.

    Returns:
        :class:`BACnetDeviceError` for error entries, else ``value`` unchanged.
    """
    if type(value).__name__ == "ErrorType":
        cls, code = getattr(value, "errorClass", ""), getattr(value, "errorCode", "")
        return BACnetDeviceError(f"{cls}: {code}".strip(": ") or "error")
    return value


@dataclass(frozen=True)
class BACnetSettings:
    """Plain copy of a PLC's BACnet settings."""

    host: str = ""
    port: int = 47808
    device_id: int = 0
    priority: int = 8
    timeout: float = 3.0
    local_ip: str | None = None
    local_port: int = 47808
    local_device_id: int = 3056

    @classmethod
    def from_plc(cls, plc, local: dict | None = None) -> "BACnetSettings":
        """Build settings from a PLC model and the local stack configuration.

        Args:
            plc: The PLC model (or similar object).
            local: ``{"ip", "port", "device_id"}`` of the local stack.

        Returns:
            Frozen settings.
        """
        local = local or {}
        return cls(host=getattr(plc, "ip_address", "") or "", port=int(getattr(plc, "tcp_port", 47808) or 47808),
                   device_id=int(getattr(plc, "bacnet_device_id", 0) or 0),
                   priority=int(getattr(plc, "bacnet_priority", 8) or 8),
                   timeout=float(getattr(plc, "timeout", 3.0) or 3.0),
                   local_ip=local.get("ip") or None, local_port=int(local.get("port") or 47808),
                   local_device_id=int(local.get("device_id") or 3056))

    @property
    def target(self) -> str:
        """BAC0 device address, e.g. ``192.168.1.40`` or ``192.168.1.40:47809``."""
        return self.host if self.port == 47808 else f"{self.host}:{self.port}"

    @property
    def description(self) -> str:
        """Human readable endpoint, e.g. ``192.168.1.40 device 1001``."""
        return f"{self.target} device {self.device_id}"


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------
class BAC0Adapter:
    """Talks to real devices through the shared :class:`BACnetNetwork`."""

    library_name = "BAC0"

    def __init__(self, settings: BACnetSettings):
        """Store the settings.

        Args:
            settings: Device and local stack settings.
        """
        self.settings = settings

    @property
    def network(self) -> BACnetNetwork:
        """The process-wide BAC0 stack (started on first use)."""
        s = self.settings
        return BACnetNetwork.get(s.local_ip, s.local_port, s.local_device_id)

    def _timeout(self) -> int:
        """BAC0 per-request timeout in whole seconds."""
        return max(1, int(math.ceil(self.settings.timeout)))

    def read(self, request: str):
        """ReadProperty, e.g. ``"analogValue 3 presentValue"``.

        Args:
            request: Object type, instance and property.

        Returns:
            The raw bacpypes3 value (``None`` if the device reports nothing).
        """
        net = self.network

        async def go():
            """Perform the read inside the BACnet loop."""
            try:
                return await net.bacnet.read(f"{self.settings.target} {request}", timeout=self._timeout())
            except Exception as exc:  # noqa: BLE001
                raise _classify(exc) from exc

        return net.run(go(), self.settings.timeout + 1)

    def read_many(self, requests: list[tuple[str, int, str]]) -> list:
        """Read several properties: one ReadPropertyMultiple, else parallel reads.

        Args:
            requests: ``(objectType, instance, property)`` tuples.

        Returns:
            One entry per request: the raw value, or an exception instance.

        Raises:
            BACnetConnectionLost: If the device does not answer at all.
        """
        net = self.network
        target, timeout = self.settings.target, self._timeout()

        async def single(obj, inst, prop):
            """Read one property, returning the exception instead of raising."""
            try:
                return _error_value(await net.bacnet.read(f"{target} {obj} {inst} {prop}", timeout=timeout))
            except Exception as exc:  # noqa: BLE001
                return _classify(exc)

        async def go():
            """Try ReadPropertyMultiple, fall back to concurrent single reads."""
            if len(requests) > 1:
                spec = " ".join(f"{obj}:{inst} {prop}" for obj, inst, prop in requests)
                try:
                    values = await net.bacnet.readMultiple(f"{target} {spec}", timeout=timeout)
                    if isinstance(values, list) and len(values) == len(requests) and all(v is not None for v in values):
                        return [_error_value(v) for v in values]
                except Exception as exc:  # noqa: BLE001
                    lost = _classify(exc)
                    if isinstance(lost, BACnetConnectionLost):
                        return [lost] * len(requests)
                    # Device answered with an error (e.g. a missing property): read one by one.
            return await asyncio.gather(*(single(*r) for r in requests))

        # The library retries internally for much longer than our timeout; cap the wait so an
        # offline device cannot stall the screen (the pending request is cancelled).
        results = net.run(go(), self.settings.timeout + 2)
        if results and all(isinstance(r, BACnetConnectionLost) for r in results):
            raise results[0]
        return results

    def write(self, obj: str, inst: int, prop: str, value: str, priority: int) -> None:
        """WriteProperty with a priority.

        Args:
            obj: Object type (``analogValue``).
            inst: Instance number.
            prop: Property (``presentValue``).
            value: Value text (see :func:`wire_value`).
            priority: 1..16.

        Raises:
            BACnetDeviceError: If the device rejects the write.
            BACnetConnectionLost: If the device does not answer.
        """
        net = self.network
        request = f"{self.settings.target} {obj} {inst} {prop} {value} - {priority}"

        async def go():
            """Perform the write inside the BACnet loop and surface errors."""
            try:
                writer = getattr(net.bacnet, "_write", None)
                if writer is not None:  # BAC0 >= 2023: awaitable, raises on errors
                    return await writer(request)
                return net.bacnet.write(request)  # pragma: no cover - older BAC0
            except Exception as exc:  # noqa: BLE001
                raise _classify(exc) from exc

        net.run(go(), self.settings.timeout + 1)

    def device_info(self) -> dict:
        """Read the device's name, vendor and model (proves it answers).

        Returns:
            Dict with ``name``, ``vendor`` and ``model``.
        """
        dev = f"device {self.settings.device_id}"
        values = self.read_many([("device", self.settings.device_id, p) for p in ("objectName", "vendorName", "modelName")])
        info = {}
        for key, val in zip(("name", "vendor", "model"), values):
            if not isinstance(val, Exception) and val is not None:
                info[key] = str(val)
        if not info:
            raise BACnetConnectionLost(f"{dev} did not answer")
        return info

    def object_list(self) -> list[tuple[str, int]]:
        """Read the device's objectList.

        Returns:
            ``(bacpypes object-type name, instance)`` pairs.
        """
        values = self.read(f"device {self.settings.device_id} objectList")
        if values is None:
            raise BACnetDeviceError("objectList not available")
        return [(str(o[0]), int(o[1])) for o in values]

    def who_is(self, address: str | None = None, timeout: float = 3.0) -> list[dict]:
        """Discover devices with a Who-Is broadcast (or directed to ``address``).

        Args:
            address: Optional target address (``ip`` or ``ip:port``).
            timeout: Seconds to listen for I-Am answers.

        Returns:
            List of ``{"device_id", "address"}``.
        """
        net = self.network

        async def go():
            """Send Who-Is inside the BACnet loop."""
            return await net.bacnet.who_is(address=address, timeout=max(1, int(timeout)))

        found = []
        for iam in net.run(go(), timeout + 5) or []:
            try:
                found.append({"device_id": int(iam.iAmDeviceIdentifier[1]), "address": str(iam.pduSource)})
            except Exception:  # pragma: no cover
                continue
        return found


class SimulatedBACnetAdapter:
    """In-memory BACnet device used when ``PLC_SIMULATION`` is on."""

    library_name = "simulator"
    _memories: dict = {}
    _lock = threading.Lock()

    def __init__(self, settings: BACnetSettings):
        """Attach to (or create) the simulated device.

        Args:
            settings: Device settings (host + device id are the memory key).
        """
        self.settings = settings
        key = (settings.host, settings.port, settings.device_id)
        with self._lock:
            self.memory = self._memories.setdefault(key, {"values": {}, "written": set()})

    @classmethod
    def reset(cls) -> None:
        """Forget all simulated devices (used by tests)."""
        with cls._lock:
            cls._memories.clear()

    def _check(self) -> None:
        """Simulate an offline device for hosts ending in ``.254``.

        Raises:
            BACnetConnectionLost: For such hosts.
        """
        if self.settings.host.endswith(".254"):
            raise BACnetConnectionLost(f"No answer from {self.settings.description} (simulated offline device)")

    def read_many(self, requests: list) -> list:
        """Return simulated values (animated unless written).

        Args:
            requests: ``(objectType, instance, property)`` tuples.

        Returns:
            Values in request order.
        """
        self._check()
        now, out = time.time(), []
        for obj, inst, prop in requests:
            key = (obj, inst, prop)
            if key in self.memory["written"]:
                out.append(self.memory["values"][key])
                continue
            phase = inst * 0.7
            if prop == "objectName":
                out.append(f"{obj}{inst}")
            elif prop == "units":
                out.append("degrees-celsius" if obj.startswith("analog") else "no-units")
            elif prop != DEFAULT_PROPERTY:
                out.append("false")
            elif obj.startswith("analog"):
                out.append(round(50 + 40 * math.sin(now / 5 + phase), 3))
            elif obj.startswith("binary"):
                out.append("active" if int(now / 3 + phase) % 2 else "inactive")
            elif obj.startswith("multiState"):
                out.append(1 + int(now / 4 + phase) % 3)
            elif obj == "characterstringValue":
                out.append(time.strftime("SIM %H:%M:%S"))
            else:
                out.append(int(500 + 400 * math.sin(now / 5 + phase)))
        return out

    def read(self, request: str):
        """Read one property given as ``"objectType instance property"``.

        Args:
            request: Request text.

        Returns:
            Simulated value.
        """
        obj, inst, prop = request.split()[:3]
        return self.read_many([(obj, int(inst), prop)])[0]

    def write(self, obj: str, inst: int, prop: str, value: str, priority: int) -> None:
        """Store a written value.

        Args:
            obj: Object type.
            inst: Instance.
            prop: Property.
            value: Value text.
            priority: Ignored by the simulator.
        """
        self._check()
        key = (obj, inst, prop)
        try:
            stored = float(value) if obj.startswith("analog") else int(value) if obj.startswith(("multi", "integer", "positive")) else value
        except ValueError:
            stored = value
        self.memory["values"][key] = stored
        self.memory["written"].add(key)

    def device_info(self) -> dict:
        """Return fake identification.

        Returns:
            Dict with ``name``, ``vendor`` and ``model``.
        """
        self._check()
        return {"name": f"SIM-{self.settings.device_id}", "vendor": "pyPLCHMI", "model": "Simulated BACnet device"}

    def object_list(self) -> list[tuple[str, int]]:
        """Return a small fixed object list.

        Returns:
            ``(object-type, instance)`` pairs.
        """
        self._check()
        return [("analog-input", 1), ("analog-value", 1), ("analog-value", 2), ("binary-output", 1),
                ("binary-value", 1), ("multi-state-value", 1)]

    def who_is(self, address: str | None = None, timeout: float = 3.0) -> list[dict]:
        """Pretend one device answers.

        Args:
            address: Ignored.
            timeout: Ignored.

        Returns:
            A one-item list.
        """
        return [{"device_id": 1001, "address": "192.168.0.50"}]


# ---------------------------------------------------------------------------
# Connection used by the PLCConnectionManager
# ---------------------------------------------------------------------------
class BACnetConnection:
    """A BACnet device "connection" (BACnet/IP is connectionless UDP).

    ``connect`` verifies that the device answers (reads its objectName);
    afterwards the connection counts as open until a request times out.
    """

    def __init__(self, settings: BACnetSettings, adapter):
        """Create the connection.

        Args:
            settings: Device settings.
            adapter: :class:`BAC0Adapter` or :class:`SimulatedBACnetAdapter`.
        """
        self.settings = settings
        self.adapter = adapter
        self._open = False

    @classmethod
    def for_plc(cls, plc, simulation: bool = False, local: dict | None = None) -> "BACnetConnection":
        """Build a connection for a PLC.

        Args:
            plc: The PLC model.
            simulation: Use the simulator.
            local: Local stack configuration (``ip``, ``port``, ``device_id``).

        Returns:
            An unconnected :class:`BACnetConnection`.
        """
        settings = BACnetSettings.from_plc(plc, local)
        adapter = SimulatedBACnetAdapter(settings) if simulation else BAC0Adapter(settings)
        return cls(settings, adapter)

    def connect(self) -> None:
        """Check that the device answers.

        Raises:
            PLCError: If the device or the local stack does not respond.
        """
        from app.plc.driver import PLCError  # noqa: PLC0415

        try:
            self.adapter.device_info()
        except (BACnetConnectionLost, BACnetDeviceError) as exc:
            raise PLCError(gettext('{0}: {1}', self.settings.description, exc)) from exc
        self._open = True

    @property
    def connected(self) -> bool:
        """``True`` after a successful connect and until a request times out."""
        return self._open

    def close(self) -> None:
        """Mark as closed (the shared BAC0 stack keeps running)."""
        self._open = False

    def cpu_info(self) -> dict:
        """Identify the device.

        Returns:
            Dict with ``module``, ``name`` and ``state``.

        Raises:
            PLCError: If the device does not answer.
        """
        from app.plc.driver import PLCError  # noqa: PLC0415

        try:
            info = self.adapter.device_info()
        except (BACnetConnectionLost, BACnetDeviceError) as exc:
            raise PLCError(gettext('No answer from {0}: {1}', self.settings.description, exc)) from exc
        model = " ".join(v for v in (info.get("vendor"), info.get("model")) if v)
        return {"module": f"BACnet/IP via {self.adapter.library_name}" + (f" – {model}" if model else ""),
                "name": info.get("name", ""), "state": "responding"}

    def read_tags(self, addresses: dict, max_gap: int = 0) -> dict:
        """Read several tags from the device.

        Args:
            addresses: Mapping ``key -> BACnetAddress``.
            max_gap: Unused (interface compatibility).

        Returns:
            Mapping ``key -> ReadResult``.

        Raises:
            BACnetConnectionLost: When the device does not answer at all.
        """
        from app.plc.driver import ReadResult  # noqa: PLC0415

        keys = list(addresses)
        requests = [(addresses[k].object_type, addresses[k].start, addresses[k].property_name) for k in keys]
        try:
            values = self.adapter.read_many(requests)
        except BACnetConnectionLost:
            self._open = False
            raise
        results = {}
        for key, value in zip(keys, values):
            if isinstance(value, Exception):
                results[key] = ReadResult(error=str(value) or type(value).__name__)
                continue
            try:
                results[key] = ReadResult(value=convert_value(value, addresses[key].data_type))
            except (BACnetDeviceError, TypeError, ValueError) as exc:
                results[key] = ReadResult(error=str(exc))
        return results

    def write_tag(self, address: BACnetAddress, raw_value) -> None:
        """Write a tag's presentValue with its (or the device's) priority.

        Args:
            address: Target address.
            raw_value: Raw value.

        Raises:
            ValueError: For read-only objects/properties or bad values.
            BACnetDeviceError: If the device rejects the write.
            BACnetConnectionLost: If the device does not answer.
        """
        if not address.writable_area:
            raise ValueError(f"{BACNET_OBJECTS[address.area][1]} {address.property_name} is read-only")
        priority = address.priority or self.settings.priority or 8
        try:
            self.adapter.write(address.object_type, address.start, address.property_name,
                               wire_value(raw_value, address), priority)
        except BACnetConnectionLost:
            self._open = False
            raise

    def browse(self, limit: int = 500) -> list[dict]:
        """List the device's supported objects with name, value and units.

        Args:
            limit: Maximum number of objects.

        Returns:
            List of dicts ``{"address", "type", "instance", "name", "value", "units"}``.
        """
        objects = [(t, i) for t, i in self.adapter.object_list() if t in _BACPYPES_TYPES][:limit]
        requests = []
        for typ, inst in objects:
            obj = BACNET_OBJECTS[_BACPYPES_TYPES[typ]][0]
            requests += [(obj, inst, "objectName"), (obj, inst, DEFAULT_PROPERTY), (obj, inst, "units")]
        values = self.adapter.read_many(requests) if requests else []
        rows = []
        for n, (typ, inst) in enumerate(objects):
            code = _BACPYPES_TYPES[typ]
            name, value, units = (values[3 * n + k] for k in range(3))
            show = lambda v: "" if isinstance(v, Exception) or v is None else str(v)  # noqa: E731
            rows.append({"address": f"{code}{inst}", "type": BACNET_OBJECTS[code][1], "instance": inst,
                         "name": show(name), "value": show(value), "units": show(units),
                         "writable": BACNET_OBJECTS[code][2]})
        return rows
