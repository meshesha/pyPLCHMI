"""Tiny local servers used by the web device tests (no network outside 127.0.0.1).

* :class:`RawServer` – answers every connection with a function of the
  request bytes (used for header-less HTTP/0.9 answers, chunked answers,
  devices that keep the connection open …).
* :class:`WSServer` – a minimal RFC 6455 WebSocket server that sends
  scripted messages and records what the client sends.
"""

from __future__ import annotations

import base64
import hashlib
import socket
import struct
import threading
import time

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class RawServer:
    """TCP server on 127.0.0.1: ``handler(conn, request_bytes)`` answers each connection."""

    def __init__(self, handler, read_request: bool = True):
        """Start listening (in a daemon thread).

        Args:
            handler: ``handler(conn, request)``; the connection is closed after it returns.
            read_request: Read the request (until a blank line or a single
                ``GET`` line) before calling the handler.
        """
        self.handler = handler
        self.read_request = read_request
        self.requests: list[bytes] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self._stop = False
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def url(self, path: str = "/", scheme: str = "http") -> str:
        """URL of this server."""
        return f"{scheme}://127.0.0.1:{self.port}{path}"

    def _loop(self) -> None:
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn) -> None:
        try:
            request = b""
            if self.read_request:
                conn.settimeout(2)
                while b"\r\n\r\n" not in request and not (request.startswith(b"GET ") and request.endswith(b"\r\n")
                                                           and b"HTTP/" not in request):
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    request += chunk
            self.requests.append(request)
            self.handler(conn, request)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self) -> None:
        """Stop the server."""
        self._stop = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)   # wakes the thread blocked in accept()
        except OSError:
            pass
        self.sock.close()


def ws_frame(opcode: int, payload: bytes, fin: bool = True) -> bytes:
    """An unmasked server frame."""
    first = (0x80 if fin else 0) | opcode
    if len(payload) < 126:
        return struct.pack("!BB", first, len(payload)) + payload
    if len(payload) < 65536:
        return struct.pack("!BBH", first, 126, len(payload)) + payload
    return struct.pack("!BBQ", first, 127, len(payload)) + payload


def read_client_frame(conn) -> tuple[int, bytes]:
    """Read one masked client frame: ``(opcode, payload)``."""
    def exact(n):
        data = b""
        while len(data) < n:
            chunk = conn.recv(n - len(data))
            if not chunk:
                raise ConnectionError("closed")
            data += chunk
        return data
    b1, b2 = exact(2)
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack("!H", exact(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", exact(8))[0]
    mask = exact(4) if b2 & 0x80 else b"\0\0\0\0"
    payload = bytes(b ^ mask[i % 4] for i, b in enumerate(exact(length)))
    return b1 & 0x0F, payload


class WSServer(RawServer):
    """WebSocket server: after the handshake ``script(conn, server)`` talks to the client."""

    def __init__(self, script, accept_override: str | None = None, status: int = 101):
        """Start the server.

        Args:
            script: ``script(conn, server)`` run after the handshake.
            accept_override: Send this ``Sec-WebSocket-Accept`` (to test a bad handshake).
            status: Status code of the handshake answer.
        """
        self.script = script
        self.received: list[tuple[int, bytes]] = []
        self.accept_override = accept_override
        self.status = status
        super().__init__(self._handle)

    def url(self, path: str = "/", scheme: str = "ws") -> str:
        """URL of this server (``ws://``)."""
        return super().url(path, scheme)

    def _handle(self, conn, request: bytes) -> None:
        key = ""
        for line in request.decode("latin-1").split("\r\n"):
            if line.lower().startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
        accept = self.accept_override or base64.b64encode(
            hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        reason = "Switching Protocols" if self.status == 101 else "Forbidden"
        conn.sendall(f"HTTP/1.1 {self.status} {reason}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode())
        if self.status == 101:
            conn.settimeout(5)
            self.script(conn, self)

    def record(self, conn, timeout: float = 2.0) -> tuple[int, bytes] | None:
        """Read one client frame (recorded), or ``None`` on time-out."""
        conn.settimeout(timeout)
        try:
            frame = read_client_frame(conn)
        except (socket.timeout, ConnectionError, OSError):
            return None
        self.received.append(frame)
        return frame


def wait_for(predicate, timeout: float = 3.0) -> bool:
    """Poll ``predicate`` until it is true or the time is up."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()
