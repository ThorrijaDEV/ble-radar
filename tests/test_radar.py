"""Golden-frame tests for pure radar rendering (deterministic)."""

from __future__ import annotations

from ble_radar.radar import (
    Frame,
    ring_radius,
    render_frame,
    sweep_advance,
)
from ble_radar.lifecycle import ACTIVE, APPEARED


def _snap(address="aa:bb:cc:dd:ee:01", band="mid", angle=45.0, state=ACTIVE, label="Tag", trails=None):
    return type(
        "Snap",
        (),
        dict(
            address=address,
            label=label,
            angle=angle,
            band=band,
            state=state,
            trails=trails or [],
            jitter=0.0,
        ),
    )()


class TestRingRadius:
    def test_bands_ordered_inside_out(self):
        radii = {band: ring_radius(band, 10) for band in ("strong", "near", "mid", "far", "weak")}
        assert radii["strong"] < radii["near"] < radii["mid"] < radii["far"] <= radii["weak"]

    def test_within_max_radius(self):
        for band in ("strong", "near", "mid", "far", "weak", "unknown"):
            assert ring_radius(band, 10) <= 10


class TestSweep:
    def test_wraps(self):
        assert sweep_advance(350.0, 20.0) == 10.0

    def test_steps(self):
        assert sweep_advance(10.0, 30.0) == 40.0


class TestRenderFrame:
    def test_returns_frame(self):
        f = render_frame([], 0.0, 80, 24)
        assert isinstance(f, Frame)

    def test_cells_within_bounds(self):
        f = render_frame([_snap()], 30.0, 80, 24)
        for cell in f.cells:
            assert 0 <= cell.row < 24
            assert 0 <= cell.col < 80

    def test_deterministic(self):
        devs = [_snap(address="aa:bb:cc:dd:ee:01"), _snap(address="bb:cc:dd:ee:ff:01", band="strong", angle=200.0)]
        a = render_frame(devs, 77.0, 80, 24)
        b = render_frame(devs, 77.0, 80, 24)
        assert [(c.row, c.col, c.char, c.kind) for c in a.cells] == [
            (c.row, c.col, c.char, c.kind) for c in b.cells
        ]

    def test_marker_present(self):
        f = render_frame([_snap(label="Tag")], 0.0, 80, 24)
        markers = [c for c in f.cells if c.kind == "marker"]
        assert markers
        assert all(c.device == "aa:bb:cc:dd:ee:01" for c in markers)

    def test_disappeared_devices_not_drawn(self):
        d = _snap(address="aa:bb:cc:dd:ee:01", state="disappeared")
        f = render_frame([d], 0.0, 80, 24)
        assert not any(c.kind == "marker" for c in f.cells)

    def test_trails_drawn(self):
        d = _snap(trails=[(30.0, "mid"), (60.0, "mid")])
        f = render_frame([d], 0.0, 80, 24)
        assert any(c.kind == "trail" for c in f.cells)

    def test_appeared_marker_still_drawn(self):
        f = render_frame([_snap(state=APPEARED)], 0.0, 80, 24)
        assert any(c.kind == "marker" for c in f.cells)

    def test_legend_explains_honesty(self):
        f = render_frame([], 0.0, 80, 24)
        assert any("NOT distance" in line for line in f.legend)

    def test_tiny_terminal_does_not_crash(self):
        f = render_frame([_snap()], 0.0, 12, 5)
        assert f.width == 12

    def test_sweep_moves_cells(self):
        a = render_frame([], 0.0, 80, 24)
        b = render_frame([], 45.0, 80, 24)
        sweep_a = {(c.row, c.col) for c in a.cells if c.kind == "sweep"}
        sweep_b = {(c.row, c.col) for c in b.cells if c.kind == "sweep"}
        assert sweep_a != sweep_b
