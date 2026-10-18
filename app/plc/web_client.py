"""Small HTTP and WebSocket clients for *web devices* (no extra libraries).

Many field devices – energy meters, weather stations, gateways, IoT
sensors – publish their values as JSON or XML over HTTP or WebSocket.  Some
old or very small devices answer **without any HTTP headers** (the HTTP/0.9
style: the body only, then the connection is closed).  Python's
``http.client`` (and therefore ``requests``/``urllib``) rejects such answers,
so this module talks to the socket directly:

* :func:`http_get` – one ``GET`` request over HTTP or HTTPS.  The answer may
  be a normal HTTP/1.x response (``Content-Length``, ``chunked`` or "until
  the connection closes") **or** a header-less HTTP/0.9 body.  In the
  ``http09`` request mode only the classic one-line request ``GET /path``
  is sent, for devices that do not understand anything else.
* :class:`WebSocketClient` – an RFC 6455 client (``ws://`` and ``wss://``):
  handshake, masked client frames, fragmented messages, ping/pong and close.

Both support user name and password in the URL
(``http://user:pass@10.0.0.5/data.json`` → HTTP *Basic* authentication),
a total time limit (``timeout``) and switching off the certificate check
for devices with self-signed HTTPS certificates (``verify_tls=False``).
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct
import time
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from app.i18n.catalog import gettext

#: Largest answer / message accepted (protects the server's memory).
MAX_BYTES = 4 * 1024 * 1024
#: User-Agent header sent to devices.
USER_AGENT = "pyPLCHMI"
#: GUID used to compute ``Sec-WebSocket-Accept`` (RFC 6455, section 1.3).
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
#: Schemes accepted per protocol code.
SCHEMES = {"http": ("http", "https"), "websocket": ("ws", "wss")}
#: Default port per scheme.
DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443}


class WebError(Exception):
    """A web device could not be reached or sent an unusable answer."""


@dataclass
class Target:
    """A parsed device URL.

    Attributes:
        scheme: ``http``, ``https``, ``ws`` or ``wss``.
        host: Host name or IP address (without brackets for IPv6).
        port: TCP port.
        path: Path with query string (always starts with ``/``).
        username: User for Basic authentication (``""`` = none).
        password: Password for Basic authentication.
    """

    scheme: str
    host: str
    port: int
    path: str = "/"
    username: str = ""
    password: str = ""

    @property
    def secure(self) -> bool:
        """``True`` for ``https`` and ``wss`` (TLS)."""
        return self.scheme in ("https", "wss")

    @property
    def host_header(self) -> str:
        """Value of the ``Host`` header (port only when not the default)."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return host if DEFAULT_PORTS[self.scheme] == self.port else f"{host}:{self.port}"

    def auth_header(self) -> str:
        """``Authorization: Basic …`` line (with CRLF) or ``""`` without a user."""
        if not self.username:
            return ""
        token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode("ascii")
        return f"Authorization: Basic {token}\r\n"


def parse_url(url: str, protocol: str = "http") -> Target:
    """Check and split a device URL.

    Args:
        url: Full URL, e.g. ``http://10.0.0.5/status.json`` or ``wss://meter.local:8443/live``.
        protocol: ``http`` (accepts http/https) or ``websocket`` (accepts ws/wss).

    Returns:
        The :class:`Target`.

    Raises:
        WebError: If the URL is empty, has the wrong scheme or no host.
    """
    text = (url or "").strip()
    allowed = SCHEMES.get(protocol, SCHEMES["http"])
    if not text:
        raise WebError(gettext("Enter the address (URL) of the device, e.g. {0}.", example_url(protocol)))
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise WebError(gettext("Invalid URL: {0}", exc)) from exc
    scheme = (parts.scheme or "").lower()
    if scheme not in allowed:
        raise WebError(gettext("The URL must start with {0} (e.g. {1}).",
                               " / ".join(f"{s}://" for s in allowed), example_url(protocol)))
    if not parts.hostname:
        raise WebError(gettext("The URL has no host name or IP address (e.g. {0}).", example_url(protocol)))
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    if any(ch in path for ch in "\r\n "):
        raise WebError(gettext("The URL cannot contain spaces or line breaks (write a space as %20)."))
    return Target(scheme=scheme, host=parts.hostname, port=port or DEFAULT_PORTS[scheme], path=path,
                  username=unquote(parts.username or ""), password=unquote(parts.password or ""))


def example_url(protocol: str) -> str:
    """Example URL shown in messages and placeholders.

    Args:
        protocol: ``http`` or ``websocket``.
    """
    return "ws://192.168.1.50:8080/live" if protocol == "websocket" else "http://192.168.1.50/status.json"


