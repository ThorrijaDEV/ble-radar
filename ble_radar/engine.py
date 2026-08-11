"""Radar engine: observation ingestion, lifecycle, snapshots.

Owns per-device state and produces the snapshot consumed by the renderer.
Backend-agnostic: radar, record, demo and replay all feed it Observations.
"""

from __future__ import annotations

from ble_radar.lifecycle import BAND_RING, DeviceState, rssi_band
from ble_radar.radar import DeviceSnap
from ble_radar.scan import Observation
from ble_radar.util import pseudonym


class RadarEngine:
    def __init__(self, clock=None):
        self.devices: dict[str, DeviceState] = {}
        self._clock = clock

    def ingest(self, obs: Observation) -> str:
        """Apply an observation; returns the device address."""
        state = self.devices.get(obs.address)
        if state is None:
            state = DeviceState(address=obs.address, name=obs.name)
            self.devices[obs.address] = state
        state.observe(obs.rssi, obs.seen_at, obs.name)
        return obs.address

    def advance(self, now: int) -> None:
        for state in self.devices.values():
            state.advance(now)

    def label_for(self, address: str) -> str:
        state = self.devices.get(address)
        if state and state.name:
            return state.name
        return pseudonym(address)

    def snapshot(self, now: int) -> list[DeviceSnap]:
        """Visible devices as renderer input, strongest first."""
        self.advance(now)
        snaps = []
        for state in self.devices.values():
            if not state.visible(now):
                continue
            snaps.append(
                DeviceSnap(
                    address=state.address,
                    label=self.label_for(state.address),
                    angle=state.angle,
                    band=rssi_band(state.raw_rssi),
                    state=state.state,
                    trails=[(angle, band) for _t, angle, band in state.trails[:-1]],
                    jitter=(state.angle / 360.0) - 0.5,
                )
            )
        snaps.sort(key=lambda s: BAND_RING.get(s.band, 9))
        return snaps
