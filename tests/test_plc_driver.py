"""Tests for the PLC connection manager, read planning and simulator.

The last test class talks to a real S7 protocol server (``snap7.server``)
on localhost, exercising the genuine snap7 client code path.
"""

import socket
import struct
from types import SimpleNamespace

import pytest

from app.plc.addressing import parse_address
from app.plc.driver import PLCConnectionManager, PLCError, plan_reads


def fake_plc(plc_id=1, ip="10.0.0.1", port=102, enabled=True):
    """Build a minimal PLC-like object for the manager.

    Args:
        plc_id: Id.
        ip: IP address.
        port: TCP port.
        enabled: Enabled flag.

    Returns:
        A ``SimpleNamespace`` with the attributes the manager uses.
    """
    return SimpleNamespace(id=plc_id, name=f"P{plc_id}", ip_address=ip, rack=0, slot=1, tcp_port=port,
                           enabled=enabled, connection_signature=(ip, 0, 1, port))


def addrs(*texts):
    """Parse several addresses into a ``{text: S7Address}`` dict.

    Args:
        *texts: Address strings.

    Returns:
        Mapping text -> S7Address.
    """
    return {t: parse_address(t) for t in texts}


class TestPlanReads:
    """Batching of tag reads into contiguous blocks."""

    def test_adjacent_tags_merge(self):
        """Neighbouring tags in one DB become a single block."""
        blocks = plan_reads(addrs("DB1.DBD0:REAL", "DB1.DBW4:INT", "DB1.DBX6.0"))
        assert len(blocks) == 1
        assert (blocks[0].start, blocks[0].size) == (0, 7)

    def test_different_areas_split(self):
        """Different DBs/areas are separate blocks."""
        blocks = plan_reads(addrs("DB1.DBW0", "DB2.DBW0", "MW0", "IW0"))
        assert len(blocks) == 4

    def test_gap_and_span_limits(self):
        """Large gaps or spans start a new block."""
        assert len(plan_reads(addrs("DB1.DBW0", "DB1.DBW100"), max_gap=32)) == 2
        assert len(plan_reads(addrs("DB1.DBW0", "DB1.DBW20"), max_gap=32, max_span=10)) == 2

    def test_overlapping_tags(self):
        """A bit inside an already covered byte does not grow the block."""
        blocks = plan_reads(addrs("DB1.DBD0:REAL", "DB1.DBX1.3"))
        assert len(blocks) == 1 and blocks[0].size == 4

    def test_timers_individual(self):
        """Timers/counters are never merged."""
        assert len(plan_reads(addrs("T1", "T2", "C1"))) == 3


class TestSimulatedManager:
    """Manager behaviour using the in-memory simulator."""

    @pytest.fixture()
    def manager(self):
        """A simulation-mode manager with no reconnect delay.

        Returns:
            :class:`PLCConnectionManager`.
        """
        from app.plc.driver import SimulatedConnection

        SimulatedConnection.reset()
        return PLCConnectionManager(simulation=True, reconnect_delay=0)

    def test_write_then_read(self, manager):
        """Written values are read back."""
        plc = fake_plc()
        a = parse_address("DB1.DBD4:REAL")
        manager.write(plc, a, 12.5)
        assert manager.read_one(plc, a).value == 12.5

    def test_bool_write_preserves_neighbour_bits(self, manager):
        """Writing one bit keeps the other bits of the byte."""
        plc = fake_plc()
        manager.write(plc, parse_address("MB0"), 0b10000001)
        manager.write(plc, parse_address("M0.2"), True)
        assert manager.read_one(plc, parse_address("MB0")).value == 0b10000101
        manager.write(plc, parse_address("M0.7"), False)
        assert manager.read_one(plc, parse_address("MB0")).value == 0b00000101

    def test_offline_plc(self, manager):
        """An unreachable PLC returns errors for every tag, not exceptions."""
        plc = fake_plc(ip="10.0.0.254")
        results = manager.read(plc, addrs("DB1.DBW0", "MW0"))
        assert all(not r.ok for r in results.values())
        assert manager.status(plc)["connected"] is False
        assert "offline" in manager.status(plc)["error"]

    def test_reconnect_backoff(self):
        """During the back-off period no new connection attempt is made."""
        mgr = PLCConnectionManager(simulation=True, reconnect_delay=60)
        plc = fake_plc(ip="10.0.0.254")
        mgr.read_one(plc, parse_address("MW0"))
        plc.ip_address = "10.0.0.5"  # now "reachable" but signature unchanged
        assert not mgr.read_one(plc, parse_address("MW0")).ok

    def test_disabled_plc(self, manager):
        """Disabled PLCs are not contacted."""
        res = manager.read_one(fake_plc(enabled=False), parse_address("MW0"))
        assert "disabled" in res.error

    def test_write_timer_rejected(self, manager):
        """Timers cannot be written."""
        with pytest.raises(PLCError):
            manager.write(fake_plc(), parse_address("T1"), 5)

    def test_signature_change_reconnects(self, manager):
        """Changing the IP closes the old connection."""
        plc = fake_plc()
        manager.read_one(plc, parse_address("MW0"))
        old = manager._entries[plc.id].conn
        plc.connection_signature = ("10.0.0.2", 0, 1, 102)
        plc.ip_address = "10.0.0.2"
        manager.read_one(plc, parse_address("MW0"))
        assert manager._entries[plc.id].conn is not old

    def test_test_connection(self, manager):
        """test_connection reports CPU info or an error."""
        assert manager.test_connection(fake_plc())["ok"] is True
        assert manager.test_connection(fake_plc(ip="1.2.3.254"))["ok"] is False


