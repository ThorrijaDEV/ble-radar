# BLE Radar — Design Document

A live radar for the Bluetooth environment around a computer, with historical
observations and replay.

> A live BLE radar that turns nearby Bluetooth activity into a visual
> environment — with RSSI, device lifecycles, recording, and session replay.

**Status:** draft for MVP v1.0.0.

---

## 1. Concept

BLE devices constantly advertise their presence. Most tools show a plain
list. This project renders those advertisements as a *radar environment*:

- a rotating sweep line
- concentric RSSI rings
- devices positioned around the observer, with lifecycle states
  (`appeared → active → fading → disappeared`)
- recorded sessions that can be replayed later

The reaction we want: *"What the hell is that?"* followed by *"Oh, that's
actually useful."*

## 2. Technical honesty: RSSI is not distance

RSSI (received signal strength) is noisy and affected by walls, antenna
orientation, interference, per-device transmit power, reflections, and
multipath. A wall can turn a 2 m reading into a 10 m reading. Therefore:

- **We never claim exact physical distance.**
- The radar radius is a *visual mapping from RSSI strength bands*, not a
  literal map of physical location.
- The UI labels rings with strength semantics ("strong / near / mid / far /
  weak"), not meters.
- Measured data (raw RSSI per advertisement) and derived visualization
  (band, radar position) are clearly separated in the code and the UI.
- A legend is always rendered: "RSSI rings — signal strength, not distance".

This follows the honest practice of BTLEmap ("RSSI values do not allow
distance measurement, but they give an estimation on the relative distance").

## 3. Architecture

Modular so scanning and visualization evolve independently:

```
ble_radar/
├── __init__.py      version
├── cli.py           argparse CLI, command dispatch
├── scan.py          scanner backends + observation model
├── lifecycle.py     per-device state machine (appeared→…→disappeared)
├── radar.py         pure radar rendering (grid, sweep, rings, markers)
├── display.py       terminal I/O (curses), animation loop, key handling
├── db.py            SQLite schema, recording, queries
├── replay.py        replay engine: recorded events → radar frames
├── export.py        JSON/CSV export with sanitization
└── util.py          address hashing, pseudonyms, time helpers
```

### 3.1 Scanner abstraction

```python
class Observation:
    address: str            # MAC address as reported by BlueZ
    name: str | None        # advertised local name
    rssi: int | None        # measured signal strength (dBm)
    mfg: dict | None        # raw manufacturer data (opaque bytes, hashed for storage)
    service_uuids: tuple    # advertised service UUIDs
    seen_at: int            # epoch seconds (clock injected for tests)
    backend: str            # "bleak" | "demo" | "replay"
```

Backends implement a common protocol (`start`, `stop`, observation callbacks):

| Backend   | Source                                   | Use                         |
|-----------|------------------------------------------|-----------------------------|
| `bleak`   | BlueZ D-Bus via bleak 3.x                | real scanning               |
| `demo`    | synthetic scripted devices               | demos, screenshots, tests   |
| `replay`  | recorded observation stream (from SQLite or JSONL) | replay mode       |

The radar/recorder only ever sees `Observation` objects; they never know
which backend produced them. Demo mode and replay use the *same* rendering
pipeline as real scans.

### 3.2 Scanning (bleak backend)

- One **long-lived** discovery session per run. BlueZ ties discovery
  sessions to the D-Bus connection: short-lived callers (`dbus-send`,
  `bluetoothctl scan on && exit`) kill the scan when they exit. Our process
  keeps the connection open for the whole session.
- `SetDiscoveryFilter` with `Transport=le` is mandatory on LE-only
  controllers (Realtek RTL8761B in this lab) — without it, no devices are
  reported.
- Adapter power: if no powered adapter is found, report a clear error and
  hint (`bluetoothctl power on` or enable Bluetooth). bleak does not power
  the adapter; we do not either.
- Rapid start/stop cycles are avoided (known to destabilize Realtek
  dongles); the scanner runs until the user exits.
- Device identity: BlueZ device addresses are used as the stable key
  (BLE addresses are privacy-randomized by many devices; see §8).

## 4. Data model (SQLite)

```sql
CREATE TABLE sessions (
    id         INTEGER PRIMARY KEY,
    started_at INTEGER NOT NULL,
    ended_at   INTEGER,
    observations INTEGER NOT NULL DEFAULT 0,
    label      TEXT,
    backend    TEXT
);

CREATE TABLE devices (
    address    TEXT PRIMARY KEY,
    first_seen INTEGER NOT NULL,
    last_seen  INTEGER NOT NULL,
    times_seen INTEGER NOT NULL DEFAULT 0,
    last_name  TEXT,
    best_rssi  INTEGER,
    last_rssi  INTEGER
);

CREATE TABLE observations (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER REFERENCES sessions(id),
    address     TEXT NOT NULL,
    name        TEXT,
    rssi        INTEGER,
    observed_at INTEGER NOT NULL
);

CREATE INDEX idx_obs_addr_time ON observations(address, observed_at);
CREATE INDEX idx_obs_time ON observations(observed_at);
```

- One row per **advertisement batch** per device (the scanner coalesces
  identical consecutive advertisements within a scan window).
- `devices` is the census (upsert on each observation, like
  `wifi-time-machine`).
- Raw manufacturer data is **not stored** in the MVP (it can carry device
  identifiers); only its presence/length may be exported.

**History questions the schema answers:** first/last seen, visibility
duration, RSSI over time, disappearance time (last observation), devices
present in a session, environment diff between two sessions.

## 5. Device lifecycle (event model)

Per-device state machine driven by observation timestamps:

```
appeared ──▶ active ──▶ fading ──▶ disappeared
                 ▲                   │
                 └── re-appeared ─────┘
```

| State        | Trigger                                                    | Rendering               |
|--------------|------------------------------------------------------------|-------------------------|
| `appeared`   | first observation of an address                            | bright flash, NEW badge |
| `active`     | observed within the last `active_window` (default 15 s)    | solid marker            |
| `fading`     | no observation for `active_window`..`gone_window` (30 s)   | dim marker, ghost trail |
| `disappeared`| no observation for > `gone_window`                         | removed from radar, listed as gone |

Lifecycle is computed *in memory* from the observation stream (pure
function of `(state, last_seen, now)` → testable). Reappearance resets to
`appeared`.

## 6. Radar coordinate system

- Terminal grid; radar center at a fixed cell.
- **Angle:** deterministic per address — `angle = hash(address) % 360`,
  stable across sessions so a device does not jump around. This is a
  *visual convention*, not a bearing (BLE provides no angle information).
- **Radius:** derived from the RSSI band of the smoothed signal, mapped to
  rings (not a distance formula).

### 6.1 RSSI bands (strength, not meters)

| Band        | RSSI threshold (dBm) | Ring |
|-------------|----------------------|------|
| `strong`    | ≥ −40                | innermost ring (ring 1) |
| `near`      | −40 … −55            | ring 2 |
| `mid`       | −55 … −70            | ring 3 |
| `far`       | −70 … −85            | ring 4 |
| `weak`      | < −85                | outer area (ring 5) |

- Marker radius = ring index + small deterministic jitter (hash-based) to
  avoid overlapping markers at the same band.
- Displayed RSSI = **EMA-smoothed** value (α ≈ 0.4 per sample) to reduce
  flicker; the raw measured value is shown in the device panel.
- The legend states: rings are *strength bands*, not meters.

### 6.2 Rendering (pure functions)

`radar.py` builds a frame as a plain grid (list of rows of cells with
optional ANSI color), given: devices snapshot, sweep angle, rings, size.
Deterministic given inputs → unit-testable without a terminal.

Visual elements:

- rotating sweep: `╱` at the sweep angle on the ring arcs
- concentric rings drawn with `─ │ ╭ ╮ ╰ ╯ ·` per ring radius
- device markers: first 3 characters of name/pseudonym, `·` for unnamed
- trails: the last few marker positions drawn with decreasing brightness
- colors: green (strong) → yellow → red (weak); grey for fading
- blinking/flash on `appeared`

## 7. Terminal UI

- Full-screen curses app (`display.py`). stdlib only; no framework.
- Left ~70%: radar. Right ~30%: device list (name/pseudonym, state, RSSI,
  age) sorted by strength. Bottom: status bar (backend, session label,
  device count, elapsed, controls).
- Keys: `q` quit, `p` pause/resume, `+`/`-` sweep speed (replay: playback
  speed), `d` toggle detail.
- Resize: curses `SIGWINCH`; radar recomputes from the new size.
- Rendering stays pure; curses only paints frames and reads keys.

## 8. Privacy

- **Local-only.** No telemetry, no uploads, no external services.
- Display: default label is a short pseudonym (`D-3F2A`) derived from the
  address; the advertised name is shown when available (it is *publicly
  broadcast by the device itself*), but the raw address is shown by
  default unless `--show-addresses` is passed.
- Exports: `--sanitize` replaces addresses with truncated SHA-256 hashes
  (non-reversible), like `wifi-time-machine`.
- Manufacturer data payloads are not stored or exported in the MVP.
- Replay/demo material is always generated from synthetic fixtures; real
  device data is never published.

## 9. Replay mode

Signature feature. `ble-radar replay [SESSION]`:

- Reads a recorded session's observations (SQLite), ordered by time.
- Feeds them through the same radar pipeline with a time cursor.
- The radar reconstructs the environment: devices appear, move, fade,
  disappear exactly as recorded.
- Controls: pause, speed up/down (×0.5 … ×64), seek to time.
- Works without hardware — presentations and demos anywhere.
- Replay can also ingest JSONL observation files (export format), so a
  session recorded elsewhere can be replayed.

## 10. Demo mode

`ble-radar demo`:

- Synthetic scripted devices (e.g. `Smart-Bulb`, `Headphones`, `Tracker`,
  `Beacon-01`) with believable RSSI trajectories (random walk within a
  band, occasional band shifts) and lifecycle events (appear, fade,
  disappear, reappear).
- Same rendering pipeline as live scanning.
- Used for screenshots, README, portfolio material, and presentations.
- Deterministic with a seed (`--seed`) for reproducible screenshots.

## 11. CLI

```
ble-radar radar [--backend bleak|demo] [--db PATH] [--label L] [--duration S]
ble-radar record [--duration S] [--label L] [--db PATH]      # headless session
ble-radar replay [SESSION_ID|latest] [--speed N] [--from T] [--db PATH]
ble-radar demo [--seed N] [--duration S]                    # radar + demo backend
ble-radar sessions [--db PATH]                              # list recorded sessions
ble-radar history [--device A] [--since T] [--until T] [--limit N]
ble-radar export [--format json|csv] [--sanitize] [--output F]
ble-radar status [--db PATH]
```

Exit codes: 0 ok · 1 error · 2 no adapter/backend unavailable (matches
`wifi-time-machine` conventions).

## 12. Dependencies

- **Runtime:** `bleak>=3.0` (BlueZ D-Bus, pulls `dbus-fast`). stdlib for
  everything else (curses, sqlite3, argparse, json/csv).
- **Dev:** `pytest`.
- No large TUI framework: curses is sufficient and honest.
- Python ≥ 3.10.

## 13. Testing strategy

Deterministic, no hardware required:

- **Scanner:** fake backend with scripted observations; bleak backend
  errors (no adapter, permission denied) simulated via a stub that raises
  the same exceptions (`BleakError` / `BleakBluetoothNotAvailableError`).
- **Lifecycle:** pure transition tests (appear, active, fade, disappear,
  reappear, time boundaries).
- **RSSI:** band mapping boundaries, EMA smoothing, jitter stability,
  None handling.
- **Radar rendering:** golden-frame tests at fixed sizes — marker
  positions, sweep angle, rings, trails, colors, legend; resizing
  (render at multiple sizes); empty environment; all-fading environment.
- **DB:** schema, upsert, session lifecycle, queries (per-device history,
  session contents, time windows, diffs between sessions).
- **Replay:** event ordering, speed scaling, seek, malformed session,
  missing session, empty session.
- **Demo:** determinism with fixed seed (two runs → identical frames).
- **CLI:** exit codes, help, bad args, export sanitization.
- **Fixtures:** synthetic JSONL observation streams + captured real bleak
  advertisement output (addresses hashed) as parse fixtures.

## 14. Limitations (stated honestly)

- RSSI-based "distance" is strength, not location (§2).
- Angle is a stable visual convention, not a bearing.
- BLE privacy addresses rotate; a device seen under several addresses
  appears as several devices (no IRK-based resolution in MVP — btrpa-scan
  does this; noted as future work).
- Many devices only advertise while connectable or in pairing windows;
  some (e.g. phones) are invisible while locked.
- Scan coverage depends on the adapter; the RTL8761B has no antenna
  diversity (single chip antenna).
- MVP has no web UI; the terminal radar is the interface.

## 15. Future ideas (post-MVP)

- Web radar view (canvas) streaming from the same observation pipeline
- IRK-based private-address resolution
- Device type classification from service UUIDs/manufacturer data
- Multi-adapter scanning; multi-node triangulation between machines
- Auto-start scanning daemon with systemd user unit
- pcap-style JSONL export compatible with replay on other machines

## 16. Inspiration and attribution

Inspired by (studied, not copied): HackingDave/btrpa-scan (Apache-2.0,
radar UX: hash-angle + log-distance), seemoo-lab/BTLEmap (Apache-2.0,
honest RSSI treatment, framework separation), BLE-Research-Group/MetaRadar
(GPL-3.0, radar UI + classification), Eric-8787/Sonar-BLE-Radar (MIT,
sweep animation). This is an independent implementation; no code is reused.
