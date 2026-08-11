"""Terminal display: curses full-screen radar UI.

Rendering stays pure (radar.py); this module paints frames, draws the device
panel, and reads keys.
"""

from __future__ import annotations

import curses

from ble_radar.lifecycle import ACTIVE, APPEARED, BAND_RING, FADING
from ble_radar.radar import render_frame

# curses color pairs: (kind, band) -> pair index
_PAIR_FG = {}


def _make_pairs():
    curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)     # strong
    curses.init_pair(2, curses.COLOR_YELLOW, curses.COLOR_BLACK)    # near
    curses.init_pair(3, curses.COLOR_CYAN, curses.COLOR_BLACK)      # mid
    curses.init_pair(4, curses.COLOR_MAGENTA, curses.COLOR_BLACK)   # far
    curses.init_pair(5, curses.COLOR_RED, curses.COLOR_BLACK)       # weak
    curses.init_pair(6, curses.COLOR_BLUE, curses.COLOR_BLACK)      # rings/sweep
    curses.init_pair(7, curses.COLOR_WHITE, curses.COLOR_BLACK)     # default
    curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_GREEN)     # appeared flash
    curses.init_pair(9, curses.COLOR_BLACK, curses.COLOR_YELLOW)    # fading


BAND_PAIR = {"strong": 1, "near": 2, "mid": 3, "far": 4, "weak": 5, "unknown": 7}


class RadarUI:
    def __init__(self, screen, db_path: str, backend: str, label: str | None = None):
        self.screen = screen
        self.db_path = db_path
        self.backend = backend
        self.label = label or ""
        curses.curs_set(0)
        curses.start_color()
        _make_pairs()
        self.screen.timeout(100)  # 10 fps main loop
        self.sweep_angle = 0.0
        self.paused = False
        self.show_addresses = False
        self.status = ""
        self.elapsed = 0
        self.replay_mode = False
        self.replay_speed = 1.0
        self.replay_end = False

    def set_replay(self, mode: bool, speed: float = 1.0):
        self.replay_mode = mode
        self.replay_speed = speed

    def key(self) -> str | None:
        ch = self.screen.getch()
        if ch == -1:
            return None
        return chr(ch) if ch < 256 else "?"

    def paint(self, snaps, legend: list[str], width: int, height: int) -> None:
        frame = render_frame(snaps, self.sweep_angle, width, height)
        self.screen.erase()
        for cell in frame.cells:
            pair = self._pair_for(cell, snaps)
            try:
                self.screen.addstr(cell.row, cell.col, cell.char, pair)
            except curses.error:
                pass
        self._paint_panel(snaps, frame, height, width)
        self._paint_status(height, width)
        self.screen.refresh()

    def _pair_for(self, cell, snaps) -> int:
        kind = cell.kind
        if kind == "marker":
            addr = cell.device
            state = self._state_for(addr, snaps)
            if state == APPEARED:
                return curses.color_pair(8)
            if state == FADING:
                return curses.color_pair(9)
            band = self._band_for(addr, snaps)
            return curses.color_pair(BAND_PAIR.get(band, 7))
        if kind in ("ring", "sweep", "trail"):
            return curses.color_pair(6)
        return curses.color_pair(7)

    @staticmethod
    def _state_for(addr, snaps) -> str:
        if addr is None:
            return ACTIVE
        for s in snaps:
            if s.address == addr:
                return s.state
        return ACTIVE

    @staticmethod
    def _band_for(addr, snaps) -> str:
        if addr is None:
            return "unknown"
        for s in snaps:
            if s.address == addr:
                return s.band
        return "unknown"

    def _paint_panel(self, snaps, frame, height, width):
        panel_w = max(20, min(34, width // 3))
        x0 = width - panel_w
        try:
            self.screen.addstr(0, x0, "DEVICES", curses.color_pair(7) | curses.A_BOLD)
        except curses.error:
            pass
        for i, snap in enumerate(snaps[: height - 6]):
            row = i + 1
            if snap.state == APPEARED:
                flag = "NEW"
            elif snap.state == FADING:
                flag = "FAD"
            else:
                flag = "   "
            label = snap.label[:14]
            band = snap.band[:5]
            line = f"{label:<14} {flag} {band}"
            try:
                self.screen.addstr(row, x0, line[:panel_w], self._pair_for_panel(snap))
            except curses.error:
                pass

    def _pair_for_panel(self, snap) -> int:
        if snap.state == APPEARED:
            return curses.color_pair(8)
        if snap.state == FADING:
            return curses.color_pair(9)
        return curses.color_pair(BAND_PAIR.get(snap.band, 7))

    def _paint_status(self, height, width):
        mode = "REPLAY" if self.replay_mode else "LIVE"
        speed = f" x{self.replay_speed}" if self.replay_mode else ""
        status = (
            f" {mode}{speed}  {self.backend}  {self.db_path}  "
            f"elapsed {self.elapsed}s  {self.status}"
        )
        try:
            self.screen.addstr(
                height - 1, 0, status[: width - 1], curses.color_pair(7)
            )
            controls = "q quit  p pause  +/- speed  d addresses"
            self.screen.addstr(
                height - 2,
                0,
                controls[: width - 1],
                curses.color_pair(6),
            )
        except curses.error:
            pass
