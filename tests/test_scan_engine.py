"""Tests for scanning backends (demo determinism, replay cursor) and engine."""

from __future__ import annotations

from ble_radar.engine import RadarEngine
from ble_radar.scan import DemoScanner, Observation, ReplayScanner, ScannerError


def _obs(addr, t, rssi=-50):
    return Observation(address=addr, name=None, rssi=rssi, seen_at=t)


class TestDemoScanner:
    def test_deterministic_with_seed(self, sink, clock):
        a = DemoScanner(sink, clock=clock, seed=7)
        b = DemoScanner(sink, clock=clock, seed=7)
        a.start()
        b.start()
        for t in range(10):
            a._tick(t)
            b._tick(t)
        assert [o.as_dict() for o in a._on_observation.collected] == [
            o.as_dict() for o in b._on_observation.collected
        ]

    def test_different_seeds_differ(self, sink, clock):
        a = DemoScanner(sink, clock=clock, seed=1)
        b = DemoScanner(sink, clock=clock, seed=2)
        a.start()
        b.start()
        for t in range(5):
            a._tick(t)
            b._tick(t)
        assert len(a._on_observation.collected) == len(b._on_observation.collected)
        assert a._on_observation.collected[0] is not None

    def test_emits_all_demo_devices(self, sink, clock):
        s = DemoScanner(sink, clock=clock, seed=3)
        s.start()
        s._tick(10)
        assert len(sink.collected) == 8

    def test_rssi_clamped(self, sink, clock):
        s = DemoScanner(sink, clock=clock, seed=0)
        s.start()
        for t in range(100):
            s._tick(t)
        for o in sink.collected:
            assert -95 <= o.rssi <= -25

    def test_devices_disappear_cyclically(self, sink, clock):
        s = DemoScanner(sink, clock=clock, seed=5)
        s.start()
        tag = DemoScanner._device_address("Chip-Tag")
        present = set()
        for t in range(0, 200):
            s._tick(t)
            if any(o.address == tag for o in sink.collected if o.seen_at == t):
                present.add(t)
        absent = set(range(0, 200)) - present
        assert absent  # Chip-Tag life=60, gone 20s per cycle
        assert 40 < len(absent) < 80


class TestReplayScanner:
    def test_ordered_next_up_to(self, sink):
        obs = [_obs("aa:bb:cc:dd:ee:01", 100), _obs("aa:bb:cc:dd:ee:02", 200)]
        r = ReplayScanner(obs, sink)
        r.start()
        assert r.next_up_to(150) == [obs[0]]
        assert r.next_up_to(500) == [obs[1]]
        assert r.next_up_to(500) == []

    def test_input_sorted(self, sink):
        obs = [_obs("aa:bb:cc:dd:ee:01", 200), _obs("aa:bb:cc:dd:ee:02", 100)]
        r = ReplayScanner(obs, sink)
        r.start()
        out = r.next_up_to(500)
        assert [o.seen_at for o in out] == [100, 200]

    def test_seek(self, sink):
        obs = [_obs("aa:bb:cc:dd:ee:01", 100), _obs("aa:bb:cc:dd:ee:02", 200)]
        r = ReplayScanner(obs, sink)
        r.start()
        r.seek(150)
        assert r.next_up_to(500) == [obs[1]]

    def test_not_running_yields_nothing(self, sink):
        r = ReplayScanner([_obs("aa:bb:cc:dd:ee:01", 100)], sink)
        assert r.next_up_to(500) == []


class TestEngine:
    def test_ingest_tracks_devices(self, clock):
        e = RadarEngine()
        e.ingest(_obs("aa:bb:cc:dd:ee:01", 1000, -60))
        assert "aa:bb:cc:dd:ee:01" in e.devices

    def test_snapshot_only_visible(self, clock):
        e = RadarEngine()
        e.ingest(_obs("aa:bb:cc:dd:ee:01", 1000, -60))
        snaps = e.snapshot(1001)
        assert [s.address for s in snaps] == ["aa:bb:cc:dd:ee:01"]
        snaps = e.snapshot(1000 + 31)
        assert snaps == []

    def test_snapshot_sorted_by_band(self, clock):
        e = RadarEngine()
        e.ingest(_obs("aa:bb:cc:dd:ee:01", 1000, -90))  # weak (ring 5)
        e.ingest(_obs("aa:bb:cc:dd:ee:02", 1000, -35))  # strong (ring 1)
        snaps = e.snapshot(1001)
        assert snaps[0].band == "strong"
        assert snaps[1].band == "weak"

    def test_label_uses_name_or_pseudonym(self, clock):
        e = RadarEngine()
        e.ingest(_obs("aa:bb:cc:dd:ee:01", 1000, -50))
        e.ingest(Observation("aa:bb:cc:dd:ee:01", "MyTag", -48, 1001))
        e.ingest(_obs("aa:bb:cc:dd:ee:02", 1000, -50))
        snaps = e.snapshot(1002)
        by_addr = {s.address: s.label for s in snaps}
        assert by_addr["aa:bb:cc:dd:ee:01"] == "MyTag"
        assert by_addr["aa:bb:cc:dd:ee:02"].startswith("D-")

    def test_ingest_returns_address(self, clock):
        e = RadarEngine()
        assert e.ingest(_obs("aa:bb:cc:dd:ee:01", 1000)) == "aa:bb:cc:dd:ee:01"

    def test_snapshot_advances_state(self, clock):
        e = RadarEngine()
        e.ingest(_obs("aa:bb:cc:dd:ee:01", 1000, -50))
        e.snapshot(1000 + 16)  # just past ACTIVE_WINDOW
        assert e.devices["aa:bb:cc:dd:ee:01"].state == "fading"