def _open_socket(target: Target, timeout: float, verify_tls: bool) -> socket.socket:
    """Open the TCP connection (and the TLS session for https/wss).

    Args:
        target: Where to connect.
        timeout: Seconds for connecting and for each socket operation.
        verify_tls: Check the server certificate and host name.

    Returns:
        A connected socket.

    Raises:
        WebError: If the device cannot be reached or TLS fails.
    """
    try:
        sock = socket.create_connection((target.host, target.port), timeout=timeout)
    except OSError as exc:
        raise WebError(gettext("Cannot connect to {0}:{1}: {2}", target.host, target.port, _os_text(exc))) from exc
    if not target.secure:
        return sock
    try:
        ctx = ssl.create_default_context()
        if not verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx.wrap_socket(sock, server_hostname=target.host)
    except (OSError, ssl.SSLError) as exc:
        sock.close()
        hint = "" if not verify_tls else " " + gettext("(self-signed certificate? Switch off the certificate check.)")
        raise WebError(gettext("TLS error with {0}: {1}", target.host, _os_text(exc)) + hint) from exc


def _os_text(exc: Exception) -> str:
    """Short text of a socket error (``timed out`` instead of a long repr)."""
    if isinstance(exc, socket.timeout):
        return gettext("timed out")
    return getattr(exc, "strerror", None) or str(exc) or exc.__class__.__name__


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
@dataclass
class HttpResponse:
    """Answer of :func:`http_get`.

    Attributes:
        body: The body bytes.
        status: HTTP status code (``None`` for a header-less answer).
        reason: Status text (``OK``).
        version: ``HTTP/1.1``, ``HTTP/1.0`` … or ``HTTP/0.9`` (no headers).
        headers: Header names (lower case) → values.
        elapsed: Seconds from connecting to the end of the answer.
    """

    body: bytes
    status: int | None = None
    reason: str = ""
    version: str = "HTTP/0.9"
    headers: dict = field(default_factory=dict)
    elapsed: float = 0.0

    @property
    def headerless(self) -> bool:
        """``True`` when the device answered without HTTP headers."""
        return self.status is None

    def text(self) -> str:
        """The body decoded with the charset from ``Content-Type`` (UTF-8 by default)."""
        charset = "utf-8"
        ctype = self.headers.get("content-type", "")
        for part in ctype.split(";")[1:]:
            key, _, value = part.strip().partition("=")
            if key.lower() == "charset" and value:
                charset = value.strip("\"' ")
        return decode_bytes(self.body, charset)

    def summary(self) -> str:
        """One line for the user, e.g. ``HTTP/1.1 200 OK · 512 bytes · 35 ms``."""
        head = f"{self.version} {self.status} {self.reason}".strip() if self.status is not None else \
            gettext("HTTP/0.9 – answer without headers")
        return f"{head} · " + gettext("{0} bytes", len(self.body)) + f" · {self.elapsed * 1000:.0f} ms"


def decode_bytes(data: bytes, charset: str = "utf-8") -> str:
    """Bytes → text: BOM first, then ``charset``, never failing (bad bytes become �).

    Args:
        data: Raw bytes.
        charset: Charset to try when there is no byte order mark.
    """
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if data.startswith(bom):
            return data[len(bom):].decode(enc, errors="replace")
    try:
        return data.decode(charset, errors="replace")
    except LookupError:   # unknown charset name
        return data.decode("utf-8", errors="replace")


def _request_bytes(target: Target, mode: str) -> bytes:
    """The request sent to the device.

    Args:
        target: The parsed URL.
        mode: ``auto`` (HTTP/1.1 request, any answer accepted) or ``http09``
            (only the one-line request ``GET /path``).
    """
    if mode == "http09":
        return f"GET {target.path}\r\n".encode("latin-1")
    return (f"GET {target.path} HTTP/1.1\r\nHost: {target.host_header}\r\nUser-Agent: {USER_AGENT}\r\n"
            f"Accept: application/json, application/xml, text/xml, */*\r\n{target.auth_header()}"
            f"Connection: close\r\n\r\n").encode("latin-1")


def _split_head(data: bytes):
    """Find the end of the HTTP header block.

    Returns:
        ``(head, rest)`` or ``None`` while the block is not complete.
    """
    for sep in (b"\r\n\r\n", b"\n\n"):
        pos = data.find(sep)
        if pos >= 0:
            return data[:pos], data[pos + len(sep):]
    return None


