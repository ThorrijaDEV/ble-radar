"""Replay engine: recorded observations -> live radar reconstruction.

Feeds observations to the same RadarEngine pipeline at a controllable speed,
so replay behaves exactly like a live scan of the past.
"""

from __future__ import annotations

from ble_radar.scan import Observation, ReplayScanner


class ReplaySession:
    def __init__(
        self,
        observations: list[Observation],
        speed: float = 1.0,
        from_time: int | None = None,
    ):
        if not observations:
            raise ValueError("session has no observations to replay")
        self._observations = sorted(observations, key=lambda o: o.seen_at)
        self._scanner = ReplayScanner(self._observations, None)
        self._scanner.start()
        self.replay_t = from_time if from_time is not None else self._observations[0].seen_at
        self._scanner.seek(self.replay_t)
        self.start_t = self._observations[0].seen_at
        self.end_t = self._observations[-1].seen_at
        self.speed = max(0.1, speed)
        self.done = False
        self.seeked = from_time is not None

    @property
    def progress(self) -> float:
        span = max(self.end_t - self.start_t, 1)
        return max(0.0, min(1.0, (self.replay_t - self.start_t) / span))

    def advance(self, wall_seconds: float, speed: float | None = None) -> list[Observation]:
        """Move the replay clock by ``wall_seconds * speed``; returns new observations."""
        if speed is not None:
            self.speed = max(0.1, speed)
        if self.done:
            return []
        self.replay_t += wall_seconds * self.speed
        if self.replay_t >= self.end_t:
            self.replay_t = self.end_t
            self.done = True
        return self._scanner.next_up_to(self.replay_t)

    def seek(self, t: int) -> None:
        self.replay_t = max(self.start_t, min(self.end_t, t))
        self._scanner.seek(self.replay_t)
        self.done = self.replay_t >= self.end_t
