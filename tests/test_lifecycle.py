"""Tests for the lifecycle state machine and RSSI bands."""

from __future__ import annotations

import pytest

from ble_radar.lifecycle import (
    ACTIVE,
    ACTIVE_WINDOW,
    APPEARED,
    BAND_FAR,
    BAND_MID,
    BAND_NEAR,
    BAND_STRONG,
    BAND_UNKNOWN,
    BAND_WEAK,
    BAND_RING,
    DISAPPEARED,
    FADING,
    GONE_WINDOW,
    DeviceState,
    rssi_band,
)


@pytest.mark.parametrize(
    ("rssi", "band"),
    [
        (-40, BAND_STRONG),
        (-39, BAND_STRONG),
        (-41, BAND_NEAR),
        (-55, BAND_NEAR),
        (-56, BAND_MID),
        (-70, BAND_MID),
        (-71, BAND_FAR),
        (-85, BAND_FAR),
        (-86, BAND_WEAK),
        (-120, BAND_WEAK),
        (None, BAND_UNKNOWN),
    ],
)
def test_rssi_band_edges(rssi, band):
    assert rssi_band(rssi) == band


def test_band_rings_are_valid_1_to_5():
    for band, ring in BAND_RING.items():
        assert 1 <= ring <= 5


class TestDeviceState:
    def test_first_observe_is_appeared(self):
        d = DeviceState(address="aa:bb:cc:dd:ee:01")
        d.observe(-50, 1000)
        assert d.state == APPEARED
        assert d.appeared_at == 1000
        assert d.times_seen == 1

    def test_second_observe_is_active(self):
        d = DeviceState(address="aa:bb:cc:dd:ee:01")
        d.observe(-50, 1000)
        d.observe(-52, 1005)
        assert d.state == ACTIVE

    def test_rssi_ema(self):
        d = DeviceState(address="aa:bb:cc:dd:ee:01")
        d.observe(-50, 1000)
        d.observe(-60, 1005)
        assert d.rssi == pytest.approx(0.6 * -50 + 0.4 * -60)
        assert d.raw_rssi == -60

    def test_name_updates_only_when_present(self):
        d = DeviceState(address="aa:bb:cc:dd:ee:01")
        d.observe(-50, 1000, name="Beacon")
        d.observe(-50, 1005, name=None)
        assert d.name == "Beacon"

    def test_fading_after_active_window(self):
        d = DeviceState(address="aa:bb:cc:dd:ee:01")
        d.observe(-50, 1000)
        d.observe(-50, 1005)
        assert d.advance(1005 + ACTIVE_WINDOW + 1) == FADING

    def test_still_active_within_window(self):
        d = DeviceState(address="aa:bb:cc:dd:ee:01")
        d.observe(-50, 1000)
        d.observe(-50, 1005)
        assert d.advance(1005 + ACTIVE_WINDOW - 1) == ACTIVE

    def test_disappeared_after_gone_window(self):
        d = DeviceState(address="aa:bb:cc:dd:ee:01")
        d.observe(-50, 1000)
        d.observe(-50, 1005)
        assert d.advance(1005 + GONE_WINDOW + 1) == DISAPPEARED

    def test_reappearance_restarts(self):
        d = DeviceState(address="aa:bb:cc:dd:ee:01")
        d.observe(-50, 1000)
        d.observe(-50, 1005)
        d.advance(1005 + GONE_WINDOW + 1)
        assert d.state == DISAPPEARED
        d.observe(-45, 2000)
        assert d.state in (ACTIVE, APPEARED)
        assert d.visible(2000)

    def test_visibility_window(self):
        d = DeviceState(address="aa:bb:cc:dd:ee:01")
        d.observe(-50, 1000)
        assert d.visible(1000)
        assert not d.visible(1000 + GONE_WINDOW + 1)

    def test_never_seen_not_visible(self):
        d = DeviceState(address="aa:bb:cc:dd:ee:01")
        assert not d.visible(1000)

    def test_trail_kept_short(self):
        d = DeviceState(address="aa:bb:cc:dd:ee:01")
        for i in range(20):
            d.observe(-50, 1000 + i)
        assert len(d.trails) <= 8

    def test_angle_stable(self):
        d1 = DeviceState(address="aa:bb:cc:dd:ee:01")
        d2 = DeviceState(address="aa:bb:cc:dd:ee:01")
        assert d1.angle == d2.angle