def _parse_head(head: bytes) -> tuple[str, int, str, dict]:
    """Parse the status line and the headers.

    Returns:
        ``(version, status, reason, headers)``.

    Raises:
        WebError: For a malformed status line.
    """
    lines = head.decode("latin-1").replace("\r\n", "\n").split("\n")
    parts = lines[0].split(" ", 2)
    try:
        version, status = parts[0], int(parts[1])
    except (IndexError, ValueError) as exc:
        raise WebError(gettext("Malformed HTTP status line: {0}", lines[0][:80])) from exc
    headers = {}
    for line in lines[1:]:
        key, sep, value = line.partition(":")
        if sep:
            headers[key.strip().lower()] = value.strip()
    return version, status, parts[2].strip() if len(parts) > 2 else "", headers


def _dechunk(data: bytes) -> tuple[bytes, bool]:
    """Decode a ``Transfer-Encoding: chunked`` body.

    Returns:
        ``(body, complete)`` – ``complete`` once the last (size 0) chunk was seen.
    """
    out, pos = bytearray(), 0
    while True:
        end = data.find(b"\r\n", pos)
        if end < 0:
            return bytes(out), False
        try:
            size = int(data[pos:end].split(b";")[0].strip() or b"0", 16)
        except ValueError:
            raise WebError(gettext("Malformed chunked HTTP answer.")) from None
        if size == 0:
            return bytes(out), True
        start = end + 2
        if len(data) < start + size:
            return bytes(out), False
        out += data[start:start + size]
        pos = start + size + 2


