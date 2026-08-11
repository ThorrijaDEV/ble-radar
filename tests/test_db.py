"""Tests for the SQLite layer: sessions, observations, devices, stats."""

from __future__ import annotations

import pytest

from ble_radar import db as dbmod
from ble_radar.scan import Observation
from ble_radar.util import now_epoch


def _obs(address="aa:bb:cc:dd:ee:01", rssi=-50, t=None, name=None):
    return Observation(
        address=address, name=name, rssi=rssi, seen_at=t if t is not None else now_epoch()
    )


class TestConnect:
    def test_creates_schema(self, tmp_path):
        c = dbmod.connect(tmp_path / "a" / "b" / "radar.db")
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"meta", "sessions", "devices", "observations"} <= tables
        c.close()

    def test_schema_version(self, conn):
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        assert row["value"] == dbmod.SCHEMA_VERSION

    def test_default_path_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BLE_RADAR_DB", str(tmp_path / "custom.db"))
        assert dbmod.default_db_path() == tmp_path / "custom.db"

    def test_default_path_xdg(self, monkeypatch, tmp_path):
        monkeypatch.delenv("BLE_RADAR_DB", raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert dbmod.default_db_path() == tmp_path / "ble-radar" / "radar.db"


class TestSessions:
    def test_start_end(self, conn):
        sid = dbmod.start_session(conn, "my scan", "demo")
        assert isinstance(sid, int)
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        assert row["label"] == "my scan"
        assert row["backend"] == "demo"
        assert row["ended_at"] is None
        dbmod.end_session(conn, sid, 10)
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        assert row["ended_at"] is not None
        assert row["observations"] == 10

    def test_list_sessions_orders_desc(self, conn):
        s1 = dbmod.start_session(conn, "one")
        s2 = dbmod.start_session(conn, "two")
        ids = [r["id"] for r in dbmod.list_sessions(conn)]
        assert ids == [s2, s1]

    def test_delete_session(self, conn):
        s1 = dbmod.start_session(conn, "keep")
        s2 = dbmod.start_session(conn, "aborted")
        dbmod.delete_session(conn, s2)
        ids = [r["id"] for r in dbmod.list_sessions(conn)]
        assert ids == [s1]
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1

    def test_session_device_count(self, conn):
        sid = dbmod.start_session(conn, "x")
        dbmod.record_observation(conn, sid, _obs("aa:bb:cc:dd:ee:01"))
        dbmod.record_observation(conn, sid, _obs("aa:bb:cc:dd:ee:02"))
        row = dbmod.list_sessions(conn)[0]
        assert row["devices"] == 2


class TestRecordObservation:
    def test_records_row(self, conn):
        sid = dbmod.start_session(conn)
        dbmod.record_observation(conn, sid, _obs(rssi=-55, t=1000, name="Beacon"))
        rows = conn.execute("SELECT * FROM observations").fetchall()
        assert len(rows) == 1
        assert rows[0]["rssi"] == -55
        assert rows[0]["observed_at"] == 1000
        assert rows[0]["session_id"] == sid

    def test_device_upsert(self, conn):
        sid = dbmod.start_session(conn)
        dbmod.record_observation(conn, sid, _obs(rssi=-50, t=1000))
        dbmod.record_observation(conn, sid, _obs(rssi=-40, t=2000))
        rows = conn.execute("SELECT * FROM devices").fetchall()
        assert len(rows) == 1
        d = rows[0]
        assert d["times_seen"] == 2
        assert d["last_seen"] == 2000
        assert d["best_rssi"] == -50  # MIN (closest)
        assert d["last_rssi"] == -40

    def test_normalizes_address_in_devices(self, conn):
        sid = dbmod.start_session(conn)
        dbmod.record_observation(conn, sid, _obs(address="AA:BB:CC:DD:EE:01"))
        d = conn.execute("SELECT * FROM devices").fetchone()
        assert d["address"] == "aa:bb:cc:dd:ee:01"

    def test_invalid_address_rejected(self, conn):
        sid = dbmod.start_session(conn)
        with pytest.raises(ValueError):
            dbmod.record_observation(conn, sid, _obs(address="not-an-address"))


class TestQueries:
    def _seed(self, conn, sid):
        for addr, rssi, t in [
            ("aa:bb:cc:dd:ee:01", -50, 1000),
            ("aa:bb:cc:dd:ee:02", -70, 2000),
            ("aa:bb:cc:dd:ee:01", -55, 3000),
        ]:
            dbmod.record_observation(conn, sid, _obs(addr, rssi, t))

    def test_observations_filters(self, conn):
        sid = dbmod.start_session(conn)
        self._seed(conn, sid)
        all_rows = dbmod.observations(conn)
        assert len(all_rows) == 3
        by_addr = dbmod.observations(conn, address="aa:bb:cc:dd:ee:01")
        assert len(by_addr) == 2
        since = dbmod.observations(conn, since=2500)
        assert [r["observed_at"] for r in since] == [3000]
        by_session = dbmod.observations(conn, session_id=sid)
        assert len(by_session) == 3

    def test_observations_order(self, conn):
        sid = dbmod.start_session(conn)
        self._seed(conn, sid)
        desc = dbmod.observations(conn, order="ASC")
        assert [r["observed_at"] for r in desc] == [1000, 2000, 3000]

    def test_session_observations_ascending(self, conn):
        sid = dbmod.start_session(conn)
        self._seed(conn, sid)
        rows = dbmod.session_observations(conn, sid)
        assert [r["observed_at"] for r in rows] == [1000, 2000, 3000]

    def test_device_history(self, conn):
        sid = dbmod.start_session(conn)
        self._seed(conn, sid)
        rows = dbmod.device_history(conn, "aa:bb:cc:dd:ee:01")
        assert len(rows) == 2

    def test_device_census(self, conn):
        sid = dbmod.start_session(conn)
        self._seed(conn, sid)
        census = dbmod.device_census(conn)
        assert len(census) == 2
        assert census[0]["address"] == "aa:bb:cc:dd:ee:01"

    def test_stats(self, conn):
        sid = dbmod.start_session(conn)
        self._seed(conn, sid)
        st = dbmod.stats(conn)
        assert st["sessions"] == 1
        assert st["devices"] == 2
        assert st["observations"] == 3
        assert st["first"] == 1000
        assert st["last"] == 3000

    def test_stats_empty(self, conn):
        st = dbmod.stats(conn)
        assert st["sessions"] == 0
        assert st["first"] is None
