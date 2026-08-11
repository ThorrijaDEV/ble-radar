"""Pure radar frame rendering.

Builds a deterministic text grid from a device snapshot + sweep angle.
No terminal I/O — the display layer (curses) paints these frames.

Coordinates: the radar is an ellipse-corrected polar view. Terminal cells
are ~twice as tall as wide, so x offsets are doubled to keep circles round.

NOTE: ring radii are RSSI *strength bands*, not physical distance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ble_radar.lifecycle import (
    ACTIVE,
    APPEARED,
    BAND_RING,
    DISAPPEARED,
    FADING,
    rssi_band,
)

RING_CHAR = "·"
SWEEP_CHAR = "·"
SWEEP_TIP = "/"
CROSS_CHAR = "+"
TRAIL_CHAR = "·"


@dataclass
class Cell:
    row: int
    col: int
    char: str
    kind: str  # ring | sweep | trail | marker | cross
    device: str | None = None  # address for marker cells


@dataclass
class DeviceSnap:
    """Minimal device info needed for rendering (built by the engine)."""

    address: str
    label: str          # display label (name or pseudonym)
    angle: float        # stable pseudo-angle (degrees)
    band: str           # rssi_band key
    state: str
    trails: list[tuple[float, str]] = field(default_factory=list)  # (angle, band)
    jitter: float = 0.0  # deterministic radial offset within a band, -0.5..0.5


@dataclass
class Frame:
    """One rendered radar frame (grid cells + legend lines)."""

    cells: list[Cell]
    legend: list[str]
    width: int
    height: int


def ring_radius(band: str, max_radius: float) -> float:
    """Radial distance for a strength band, as a fraction of max radius.

    ``band`` maps to ring 1..5; a device sits *between* rings, with a small
    deterministic jitter so devices in the same band do not overlap.
    """
    ring = BAND_RING.get(band, 5)
    inner = (ring - 1) / 5
    span = 1 / 5
    return (inner + span * 0.5) * max_radius


def polar_cell(angle_deg: float, radius: float, cx: float, cy: float) -> tuple[int, int]:
    """Angle (deg) + radius -> cell coordinates (aspect-corrected)."""
    rad = math.radians(angle_deg)
    x = cx + radius * 2 * math.cos(rad)
    y = cy - radius * math.sin(rad)
    return round(y), round(x)


def _in_bounds(row: int, col: int, height: int, width: int) -> bool:
    return 0 <= row < height and 0 <= col < width


def render_frame(
    devices: list[DeviceSnap],
    sweep_angle: float,
    width: int,
    height: int,
    sweep_trail: float = 60.0,
) -> Frame:
    """Build one radar frame. Deterministic for identical inputs."""
    height = max(height, 5)
    width = max(width, 11)
    cx = width // 2
    cy = (height - 1) // 2
    max_radius = min(cx / 2, cy) - 1
    max_radius = max(max_radius, 2.0)

    cells: list[Cell] = []

    def place(row: int, col: int, char: str, kind: str, force: bool = False, device: str | None = None):
        if not _in_bounds(row, col, height, width):
            return
        for existing in cells:
            if existing.row == row and existing.col == col and not force:
                return
        cells.append(Cell(row, col, char, kind, device=device))

    # Crosshair
    for c in range(max(0, cx - 2), min(width, cx + 3)):
        place(cy, c, CROSS_CHAR, "cross")
    for r in range(max(0, cy - 2), min(height, cy + 3)):
        place(r, cx, CROSS_CHAR, "cross")

    # Concentric strength rings (dotted circles)
    for ring in range(1, 6):
        rr = ring / 5 * max_radius
        step = max(3, int(360 / max(12, rr * 2)))
        for ang in range(0, 360, step):
            row, col = polar_cell(ang, rr, cx, cy)
            place(row, col, RING_CHAR, "ring")

    # Sweep line with fading trail
    for offset in range(0, int(sweep_trail), 8):
        ang = sweep_angle - offset
        kind = "sweep"
        for frac in (0.25, 0.5, 0.75, 1.0):
            row, col = polar_cell(ang, max_radius * frac, cx, cy)
            place(row, col, SWEEP_CHAR, kind, force=True)

    # Devices: trails first (fainter), then markers
    for dev in devices:
        for angle, band in dev.trails:
            row, col = polar_cell(angle, ring_radius(band, max_radius), cx, cy)
            place(row, col, TRAIL_CHAR, "trail")
    for dev in devices:
        if dev.state == DISAPPEARED:
            continue
        radius = ring_radius(dev.band, max_radius) * (1 + 0.18 * dev.jitter)
        row, col = polar_cell(dev.angle, radius, cx, cy)
        label = dev.label[:3] if dev.label else "·"
        # Marker char is placed first (force), label chars around it.
        place(row, col, label[0], "marker", force=True, device=dev.address)
        for i, ch in enumerate(label[1:], start=1):
            place(row, col + i, ch, "marker", device=dev.address)
        # tiny direction hint so the marker reads as one blob
        place(row, col - 1, "(", "marker", device=dev.address)

    legend = [
        "rings: RSSI strength, NOT distance",
        " · · · · · · · · · · · · · · · · · · · · · · · · ",
        "strong>=-40 near>=-55 mid>=-70 far>=-85 weak else",
    ]
    return Frame(cells=cells, legend=legend, width=width, height=height)


def sweep_advance(sweep_angle: float, degrees: float) -> float:
    """Advance the sweep angle, wrapping at 360."""
    return (sweep_angle + degrees) % 360.0