def http_get(url: str | Target, timeout: float = 3.0, mode: str = "auto", verify_tls: bool = True) -> HttpResponse:
    """Send one ``GET`` request and return the answer (HTTP/1.x or header-less HTTP/0.9).

    The whole exchange must finish within ``timeout`` seconds.  A device that
    sends its data and then keeps the connection open is accepted too: when
    nothing more arrives before the time limit, the bytes received so far
    are the answer.

    Args:
        url: Device URL (``http://`` or ``https://``) or a parsed :class:`Target`.
        timeout: Time limit in seconds.
        mode: ``auto`` or ``http09`` (see :func:`_request_bytes`).
        verify_tls: Check the HTTPS certificate.

    Returns:
        The :class:`HttpResponse`.

    Raises:
        WebError: Connection errors, time-outs without data, HTTP errors
            (status 300 and above) or answers larger than :data:`MAX_BYTES`.
    """
    target = url if isinstance(url, Target) else parse_url(url, "http")
    started = time.monotonic()
    deadline = started + max(0.1, float(timeout))
    sock = _open_socket(target, timeout, verify_tls)
    data = bytearray()
    head_info = None   # (version, status, reason, headers, body_start)
    try:
        sock.sendall(_request_bytes(target, mode))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if data:
                    break
                raise WebError(gettext("No answer from {0} within {1} s.", target.host_header, timeout))
            sock.settimeout(remaining)
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                if data:
                    break    # the device sent its data but keeps the connection open
                raise WebError(gettext("No answer from {0} within {1} s.", target.host_header, timeout)) from None
            except (OSError, ssl.SSLError) as exc:
                if data:
                    break
                raise WebError(gettext("Connection to {0} failed: {1}", target.host_header, _os_text(exc))) from exc
            if not chunk:
                break
            data += chunk
            if len(data) > MAX_BYTES:
                raise WebError(gettext("The answer is larger than {0} MB.", MAX_BYTES // (1024 * 1024)))
            if head_info is None and data[:5] == b"HTTP/":
                split = _split_head(bytes(data))
                if split is not None:
                    head_info = _parse_head(split[0]) + (len(data) - len(split[1]),)
            if head_info is not None and _complete(head_info, bytes(data[head_info[4]:])):
                break
    finally:
        sock.close()
    elapsed = time.monotonic() - started
    if not data:
        raise WebError(gettext("{0} closed the connection without sending data.", target.host_header))
    if head_info is None and data[:5] == b"HTTP/":
        split = _split_head(bytes(data))
        if split is None:
            raise WebError(gettext("Incomplete HTTP answer from {0}.", target.host_header))
        head_info = _parse_head(split[0]) + (len(data) - len(split[1]),)
    if head_info is None:
        return HttpResponse(body=bytes(data), elapsed=elapsed)
    version, status, reason, headers, start = head_info
    body = bytes(data[start:])
    if "chunked" in headers.get("transfer-encoding", "").lower():
        body, _ = _dechunk(body)
    elif headers.get("content-length", "").isdigit():
        body = body[:int(headers["content-length"])]
    response = HttpResponse(body=body, status=status, reason=reason, version=version, headers=headers, elapsed=elapsed)
    if status >= 300:
        where = f" → {headers['location']}" if "location" in headers and 300 <= status < 400 else ""
        raise WebError(gettext("The device answered {0} {1}{2}", status, reason, where))
    return response


def _complete(head_info: tuple, body: bytes) -> bool:
    """Tell whether an HTTP answer with headers is complete.

    Args:
        head_info: Result of :func:`_parse_head` plus the body offset.
        body: Body bytes received so far.
    """
    headers = head_info[3]
    if "chunked" in headers.get("transfer-encoding", "").lower():
        return _dechunk(body)[1]
    length = headers.get("content-length", "")
    if length.isdigit():
        return len(body) >= int(length)
    return head_info[1] in (204, 304)


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------
class WebSocketClosed(WebError):
    """The WebSocket connection was closed (by the device or the network)."""


class WebSocketClient:
    """Minimal RFC 6455 WebSocket client (text and binary messages).

    Typical use::

        ws = WebSocketClient("ws://10.0.0.7:81/", timeout=3)
        ws.connect()
        ws.send_text('{"subscribe": "all"}')
        message = ws.receive(timeout=5)   # str (text) or bytes (binary)
        ws.close()
    """

    def __init__(self, url: str | Target, timeout: float = 3.0, verify_tls: bool = True):
        """Prepare a client (no network activity yet).

        Args:
            url: ``ws://`` / ``wss://`` URL or a parsed :class:`Target`.
            timeout: Seconds for connecting and the handshake.
            verify_tls: Check the certificate for ``wss://``.
        """
        self.target = url if isinstance(url, Target) else parse_url(url, "websocket")
        self.timeout = max(0.1, float(timeout))
        self.verify_tls = verify_tls
        self.sock: socket.socket | None = None
        self._buffer = bytearray()
        #: ``time.monotonic()`` of the last frame received (any kind, also pong).
        self.last_frame = 0.0

    # -- connection ----------------------------------------------------------
    def connect(self) -> None:
        """Open the connection and perform the opening handshake.

        Raises:
            WebError: If the device cannot be reached or refuses the upgrade.
        """
        target = self.target
        self.sock = _open_socket(target, self.timeout, self.verify_tls)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (f"GET {target.path} HTTP/1.1\r\nHost: {target.host_header}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                   f"User-Agent: {USER_AGENT}\r\n{target.auth_header()}\r\n")
        deadline = time.monotonic() + self.timeout
        try:
            self.sock.sendall(request.encode("latin-1"))
            data = bytearray()
            while _split_head(bytes(data)) is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise socket.timeout()
                self.sock.settimeout(remaining)
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise WebError(gettext("{0} closed the connection during the WebSocket handshake.",
                                           target.host_header))
                data += chunk
                if len(data) > 65536:
                    raise WebError(gettext("Invalid WebSocket handshake answer."))
        except (OSError, ssl.SSLError) as exc:
            self.close()
            raise WebError(gettext("WebSocket handshake with {0} failed: {1}", target.host_header,
                                   _os_text(exc))) from exc
        except WebError:
            self.close()
            raise
        head, rest = _split_head(bytes(data))
        try:
            _version, status, reason, headers = _parse_head(head)
        except WebError:
            self.close()
            raise
        expected = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode("ascii")
        if status != 101:
            self.close()
            raise WebError(gettext("The device refused the WebSocket connection: {0} {1}", status, reason))
        if headers.get("sec-websocket-accept") != expected:
            self.close()
            raise WebError(gettext("Invalid WebSocket handshake answer (Sec-WebSocket-Accept)."))
        self._buffer = bytearray(rest)
        self.last_frame = time.monotonic()

    @property
    def connected(self) -> bool:
        """``True`` while the socket is open."""
        return self.sock is not None

    def close(self) -> None:
        """Send a close frame (best effort) and close the socket."""
        sock, self.sock = self.sock, None
        if sock is None:
            return
        try:
            sock.settimeout(0.5)
            sock.sendall(self._frame(0x8, struct.pack("!H", 1000)))
        except (OSError, ssl.SSLError):
            pass
        try:
            sock.close()
        except OSError:
            pass

    # -- frames ----------------------------------------------------------------
    @staticmethod
    def _frame(opcode: int, payload: bytes) -> bytes:
        """Build one masked client frame (FIN set).

        Args:
            opcode: 0x1 text, 0x2 binary, 0x8 close, 0x9 ping, 0xA pong.
            payload: Frame data.
        """
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB", 0x80 | opcode, 0x80 | length)
        elif length < 65536:
            header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, length)
        else:
            header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, length)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return header + mask + masked

    def _send(self, opcode: int, payload: bytes) -> None:
        """Send a frame.

        Raises:
            WebSocketClosed: If the connection is closed or broken.
        """
        if self.sock is None:
            raise WebSocketClosed(gettext("The WebSocket connection is closed."))
        try:
            self.sock.settimeout(self.timeout)
            self.sock.sendall(self._frame(opcode, payload))
        except (OSError, ssl.SSLError) as exc:
            self.close()
            raise WebSocketClosed(gettext("WebSocket send failed: {0}", _os_text(exc))) from exc

    def send_text(self, text: str) -> None:
        """Send a text message (e.g. a subscribe command the device expects)."""
        self._send(0x1, text.encode("utf-8"))

    def ping(self, payload: bytes = b"") -> None:
        """Send a ping frame (the device must answer with a pong)."""
        self._send(0x9, payload)

    def _read_exact(self, size: int, deadline: float) -> bytes:
        """Take ``size`` bytes from the buffer, receiving more as needed.

        Raises:
            socket.timeout: When the deadline passes first.
            WebSocketClosed: When the connection closes.
        """
        while len(self._buffer) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout()
            self.sock.settimeout(remaining)
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                raise
            except (OSError, ssl.SSLError) as exc:
                self.close()
                raise WebSocketClosed(gettext("WebSocket connection lost: {0}", _os_text(exc))) from exc
            if not chunk:
                self.close()
                raise WebSocketClosed(gettext("The device closed the WebSocket connection."))
            self._buffer += chunk
        out = bytes(self._buffer[:size])
        del self._buffer[:size]
        return out

    def _read_frame(self, deadline: float) -> tuple[bool, int, bytes]:
        """Read one frame: ``(fin, opcode, payload)``.

        A frame that is only partly received when the deadline passes stays
        in the buffer only if its header was not consumed – so the deadline
        is checked before a new frame is started and the rest of a started
        frame is read with the connection timeout.
        """
        head = self._read_exact(2, deadline)
        later = max(deadline, time.monotonic() + self.timeout)   # finish a started frame
        fin, opcode = bool(head[0] & 0x80), head[0] & 0x0F
        masked, length = bool(head[1] & 0x80), head[1] & 0x7F
        try:
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2, later))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8, later))[0]
            if length > MAX_BYTES:
                self.close()
                raise WebSocketClosed(gettext("WebSocket message larger than {0} MB.", MAX_BYTES // (1024 * 1024)))
            mask = self._read_exact(4, later) if masked else b""
            payload = self._read_exact(length, later) if length else b""
        except socket.timeout:
            # The header is already consumed: the stream cannot be resynchronised.
            self.close()
            raise WebSocketClosed(gettext("The device stopped in the middle of a WebSocket frame.")) from None
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.last_frame = time.monotonic()
        return fin, opcode, payload

    def receive(self, timeout: float | None = None):
        """Wait for the next complete message.

        Pings are answered automatically; pongs are ignored.

        Args:
            timeout: Seconds to wait (default: the connection timeout).

        Returns:
            ``str`` for text messages, ``bytes`` for binary messages, or
            ``None`` when no message arrived in time.

        Raises:
            WebSocketClosed: If the connection was closed or broke.
        """
        if self.sock is None:
            raise WebSocketClosed(gettext("The WebSocket connection is closed."))
        deadline = time.monotonic() + (self.timeout if timeout is None else max(0.0, timeout))
        parts, kind = [], None
        while True:
            try:
                fin, opcode, payload = self._read_frame(deadline)
            except socket.timeout:
                if parts:   # a fragmented message must be finished
                    deadline = time.monotonic() + self.timeout
                    continue
                return None
            if opcode == 0x8:   # close
                code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else 1005
                self.close()
                raise WebSocketClosed(gettext("The device closed the WebSocket connection (code {0}).", code))
            if opcode == 0x9:   # ping -> pong
                self._send(0xA, payload)
                continue
            if opcode == 0xA:   # pong
                continue
            if opcode in (0x1, 0x2):
                kind, parts = opcode, [payload]
            elif opcode == 0x0 and kind is not None:
                parts.append(payload)
            else:
                continue   # unknown / unexpected frame: ignore
            if sum(len(p) for p in parts) > MAX_BYTES:
                self.close()
                raise WebSocketClosed(gettext("WebSocket message larger than {0} MB.", MAX_BYTES // (1024 * 1024)))
            if fin:
                data = b"".join(parts)
                return decode_bytes(data) if kind == 0x1 else data