def _free_port() -> int:
    """Find a free local TCP port.

    Returns:
        A port number.
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestRealSnap7Server:
    """End-to-end tests against ``snap7.server.Server`` on localhost."""

    @pytest.fixture()
    def server(self):
        """Start an S7 server with DB1, M, I and Q areas registered.

        Yields:
            Tuple ``(port, memory dict)``.
        """
        snap7_server = pytest.importorskip("snap7.server")
        from snap7.type import SrvArea

        mem = {"db1": bytearray(64), "mk": bytearray(32), "pe": bytearray(8), "pa": bytearray(8)}
        mem["db1"][0:4] = struct.pack(">f", 21.5)
        mem["db1"][4:6] = struct.pack(">h", -42)
        mem["db1"][6] = 0b00000100
        srv = snap7_server.Server()
        srv.register_area(SrvArea.DB, 1, mem["db1"])
        srv.register_area(SrvArea.MK, 0, mem["mk"])
        srv.register_area(SrvArea.PE, 0, mem["pe"])
        srv.register_area(SrvArea.PA, 0, mem["pa"])
        port = _free_port()
        try:
            srv.start(tcp_port=port)
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"snap7 server unavailable: {exc}")
        yield port, mem
        srv.stop()

    def test_batched_read(self, server):
        """Several tags are decoded correctly from one batched read."""
        port, _ = server
        mgr = PLCConnectionManager(simulation=False, reconnect_delay=0)
        plc = fake_plc(ip="127.0.0.1", port=port)
        res = mgr.read(plc, addrs("DB1.DBD0:REAL", "DB1.DBW4:INT", "DB1.DBX6.2", "DB1.DBX6.3"))
        assert res["DB1.DBD0:REAL"].value == 21.5
        assert res["DB1.DBW4:INT"].value == -42
        assert res["DB1.DBX6.2"].value is True
        assert res["DB1.DBX6.3"].value is False
        assert mgr.status(plc)["connected"]
        mgr.disconnect_all()

    def test_write_reaches_plc_memory(self, server):
        """Writes change the server's memory."""
        port, mem = server
        mgr = PLCConnectionManager(simulation=False, reconnect_delay=0)
        plc = fake_plc(ip="127.0.0.1", port=port)
        mgr.write(plc, parse_address("MW2:INT"), 1234)
        mgr.write(plc, parse_address("Q0.3"), True)
        assert struct.unpack(">h", mem["mk"][2:4])[0] == 1234
        assert mem["pa"][0] == 0b1000
        mgr.disconnect_all()

    def test_bad_db_isolated(self, server):
        """A missing DB fails only its own tag."""
        port, _ = server
        mgr = PLCConnectionManager(simulation=False, reconnect_delay=0)
        plc = fake_plc(ip="127.0.0.1", port=port)
        res = mgr.read(plc, addrs("DB99.DBW0", "DB1.DBW4:INT"))
        assert not res["DB99.DBW0"].ok
        assert res["DB1.DBW4:INT"].value == -42
        mgr.disconnect_all()

    def test_unreachable_port(self):
        """A closed port yields a clean error."""
        mgr = PLCConnectionManager(simulation=False, reconnect_delay=0)
        res = mgr.read_one(fake_plc(ip="127.0.0.1", port=_free_port()), parse_address("MW0"))
        assert not res.ok
