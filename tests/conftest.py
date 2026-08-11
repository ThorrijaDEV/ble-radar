"""Shared fixtures: fake clock, fake scanner callback, in-memory DB."""

from __future__ import annotations

import sqlite3

import pytest

from ble_radar import db as dbmod
from ble_radar.scan import Observation


@pytest.fixture
def clock():
    """Mutable fake clock returning a counter value, starting at 1_000_000."""
    box = {"t": 1_000_000}

    def now() -> int:
        return box["t"]

    def advance(seconds: int = 1) -> int:
        box["t"] += seconds
        return box["t"]

    now.advance = advance  # type: ignore[attr-defined]
    now.set = lambda t: box.__setitem__("t", t)  # type: ignore[attr-defined]
    return now


@pytest.fixture
def sink():
    """Collects every Observation a scanner emits."""
    collected = []

    def on_observation(obs):
        collected.append(obs)

    on_observation.collected = collected  # type: ignore[attr-defined]
    return on_observation


@pytest.fixture
def conn(tmp_path):
    c = dbmod.connect(tmp_path / "test.db")
    yield c
    c.close()


def make_obs(address, rssi, seen_at, name=None, **kw) -> Observation:
    return Observation(
        address=address, name=name, rssi=rssi, seen_at=seen_at, **kw
    )


def obs_from_row(row):
    return Observation(
        address=row["address"],
        name=row["name"],
        rssi=row["rssi"],
        seen_at=row["observed_at"],
        session_id=row["session_id"],
    )
