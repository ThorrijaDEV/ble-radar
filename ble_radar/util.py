"""Small shared helpers: address hashing, pseudonyms, time, smoothing."""

from __future__ import annotations

import datetime as _dt
import hashlib
import re

_ADDR_RE = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")


def now_epoch() -> int:
    return int(_dt.datetime.now(_dt.timezone.utc).timestamp())


def normalize_address(address: str) -> str:
    """Normalize a MAC-style address to lowercase; raises ValueError."""
    if not _ADDR_RE.match(address):
        raise ValueError(f"invalid BLE address: {address!r}")
    return address.lower()


def hash_address(address: str) -> str:
    """Privacy-preserving identifier: first 12 hex chars of SHA-256."""
    return hashlib.sha256(normalize_address(address).encode("ascii")).hexdigest()[:12]


def pseudonym(address: str) -> str:
    """Short display label, e.g. ``D-3f2a``, derived from the address."""
    h = hash_address(address)
    return f"D-{h[-4:]}"


def stable_angle(address: str) -> float:
    """Deterministic pseudo-angle (0-360) from an address hash.

    Visual convention only — BLE provides no bearing information.
    """
    digest = hashlib.sha256(normalize_address(address).encode("ascii")).digest()
    return (int.from_bytes(digest[:2], "big") / 65536) * 360.0


def ema(prev: float | None, value: float, alpha: float = 0.4) -> float:
    """Exponential moving average; returns ``value`` when ``prev`` is None."""
    if prev is None:
        return float(value)
    return alpha * value + (1 - alpha) * prev


def fmt_duration(seconds: float) -> str:
    """``3h12m`` / ``4m05s`` / ``42s`` style formatting."""
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def fmt_time(epoch: int) -> str:
    return _dt.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def parse_time(spec: str | None, now: int | None = None) -> int | None:
    """Parse ``30m``, ``2h``, ``1d``, ``2026-08-11``, ``10:30`` … → epoch."""
    if spec is None:
        return None
    spec = spec.strip()
    if not spec:
        return None
    now = now or now_epoch()
    m = re.match(r"^(\d+)([smhd])$", spec)
    if m:
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
        return now - int(m.group(1)) * mult
    text = spec.replace("T", " ").replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%H:%M"):
        try:
            dt = _dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%H:%M":
            dt = dt.replace(
                year=_dt.datetime.fromtimestamp(now).year,
                month=_dt.datetime.fromtimestamp(now).month,
                day=_dt.datetime.fromtimestamp(now).day,
            )
        return int(dt.replace(tzinfo=_dt.timezone.utc).timestamp())
    raise ValueError(f"cannot parse time spec: {spec!r}")
