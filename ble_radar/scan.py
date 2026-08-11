"""Scanning backends: bleak (real hardware), demo (synthetic), replay (file).

Every backend produces the same Observation objects; the radar, recorder and
replay engine never know which backend produced them.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from ble_radar.util import normalize_address, now_epoch

DEMO_DEVICES = [
    # name, base_rssi, band_floor (random walk limits), life (seconds or None)
    ("Smart-Bulb", -52, 0.6, None),
    ("Headphones", -38, 0.3, 90),
    ("Fitness-Tracker", -64, 0.5, 160),
    ("Beacon-01", -78, 0.4, None),
    ("Laptop", -44, 0.4, None),
    ("Chip-Tag", -88, 0.3, 60),
    ("Speaker", -60, 0.5, None),
    ("Unknown-Dongle", -72, 0.4, 120),
]


@dataclass
class Observation:
    """One advertisement batch from a device."""

    address: str
    name: str | None
    rssi: int | None
    seen_at: int
    backend: str = ""
    mfg_present: bool = False
    service_uuids: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "address": self.address,
            "name": self.name or "",
            "rssi": self.rssi,
            "seen_at": self.seen_at,
            "backend": self.backend,
            "mfg_present": self.mfg_present,
        }


class ScannerError(RuntimeError):
    """Scanning backend could not be used (no adapter, permission, ...)."""


class BaseScanner:
    """Minimal scanner protocol: start(), stop(), observations via callback."""

    def __init__(self, on_observation, clock=None):
        self._on_observation = on_observation
        self._clock = clock or now_epoch
        self._running = False

    def start(self):
        raise NotImplementedError

    def stop(self):
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def _emit(self, obs: Observation) -> None:
        if self._on_observation:
            self._on_observation(obs)


class DemoScanner(BaseScanner):
    """Synthetic devices with realistic RSSI random walks and lifecycles.

    Deterministic when seeded — used for screenshots, tests and demos.
    """

    def __init__(self, on_observation, clock=None, seed: int | None = None):
        super().__init__(on_observation, clock)
        self._rng = random.Random(seed)
        self._base = 1000

    def start(self):
        self._running = True
        self._base = self._clock()

    def _tick(self, t: int) -> None:
        for name, base, jitter, life in DEMO_DEVICES:
            addr = self._device_address(name)
            if life is not None and (t - self._base) % life > life - 20:
                continue  # device disappeared until its next cycle
            rssi = base + round(self._rng.uniform(-jitter * 20, jitter * 20))
            rssi = max(-95, min(-25, rssi))
            self._emit(
                Observation(
                    address=addr,
                    name=name,
                    rssi=rssi,
                    seen_at=t,
                    backend="demo",
                    mfg_present=self._rng.random() < 0.5,
                )
            )

    @staticmethod
    def _device_address(name: str) -> str:
        # Deterministic pseudo-address per demo device.
        import hashlib

        h = hashlib.sha256(name.encode()).hexdigest()
        return ":".join(h[i : i + 2] for i in range(0, 12, 2))

    def run_until(self, deadline: int, step: int = 1) -> None:
        """Generate observations until ``deadline`` (for tests and demo)."""
        t = self._clock()
        while t <= deadline:
            self._tick(t)
            t += step


class ReplayScanner(BaseScanner):
    """Feeds pre-recorded observations (time-ordered) to the pipeline.

    Used by replay mode: the radar sees the same Observation stream it would
    have seen live, at recorded timestamps (scaled by ``speed``).
    """

    def __init__(self, observations, on_observation, clock=None, speed: float = 1.0):
        super().__init__(on_observation, clock)
        self._observations = sorted(observations, key=lambda o: o.seen_at)
        self._speed = speed
        self._cursor = 0

    def start(self):
        self._running = True

    def next_up_to(self, t: int) -> list[Observation]:
        """Observations with ``seen_at <= t``, in order, from the cursor."""
        if not self._running:
            return []
        out = []
        while self._cursor < len(self._observations):
            obs = self._observations[self._cursor]
            if obs.seen_at <= t:
                out.append(obs)
                self._cursor += 1
            else:
                break
        return out

    def seek(self, t: int) -> None:
        self._cursor = 0
        while self._cursor < len(self._observations):
            if self._observations[self._cursor].seen_at >= t:
                break
            self._cursor += 1
