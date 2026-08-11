"""Tests for replay (timeline cursor, seeking, speed) and export."""

from __future__ import annotations

import csv
import io
import json

import pytest

from ble_radar import db as dbmod
from ble_radar import export as exportmod
from ble_radar.replay import ReplaySession
from ble_radar.scan import Observation
from ble_radar.util import hash_address


def _obs(addr, t, rssi=-50, name=None):
    return Observation(address=addr, name=name, rssi=rssi, seen_at=t)


def _session_obs(clock):
    return [
        _obs("aa:bb:cc:dd:ee:01", 1000, -40, "Beacon"),
        _obs("aa:bb:cc:dd:ee:02", 1005, -60),
        _obs("aa:bb:cc:dd:ee:01", 1010, -42, "Beacon"),
        _obs("aa:bb:cc:dd:ee:03", 1020, -80, "Far"),
    ]


class TestReplaySession:
    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            ReplaySession([])

    def test_advance_by_speed(self):
        s = ReplaySession(_session_obs(None), speed=2.0)
        out = s.advance(5.0)  # replay clock +10s -> t=1010
        assert [o.seen_at for o in out] == [1000, 1005, 1010]

    def test_advance_realtime(self):
        s = ReplaySession(_session_obs(None), speed=1.0)
        out = s.advance(5.0)
        assert [o.seen_at for o in out] == [1000, 1005]

    def test_completes_at_end(self):
        s = ReplaySession(_session_obs(None))
        out = []
        while not s.done:
            out.extend(s.advance(2.0))
        assert [o.seen_at for o in out] == [1000, 1005, 1010, 1020]
        assert s.done
        assert s.advance(1.0) == []

    def test_progress_span(self):
        s = ReplaySession(_session_obs(None))
        assert s.progress == pytest.approx(0.0)
        s.advance(10.0)
        assert 0.0 < s.progress < 1.0
        while not s.done:
            s.advance(1.0)
        assert s.progress == pytest.approx(1.0)

    def test_seek(self):
        s = ReplaySession(_session_obs(None))
        s.seek(1012)
        assert [o.seen_at for o in s.advance(100.0)] == [1020]

    def test_seek_clamped_to_end(self):
        s = ReplaySession(_session_obs(None))
        s.seek(99999)
        assert s.done

    def test_from_time(self):
        s = ReplaySession(_session_obs(None), from_time=1010)
        assert s.replay_t == 1010

    def test_speed_change(self):
        s = ReplaySession(_session_obs(None), speed=1.0)
        out = s.advance(5.0, speed=4.0)
        assert [o.seen_at for o in out] == [1000, 1005, 1010, 1020]
        assert s.speed == 4.0


class TestExport:
    def _rows(self, conn):
        sid = dbmod.start_session(conn, "x")
        dbmod.record_observation(conn, sid, _obs("aa:bb:cc:dd:ee:01", 1000, -40, "Beacon"))
        dbmod.record_observation(conn, sid, _obs("aa:bb:cc:dd:ee:02", 1001, None))
        return dbmod.observations(conn)

    def test_to_json_structure(self, conn):
        data = json.loads(exportmod.to_json(self._rows(conn)))
        assert len(data) == 2
        rec = next(r for r in data if r["address"] == "aa:bb:cc:dd:ee:01")
        assert set(rec) == {"observed_at", "address", "name", "rssi", "session_id"}
        assert rec["name"] == "Beacon"
        assert rec["rssi"] == -40

    def test_to_json_sanitize(self, conn):
        rows = self._rows(conn)
        clean = json.loads(exportmod.to_json(rows, sanitize=True))
        rec = next(r for r in clean if r["address"] == hash_address("aa:bb:cc:dd:ee:01"))
        assert rec is not None
        assert rec["address"] != "aa:bb:cc:dd:ee:01"

    def test_to_json_none_rssi(self, conn):
        data = json.loads(exportmod.to_json(self._rows(conn)))
        assert any(r["rssi"] is None for r in data)

    def test_to_csv_header_and_rows(self, conn):
        text = exportmod.to_csv(self._rows(conn))
        rows = list(csv.DictReader(io.StringIO(text)))
        assert len(rows) == 2
        rec = next(r for r in rows if r["address"] == "aa:bb:cc:dd:ee:01")
        assert rec["name"] == "Beacon"

    def test_to_csv_sanitize(self, conn):
        rows = self._rows(conn)
        clean = list(csv.DictReader(io.StringIO(exportmod.to_csv(rows, sanitize=True))))
        rec = next(r for r in clean if r["address"] == hash_address("aa:bb:cc:dd:ee:01"))
        assert rec is not None

    def test_empty(self, conn):
        assert exportmod.to_csv([]).startswith("observed_at,")
        assert json.loads(exportmod.to_json([])) == []
