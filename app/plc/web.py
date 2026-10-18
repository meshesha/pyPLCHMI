"""Web devices: values read from JSON or XML sent over HTTP(S) or WebSocket.

Two protocols use this module:

* ``http`` – the HMI asks the device with a ``GET`` request – or a ``POST``
  with a body (parameters, a JSON / XML command) – every *poll
  interval* (HTTP or HTTPS; answers with headers or header-less HTTP/0.9,
  see :mod:`app.plc.web_client`);
* ``websocket`` – the HMI keeps a WebSocket (``ws://`` / ``wss://``) open
  and the device sends messages.  Optionally a text message (for example a
  subscribe command) is sent after connecting and then every poll interval.

The address of a tag is the **key path** of its value in the document, for
example ``meter.power`` (JSON) or ``status/sensor[2]/temp`` (XML) – see
:mod:`app.plc.web_data`.  Web device tags are **read only**.

The connection object (:class:`WebConnection`) plugs into the
:class:`~app.plc.driver.PLCConnectionManager` like the Modbus and BACnet
drivers: ``connect``/``connected``/``close``/``read_tags``/``write_tag``/
``cpu_info``.  So the usual features work unchanged – communication
timeout, communication alarm, last-good values, history, alarms and
automations.

:func:`probe` performs one test exchange for the *Add device* wizard and
lists the keys found in the data.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass

from app.i18n.catalog import gettext
from app.plc.errors import ConnectionLostError, DeviceError
from app.plc.web_client import (METHODS, WebError, WebSocketClient, decode_bytes, http_get, parse_headers,
                                parse_url)
from app.plc.web_data import (FORMATS, MAX_KEYS, MAX_PATH, WEB_TYPES, Missing, WebDataError, check_path, convert,
                              display, flatten, parse_document, pretty, resolve, suggest_type)

log = logging.getLogger(__name__)

#: Protocol codes handled here.
WEB_PROTOCOLS = ("http", "websocket")
#: Request modes for HTTP devices.
HTTP_MODES = {
    "auto": "Automatic – HTTP/1.1 request; answers with or without headers (HTTP/0.9) are accepted",
    "http09": "HTTP/0.9 – one-line request 'GET /path' only (very old or minimal devices)",
}
#: Seconds between WebSocket pings (a device that does not answer for
#: this long plus the timeout counts as disconnected).
PING_EVERY = 10.0
#: Shortest and longest poll interval (seconds).
MIN_INTERVAL, MAX_INTERVAL = 0.1, 3600.0
#: Shortest and longest timeout (seconds).
MIN_TIMEOUT, MAX_TIMEOUT = 0.1, 60.0
#: Longest message sent to a WebSocket device.
MAX_MESSAGE = 1000
#: Longest POST body.
MAX_BODY = 10000
#: Longest text of the extra headers.
MAX_HEADERS_TEXT = 4000
#: Content type used for a POST body when none is given.
DEFAULT_CONTENT_TYPE = "application/x-www-form-urlencoded"
#: Content types offered in the forms (any other may be typed).
CONTENT_TYPES = ("application/x-www-form-urlencoded", "application/json", "application/xml", "text/xml",
                 "text/plain")

_TYPE_SUFFIX = re.compile(r"^(.*):([A-Za-z]+)$", re.DOTALL)


def is_web(protocol: str | None) -> bool:
    """``True`` for the web device protocols (``http`` and ``websocket``)."""
    return protocol in WEB_PROTOCOLS


# ---------------------------------------------------------------------------
# Tag address
# ---------------------------------------------------------------------------
@dataclass
class WebAddress:
    """The key path of a value in the device's JSON / XML data.

    Attributes:
        path: Key path (``meter.power``, ``status/sensor[1]/temp``).
        data_type: Tag data type (see :data:`~app.plc.web_data.WEB_TYPES`).
        string_length: Maximum length for ``STRING``.
        area: Always ``"WB"`` (stored in ``DataPoint.area``).
        db_number: Always 0 (interface compatibility).
        start: Always 0.
        bit: Always 0.
    """

    path: str = ""
    data_type: str = "LREAL"
    string_length: int = 0
    area: str = "WB"
    db_number: int = 0
    start: int = 0
    bit: int = 0

    #: Never an S7 timer/counter.
    is_timer_or_counter = False
    #: Web device tags are read only.
    writable_area = False

    @property
    def size(self) -> int:
        """Always 1 (one value)."""
        return 1

    def validate(self) -> "WebAddress":
        """Check the data type and the key syntax (JSON or XML form).

        Returns:
            ``self``.

        Raises:
            WebDataError: With instructions.
        """
        if self.data_type not in WEB_TYPES:
            raise WebDataError(gettext("{0} is not available for web device tags (use {1})",
                                       self.data_type, ", ".join(WEB_TYPES)))
        if self.data_type != "STRING":
            self.string_length = 0
        try:
            self.path = check_path(self.path, "json")
        except WebDataError as json_error:
            try:
                self.path = check_path(self.path, "xml")
            except WebDataError:
                raise json_error from None
        return self

    def format(self, with_type: bool = False) -> str:
        """The key path (``meter.power:REAL`` with the type)."""
        return self.path + (f":{self.data_type}" if with_type else "")

    def __str__(self) -> str:
        """Same as :meth:`format`."""
        return self.format()


def parse_web_address(text: str | None, data_type: str | None = None, string_length: int | None = None) -> WebAddress:
    """Build a web address from the text typed in the tag form.

    Args:
        text: Key path, optionally with ``:TYPE`` (``meter.power:REAL``).
        data_type: Type from the form's drop-down.
        string_length: STRING length from the form.

    Returns:
        A validated :class:`WebAddress`.

    Raises:
        WebDataError: If the key or the type is invalid.
    """
    raw = (text or "").strip()
    dtype = (data_type or "LREAL").upper()
    match = _TYPE_SUFFIX.match(raw)
    if match and match.group(2).upper() in WEB_TYPES:
        raw, dtype = match.group(1).strip(), match.group(2).upper()
    length = int(string_length or 40) if dtype == "STRING" else 0
    return WebAddress(path=raw[:MAX_PATH + 1], data_type=dtype, string_length=length).validate()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
@dataclass
class WebSettings:
    """Connection settings of a web device (from a PLC row or from the wizard).

    Attributes:
        protocol: ``http`` or ``websocket``.
        url: Device URL.
        data_format: ``json`` or ``xml``.
        poll_interval: Seconds between requests (HTTP) or between repeated
            messages (WebSocket with :attr:`ws_message`).
        timeout: Time limit of one request / the handshake, in seconds.
        http_mode: ``auto`` or ``http09`` (HTTP only).
        verify_tls: Check HTTPS / WSS certificates.
        ws_message: Text sent after connecting (WebSocket only; ``""`` = none).
        http_method: ``GET`` or ``POST`` (HTTP only).
        http_body: Body sent with ``POST`` (parameters, a JSON or XML command…).
        http_content_type: ``Content-Type`` of the body.
        http_headers: Extra request headers, one ``Name: value`` per line
            (HTTP requests and the WebSocket handshake).
    """

    protocol: str = "http"
    url: str = ""
    data_format: str = "json"
    poll_interval: float = 1.0
    timeout: float = 3.0
    http_mode: str = "auto"
    verify_tls: bool = True
    ws_message: str = ""
    http_method: str = "GET"
    http_body: str = ""
    http_content_type: str = DEFAULT_CONTENT_TYPE
    http_headers: str = ""

    @property
    def headers(self) -> list[tuple[str, str]]:
        """The extra headers as ``[(name, value), …]`` (invalid text → none; it is checked when saved)."""
        try:
            return parse_headers(self.http_headers)
        except WebError:
            return []

    def request_options(self) -> dict:
        """Keyword arguments for :func:`~app.plc.web_client.http_get`."""
        return {"timeout": self.timeout, "mode": self.http_mode, "verify_tls": self.verify_tls,
                "method": self.http_method, "body": self.http_body if self.http_method == "POST" else b"",
                "content_type": self.http_content_type, "headers": self.headers}

    @classmethod
    def from_plc(cls, plc) -> "WebSettings":
        """Settings of a saved PLC row."""
        return cls(protocol=plc.protocol, url=plc.url or "", data_format=plc.data_format or "json",
                   poll_interval=float(plc.poll_interval or 1.0), timeout=float(plc.timeout or 3.0),
                   http_mode=plc.http_mode or "auto", verify_tls=bool(plc.verify_tls),
                   ws_message=plc.ws_message or "", http_method=plc.http_method or "GET",
                   http_body=plc.http_body or "", http_content_type=plc.http_content_type or "",
                   http_headers=plc.http_headers or "")

    @classmethod
    def from_dict(cls, data: dict) -> tuple["WebSettings", dict]:
        """Read and check settings sent by the wizard.

        Args:
            data: ``protocol``, ``url``, ``data_format``, ``poll_interval``,
                ``timeout``, ``http_mode``, ``verify_tls``, ``ws_message``,
                ``http_method``, ``http_body``, ``http_content_type``, ``http_headers``.

        Returns:
            ``(settings, errors)`` – ``errors`` maps field names to messages.
        """
        errors: dict[str, str] = {}
        protocol = str(data.get("protocol") or "http")
        if protocol not in WEB_PROTOCOLS:
            errors["protocol"] = gettext("Choose HTTP or WebSocket.")
            protocol = "http"
        url = str(data.get("url") or "").strip()
        try:
            parse_url(url, protocol)
        except WebError as exc:
            errors["url"] = str(exc)
        if len(url) > 500:
            errors["url"] = gettext("The URL is too long (at most {0} characters).", 500)
        fmt = str(data.get("data_format") or "json")
        if fmt not in FORMATS:
            errors["data_format"] = gettext("Choose JSON or XML.")

        def number(name, low, high, default):
            """Read a number field within ``low..high``."""
            try:
                value = float(data.get(name, default))
            except (TypeError, ValueError):
                value = float("nan")
            if not low <= value <= high:
                errors[name] = gettext("Enter a number from {0} to {1}.", low, high)
                return default
            return value

        interval = number("poll_interval", MIN_INTERVAL, MAX_INTERVAL, 1.0)
        timeout = number("timeout", MIN_TIMEOUT, MAX_TIMEOUT, 3.0)
        mode = str(data.get("http_mode") or "auto")
        if mode not in HTTP_MODES:
            errors["http_mode"] = gettext("Choose a request mode.")
            mode = "auto"
        message = str(data.get("ws_message") or "")
        if len(message) > MAX_MESSAGE:
            errors["ws_message"] = gettext("The message is too long (at most {0} characters).", MAX_MESSAGE)
        verify = data.get("verify_tls", True)
        verify = verify if isinstance(verify, bool) else str(verify).lower() in ("1", "true", "on", "yes", "y")
        method = str(data.get("http_method") or "GET").upper()
        if method not in METHODS:
            errors["http_method"] = gettext("Choose GET or POST.")
            method = "GET"
        elif protocol == "http" and method == "POST" and mode == "http09":
            errors["http_method"] = gettext("The HTTP/0.9 request mode can only send GET – choose the automatic "
                                            "mode for POST.")
        body = str(data.get("http_body") or "")
        if len(body) > MAX_BODY:
            errors["http_body"] = gettext("The body is too long (at most {0} characters).", MAX_BODY)
        ctype = str(data.get("http_content_type") or "").strip()
        if len(ctype) > 100 or any(ord(ch) < 32 for ch in ctype):
            errors["http_content_type"] = gettext("Enter a content type such as {0}.", "application/json")
        headers = str(data.get("http_headers") or "")
        try:
            if len(headers) > MAX_HEADERS_TEXT:
                raise WebError(gettext("The headers are too long (at most {0} characters).", MAX_HEADERS_TEXT))
            parse_headers(headers)
        except WebError as exc:
            errors["http_headers"] = str(exc)
        return cls(protocol=protocol, url=url, data_format=fmt if fmt in FORMATS else "json",
                   poll_interval=interval, timeout=timeout, http_mode=mode, verify_tls=verify,
                   ws_message=message, http_method=method, http_body=body,
                   http_content_type=ctype or DEFAULT_CONTENT_TYPE, http_headers=headers.strip()), errors

    def apply(self, plc) -> None:
        """Copy the settings into a PLC row (host and port are filled from the URL)."""
        target = parse_url(self.url, self.protocol)
        plc.protocol = self.protocol
        plc.url = self.url
        plc.ip_address = target.host[:64]
        plc.tcp_port = target.port
        plc.data_format = self.data_format
        plc.poll_interval = self.poll_interval
        plc.timeout = self.timeout
        plc.http_mode = self.http_mode
        plc.verify_tls = self.verify_tls
        plc.ws_message = self.ws_message
        plc.http_method = self.http_method
        plc.http_body = self.http_body
        plc.http_content_type = self.http_content_type
        plc.http_headers = self.http_headers


# ---------------------------------------------------------------------------
# Reading values from a document
# ---------------------------------------------------------------------------
def read_values(doc, fmt: str, addresses: dict, merged: dict | None = None) -> dict:
    """Look up every tag's key in a document and convert the values.

    Args:
        doc: Parsed document.
        fmt: ``json`` or ``xml``.
        addresses: ``key -> WebAddress``.
        merged: Values from earlier WebSocket messages (``path -> value``),
            used for keys missing from the latest message.

    Returns:
        ``key -> ReadResult``.
    """
    from app.plc.driver import ReadResult  # noqa: PLC0415

    results = {}
    for key, addr in addresses.items():
        try:
            try:
                value = resolve(doc, addr.path, fmt)
            except Missing:
                if merged is None or addr.path not in merged:
                    raise
                value = merged[addr.path]
            results[key] = ReadResult(value=convert(value, addr.data_type, addr.string_length))
        except Missing:
            results[key] = ReadResult(error=gettext("key '{0}' is not in the data", addr.path))
        except WebDataError as exc:
            results[key] = ReadResult(error=str(exc))
    return results


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
class WebConnection:
    """Connection object of an HTTP or WebSocket device (used by the connection manager)."""

    def __init__(self, settings: WebSettings, name: str = ""):
        """Prepare a connection (no network activity yet).

        Args:
            settings: Device settings.
            name: PLC name for log messages.
        """
        self.settings = settings
        self.name = name or settings.url
        self.target = parse_url(settings.url, settings.protocol)
        self._lock = threading.Lock()
        self._alive = False
        self._error: str | None = None
        self._doc = None
        self._merged: dict = {}
        self._fetched = 0.0          # monotonic time of the last HTTP request
        self._summary = ""
        self._messages = 0
        self._client: WebSocketClient | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @classmethod
    def for_plc(cls, plc) -> "WebConnection":
        """Connection for a saved PLC row."""
        return cls(WebSettings.from_plc(plc), name=plc.name)

    @property
    def websocket(self) -> bool:
        """``True`` for WebSocket devices."""
        return self.settings.protocol == "websocket"

    # -- HTTP ------------------------------------------------------------------
    def _fetch(self) -> None:
        """Request the document once (HTTP) and keep it.

        Raises:
            WebError, WebDataError: On any failure.
        """
        s = self.settings
        self._fetched = time.monotonic()
        response = http_get(self.target, **s.request_options())
        doc = parse_document(response.text(), s.data_format)
        with self._lock:
            self._doc, self._summary = doc, response.summary()

    # -- WebSocket -------------------------------------------------------------
    def _accept(self, message) -> None:
        """Keep a received WebSocket message (invalid messages are logged and skipped)."""
        text = decode_bytes(message) if isinstance(message, bytes) else message
        try:
            doc = parse_document(text, self.settings.data_format)
        except WebDataError as exc:
            log.debug("Web device %s: message skipped: %s", self.name, exc)
            return
        with self._lock:
            self._doc = doc
            self._messages += 1
            self._merged.update(flatten(doc, self.settings.data_format))

    def _run(self) -> None:
        """Background thread: receive messages, repeat the request message, ping."""
        s, client = self.settings, self._client
        now = time.monotonic()
        next_send = now + s.poll_interval if s.ws_message else None
        next_ping = now + PING_EVERY
        try:
            while not self._stop.is_set():
                message = client.receive(timeout=min(0.5, s.poll_interval))
                if message is not None:
                    self._accept(message)
                now = time.monotonic()
                if next_send is not None and now >= next_send:
                    client.send_text(s.ws_message)
                    next_send = now + s.poll_interval
                if now >= next_ping:
                    client.ping()
                    next_ping = now + PING_EVERY
                if now - client.last_frame > PING_EVERY + s.timeout:
                    raise WebError(gettext("No data and no answer to ping for {0} s.", round(now - client.last_frame)))
        except WebError as exc:
            if not self._stop.is_set():
                log.warning("Web device %s: %s", self.name, exc)
                self._error = str(exc)
        finally:
            self._alive = False
            client.close()

    # -- connection interface --------------------------------------------------
    def connect(self) -> None:
        """HTTP: request the document once.  WebSocket: open the connection.

        Raises:
            PLCError: If the device cannot be reached or sends unusable data.
        """
        from app.plc.driver import PLCError  # noqa: PLC0415

        self._error = None
        try:
            if not self.websocket:
                self._fetch()
            else:
                client = WebSocketClient(self.target, timeout=self.settings.timeout,
                                         verify_tls=self.settings.verify_tls, headers=self.settings.headers)
                client.connect()
                if self.settings.ws_message:
                    client.send_text(self.settings.ws_message)
                self._client = client
                self._summary = gettext("WebSocket connected")
                self._stop.clear()
                self._thread = threading.Thread(target=self._run, name=f"ws-{self.name}", daemon=True)
                self._alive = True
                self._thread.start()
        except (WebError, WebDataError) as exc:
            raise PLCError(str(exc)) from exc
        self._alive = True

    @property
    def connected(self) -> bool:
        """``True`` after a successful request / while the WebSocket is open."""
        return self._alive

    def close(self) -> None:
        """Stop the WebSocket thread / forget the HTTP state."""
        self._alive = False
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        client, self._client = self._client, None
        if client is not None:
            client.close()

    def cpu_info(self) -> dict:
        """Device information for *Test connection* (HTTP: the answer line; WebSocket: first message).

        Raises:
            PLCError: If no usable data arrives.
        """
        from app.plc.driver import PLCError  # noqa: PLC0415

        if self.websocket:
            deadline = time.monotonic() + self.settings.timeout
            while self._messages == 0 and self._alive and time.monotonic() < deadline:
                time.sleep(0.05)
            if self._messages == 0:
                raise PLCError(self._error or gettext("Connected, but no valid {0} message arrived within {1} s.",
                                                      FORMATS[self.settings.data_format], self.settings.timeout))
        return {"module": f"{self._summary} · {FORMATS[self.settings.data_format]}",
                "name": self.settings.url, "state": gettext("{0} keys", len(self.keys()))}

    def keys(self) -> list[tuple[str, object]]:
        """Keys and values of the current document (empty before the first data)."""
        with self._lock:
            doc = self._doc
        return [] if doc is None else flatten(doc, self.settings.data_format)

    def read_tags(self, addresses: dict, max_gap: int = 0) -> dict:
        """Read tags from the latest document (HTTP: a new request once the poll interval has passed).

        Args:
            addresses: ``key -> WebAddress``.
            max_gap: Unused (interface compatibility).

        Returns:
            ``key -> ReadResult``.

        Raises:
            ConnectionLostError: When the device does not answer / the WebSocket closed.
        """
        from app.plc.driver import ReadResult  # noqa: PLC0415

        if not self.websocket:
            if time.monotonic() - self._fetched >= self.settings.poll_interval:
                try:
                    self._fetch()
                except (WebError, WebDataError) as exc:
                    self._alive = False
                    raise ConnectionLostError(str(exc)) from exc
        elif not self._alive:
            raise ConnectionLostError(self._error or gettext("The WebSocket connection is closed."))
        with self._lock:
            doc, merged = self._doc, dict(self._merged)
        if doc is None:
            return {key: ReadResult(error=gettext("waiting for the first message from the device"))
                    for key in addresses}
        return read_values(doc, self.settings.data_format, addresses, merged if self.websocket else None)

    def write_tag(self, address, raw_value) -> None:
        """Web device tags cannot be written.

        Raises:
            DeviceError: Always.
        """
        raise DeviceError(gettext("Web device tags are read only."))


# ---------------------------------------------------------------------------
# Wizard test
# ---------------------------------------------------------------------------
def probe(settings: WebSettings, extra_wait: float = 0.5) -> dict:
    """One test exchange with a device; lists the received data and its keys.

    Args:
        settings: Device settings.
        extra_wait: WebSocket only – after the first message, keep collecting
            messages for this many seconds (devices that send different
            values in different messages).

    Returns:
        ``{"ok": True, "summary", "raw", "rows", "truncated"}`` where each row is
        ``{"path", "value", "display", "data_type", "string_length"}``, or
        ``{"ok": False, "error", "raw"}`` (``raw`` = the text received, if any).
    """
    raw, summary = "", ""
    fmt = settings.data_format
    try:
        target = parse_url(settings.url, settings.protocol)
        started = time.monotonic()
        if settings.protocol == "websocket":
            client = WebSocketClient(target, timeout=settings.timeout, verify_tls=settings.verify_tls,
                                     headers=settings.headers)
            client.connect()
            texts = []
            try:
                if settings.ws_message:
                    client.send_text(settings.ws_message)
                first = client.receive(timeout=settings.timeout)
                if first is None:
                    return {"ok": False, "raw": "", "error": gettext(
                        "Connected, but the device sent no message within {0} s. Does it expect a message first?",
                        settings.timeout)}
                first_ms = (time.monotonic() - started) * 1000
                texts.append(decode_bytes(first) if isinstance(first, bytes) else first)
                end = time.monotonic() + extra_wait
                while time.monotonic() < end:
                    more = client.receive(timeout=max(0.0, end - time.monotonic()))
                    if more is None:
                        break
                    texts.append(decode_bytes(more) if isinstance(more, bytes) else more)
            finally:
                client.close()
            raw = texts[0]
            summary = gettext("WebSocket · {0} message(s) · first after {1} ms", len(texts), round(first_ms))
            docs = [parse_document(t, fmt) for t in texts]
        else:
            response = http_get(target, **settings.request_options())
            raw, summary = response.text(), response.summary()
            docs = [parse_document(raw, fmt)]
    except (WebError, WebDataError) as exc:
        return {"ok": False, "error": str(exc), "raw": pretty(raw, fmt) if raw else ""}
    merged: dict = {}
    for doc in docs:
        merged.update(flatten(doc, fmt))
    rows = []
    for path, value in merged.items():
        dtype, length = suggest_type(value)
        rows.append({"path": path, "value": value if isinstance(value, (bool, int, float, str)) or value is None
                     else display(value), "display": display(value), "data_type": dtype, "string_length": length})
    return {"ok": True, "summary": summary, "raw": pretty(raw, fmt), "rows": rows,
            "truncated": len(rows) >= MAX_KEYS}
