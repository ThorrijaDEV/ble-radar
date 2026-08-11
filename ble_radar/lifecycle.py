"""Device lifecycle state machine.

States: appeared -> active -> fading -> disappeared (reappearance restarts).

Pure functions of observation timestamps — no I/O, fully testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ble_radar.util import stable_angle

ACTIVE_WINDOW = 15   # seconds without advertisements before fading
GONE_WINDOW = 30     # seconds without advertisements before disappearing

APPEARED = "appeared"
ACTIVE = "active"
FADING = "fading"
DISAPPEARED = "disappeared"

STATE_ORDER = (APPEARED, ACTIVE, FADING, DISAPPEARED)


@dataclass
class DeviceState:
    """In-memory state of one observed device."""

    address: str
    name: str | None = None
    rssi: float | None = None           # EMA-smoothed
    raw_rssi: int | None = None         # last measured value
    appeared_at: int | None = None
    last_seen: int | None = None
    times_seen: int = 0
    state: str = APPEARED
    trails: list[tuple[int, float, int]] = field(default_factory=list)  # (seen_at, angle, band)

    @property
    def angle(self) -> float:
        return stable_angle(self.address)

    def observe(self, rssi: int | None, seen_at: int, name: str | None = None) -> None:
        """Apply one observation; returns nothing (mutates in place)."""
        if self.appeared_at is None:
            self.appeared_at = seen_at
        if self.last_seen is not None:
            self.state = ACTIVE
        else:
            self.state = APPEARED
        self.last_seen = seen_at
        self.times_seen += 1
        if name:
            self.name = name
        self.raw_rssi = rssi
        if rssi is not None:
            self.rssi = (0.6 * self.rssi + 0.4 * rssi) if self.rssi is not None else float(rssi)
        band = rssi_band(rssi)
        self.trails.append((seen_at, self.angle, band))
        self.trails = self.trails[-8:]  # keep a short fading trail

    def advance(self, now: int) -> str:
        """Move state forward based on elapsed time since last observation."""
        if self.last_seen is None:
            return self.state
        since = now - self.last_seen
        if since > GONE_WINDOW:
            self.state = DISAPPEARED
        elif since > ACTIVE_WINDOW:
            if self.state != DISAPPEARED:
                self.state = FADING
        elif self.state == DISAPPEARED:
            self.state = APPEARED  # only reachable if a stale observe() missed
        return self.state

    def visible(self, now: int) -> bool:
        return self.state != DISAPPEARED and self.last_seen is not None and (now - self.last_seen) <= GONE_WINDOW


BAND_STRONG = "strong"
BAND_NEAR = "near"
BAND_MID = "mid"
BAND_FAR = "far"
BAND_WEAK = "weak"
BAND_UNKNOWN = "unknown"

BAND_RING = {BAND_STRONG: 1, BAND_NEAR: 2, BAND_MID: 3, BAND_FAR: 4, BAND_WEAK: 5, BAND_UNKNOWN: 5}


def rssi_band(rssi: int | None) -> str:
    """Map a measured RSSI to a coarse strength band.

    These are strength buckets, NOT distance. See DESIGN.md §6.1.
    """
    if rssi is None:
        return BAND_UNKNOWN
    if rssi >= -40:
        return BAND_STRONG
    if rssi >= -55:
        return BAND_NEAR
    if rssi >= -70:
        return BAND_MID
    if rssi >= -85:
        return BAND_FAR
    return BAND_WEAK
