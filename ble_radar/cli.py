"""Command-line interface for BLE Radar."""

from __future__ import annotations

import argparse
import queue
import sqlite3
import sys
import time

from ble_radar import __version__
from ble_radar import db as dbmod
from ble_radar import export as exportmod
from ble_radar import scan as scanmod
from ble_radar.util import fmt_time, now_epoch, parse_time, pseudonym

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_BACKEND = 2

_BLEAK_BACKEND = "bleak"
_DEMO_BACKEND = "demo"
_REPLAY_BACKEND = "replay"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ble-radar",
        description="A live radar for the Bluetooth environment around you — "
        "with RSSI, device lifecycles, recording and session replay.",
        epilog="example: ble-radar radar   then: ble-radar replay latest",
    )
    parser.add_argument("--version", action="version", version=f"ble-radar {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--db",
        metavar="PATH",
        default=None,
        help="SQLite database path (default: $XDG_DATA_HOME/ble-radar/radar.db, override with BLE_RADAR_DB)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("radar", parents=[common], help="live radar (full-screen)")
    p.add_argument("--backend", choices=["auto", _BLEAK_BACKEND, _DEMO_BACKEND], default="auto")
    p.add_argument("--label", default=None, help="label for the recording session")
    p.add_argument("--duration", type=int, default=None, help="stop after N seconds")
    p.add_argument("--no-record", action="store_true", help="do not store observations")

    p = sub.add_parser("record", parents=[common], help="headless recording session")
    p.add_argument("--backend", choices=["auto", _BLEAK_BACKEND, _DEMO_BACKEND], default="auto")
    p.add_argument("--label", default=None)
    p.add_argument("--duration", type=int, default=None)
    p.add_argument("--limit", type=int, default=None, help="stop after N observations")
    p.add_argument("--interval", type=float, default=1.0, help="summary print interval (s)")

    p = sub.add_parser("replay", parents=[common], help="replay a recorded session on the radar")
    p.add_argument("session", nargs="?", default="latest", help="session ID or 'latest'")
    p.add_argument("--speed", type=float, default=1.0, help="playback speed")
    p.add_argument("--from", dest="from_time", default=None, help="start time (epoch or offset like 1m)")

    p = sub.add_parser("demo", parents=[common], help="synthetic radar demo (same pipeline)")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--duration", type=int, default=None)
    p.add_argument("--no-record", action="store_true")

    p = sub.add_parser("sessions", parents=[common], help="list recorded sessions")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("history", parents=[common], help="observation history")
    p.add_argument("--device", default=None, help="address or pseudonym prefix")
    p.add_argument("--session", type=int, default=None)
    p.add_argument("--since", default=None)
    p.add_argument("--until", default=None)
    p.add_argument("--limit", type=int, default=100)

    p = sub.add_parser("export", parents=[common], help="export observations (JSON or CSV)")
    p.add_argument("--format", choices=["json", "csv"], default="json")
    p.add_argument("--output", default="-")
    p.add_argument("--sanitize", action="store_true", help="hash addresses (SHA-256 truncated)")
    p.add_argument("--session", type=int, default=None)
    p.add_argument("--since", default=None)
    p.add_argument("--until", default=None)
    p.add_argument("--limit", type=int, default=100000)

    p = sub.add_parser("status", parents=[common], help="database and backend status")
    return parser


def _open_db(args) -> sqlite3.Connection:
    return dbmod.connect(args.db or dbmod.default_db_path())


def _table(headers: list[str], rows: list[list]) -> str:
    widths = [
        max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(str(h))
        for i, h in enumerate(headers)
    ]
    lines = ["  ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers))]
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)))
    return "\n".join(lines)


def _make_scanner(backend: str, on_observation, seed: int | None = None):
    """Resolve backend choice; returns (backend_name, scanner)."""
    if backend in (None, "auto"):
        try:
            from ble_radar.backends import BleakScannerBackend

            return _BLEAK_BACKEND, BleakScannerBackend(on_observation)
        except Exception:
            return _DEMO_BACKEND, scanmod.DemoScanner(on_observation, seed=seed)
    if backend == _BLEAK_BACKEND:
        from ble_radar.backends import BleakScannerBackend

        return _BLEAK_BACKEND, BleakScannerBackend(on_observation)
    return _DEMO_BACKEND, scanmod.DemoScanner(on_observation, seed=seed)


# ---------------------------------------------------------------------------
# Recording plumbing
# ---------------------------------------------------------------------------


def _drain(q: queue.Queue, engine, conn, session_id: int | None) -> int:
    """Move queued observations into the engine and (optionally) the database."""
    n = 0
    while not q.empty():
        try:
            obs = q.get_nowait()
        except queue.Empty:
            break
        engine.ingest(obs)
        if session_id is not None:
            dbmod.record_observation(conn, session_id, obs)
        n += 1
    return n


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_radar(args) -> int:
    from ble_radar.display import RadarUI
    from ble_radar.engine import RadarEngine
    from ble_radar.radar import sweep_advance

    q: queue.Queue = queue.Queue()
    try:
        backend_name, scanner = _make_scanner(args.backend, q.put, seed=None)
    except scanmod.ScannerError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_NO_BACKEND
    conn = _open_db(args)
    session_id = None
    if not args.no_record:
        session_id = dbmod.start_session(conn, args.label, backend_name)
    engine = RadarEngine()

    import curses

    count = 0

    def run(screen):
        nonlocal count
        ui = RadarUI(screen, str(args.db or dbmod.default_db_path()), backend_name, args.label)
        scanner.start()
        start = now_epoch()
        last_tick = time.monotonic()
        last_pump = 0
        try:
            while True:
                now = now_epoch()
                dt = time.monotonic() - last_tick
                last_tick = time.monotonic()
                if args.duration and now - start >= args.duration:
                    break
                if backend_name == _DEMO_BACKEND and now - last_pump >= 1:
                    last_pump = now
                    scanner._tick(now)
                count += _drain(q, engine, conn, session_id)
                if not ui.paused:
                    ui.sweep_angle = sweep_advance(ui.sweep_angle, dt * 30)
                snaps = engine.snapshot(now)
                ui.elapsed = now - start
                ui.status = f"{len(snaps)} devices  {count} obs"
                y, x = screen.getmaxyx()
                ui.paint(snaps, [], x, y)
                key = ui.key()
                if key == "q":
                    break
                if key == "p":
                    ui.paused = not ui.paused
                if key == "+":
                    ui.replay_speed = min(64.0, ui.replay_speed * 2)
                if key == "-":
                    ui.replay_speed = max(0.25, ui.replay_speed / 2)
        except KeyboardInterrupt:
            pass
        finally:
            scanner.stop()

    ok = False
    try:
        code = curses.wrapper(run)
        ok = True
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
    finally:
        if session_id is not None:
            if ok:
                dbmod.end_session(conn, session_id, count)
            else:
                dbmod.delete_session(conn, session_id)
        conn.close()
    if not ok:
        return EXIT_ERROR
    print(f"\nsession stored: {count} observations ({backend_name})")
    return code


def cmd_record(args) -> int:
    from ble_radar.engine import RadarEngine

    q: queue.Queue = queue.Queue()
    try:
        backend_name, scanner = _make_scanner(args.backend, q.put, seed=None)
    except scanmod.ScannerError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_NO_BACKEND
    conn = _open_db(args)
    session_id = dbmod.start_session(conn, args.label, backend_name)
    engine = RadarEngine()
    scanner.start()
    start = now_epoch()
    last_report = time.monotonic()
    last_pump = 0
    count = 0
    print(f"recording via {backend_name} into {conn.execute('PRAGMA database_list').fetchone()[2]}")
    print("ctrl+C to stop")
    try:
        while True:
            now = now_epoch()
            if args.duration and now - start >= args.duration:
                break
            if args.limit and count >= args.limit:
                break
            if backend_name == _DEMO_BACKEND and now - last_pump >= 1:
                last_pump = now
                scanner._tick(now)
            count += _drain(q, engine, conn, session_id)
            snaps = engine.snapshot(now)
            t = time.monotonic()
            if t - last_report >= args.interval:
                last_report = t
                print(
                    f"{fmt_time(now)}  {len(snaps)} devices  "
                    f"{count} observations  "
                    f"({len(engine.devices)} total seen)"
                )
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        scanner.stop()
        dbmod.end_session(conn, session_id, count)
    print(f"session finished: {count} observations, {len(engine.devices)} devices")
    return EXIT_OK


def _replay_from(spec: str | None, start_t: int) -> int | None:
    """Replay start time: '+2m' relative to session start, else absolute."""
    if spec is None or not spec.strip():
        return None
    import re

    m = re.match(r"^\+(\d+)([smhd])$", spec.strip())
    if m:
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
        return start_t + int(m.group(1)) * mult
    return parse_time(spec)


def cmd_replay(args) -> int:
    from ble_radar.engine import RadarEngine
    from ble_radar.replay import ReplaySession
    from ble_radar.scan import Observation

    conn = _open_db(args)
    if args.session == "latest":
        row = conn.execute("SELECT id FROM sessions ORDER BY started_at DESC LIMIT 1").fetchone()
        if row is None:
            print("no sessions recorded yet — run `ble-radar radar` first", file=sys.stderr)
            return EXIT_ERROR
        session_id = row["id"]
    else:
        try:
            session_id = int(args.session)
        except ValueError:
            print(f"invalid session: {args.session!r}", file=sys.stderr)
            return EXIT_ERROR
    rows = dbmod.session_observations(conn, session_id)
    if not rows:
        print(f"session {session_id} has no observations", file=sys.stderr)
        return EXIT_ERROR
    observations = [
        Observation(
            address=r["address"],
            name=r["name"],
            rssi=r["rssi"],
            seen_at=r["observed_at"],
            backend="replay",
        )
        for r in rows
    ]
    start_t = observations[0].seen_at
    from_time = _replay_from(args.from_time, start_t)
    try:
        session = ReplaySession(observations, speed=args.speed, from_time=from_time)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    engine = RadarEngine()

    import curses

    def run(screen):
        from ble_radar.display import RadarUI
        from ble_radar.radar import sweep_advance

        ui = RadarUI(screen, str(args.db or dbmod.default_db_path()), "replay")
        ui.set_replay(True, session.speed)
        last_tick = time.monotonic()
        end_shown = None
        try:
            while True:
                dt = time.monotonic() - last_tick
                last_tick = time.monotonic()
                if not ui.paused and not session.done:
                    for obs in session.advance(dt, ui.replay_speed):
                        engine.ingest(obs)
                    ui.replay_speed = session.speed
                if session.done:
                    end_shown = end_shown or time.monotonic()
                    if time.monotonic() - end_shown > 3:
                        break
                now = now_epoch()
                snaps = engine.snapshot(now)
                ui.sweep_angle = sweep_advance(ui.sweep_angle, dt * 30)
                ui.elapsed = int(session.replay_t - session.start_t)
                ui.status = (
                    f"progress {int(session.progress * 100)}%"
                    + ("  END" if session.done else "")
                )
                y, x = screen.getmaxyx()
                ui.paint(snaps, [], x, y)
                key = ui.key()
                if key == "q":
                    break
                if key == "p":
                    ui.paused = not ui.paused
                if key == "+":
                    session.speed = min(64.0, session.speed * 2)
                    ui.replay_speed = session.speed
                if key == "-":
                    session.speed = max(0.1, session.speed / 2)
                    ui.replay_speed = session.speed
        except KeyboardInterrupt:
            pass

    try:
        code = curses.wrapper(run)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    return code


def cmd_demo(args) -> int:
    from ble_radar.engine import RadarEngine

    q: queue.Queue = queue.Queue()
    scanner = scanmod.DemoScanner(q.put, seed=args.seed)
    conn = _open_db(args)
    session_id = None
    if not args.no_record:
        session_id = dbmod.start_session(conn, "demo", "demo")
    engine = RadarEngine()

    import curses

    count = 0

    def run(screen):
        nonlocal count
        from ble_radar.display import RadarUI
        from ble_radar.radar import sweep_advance

        ui = RadarUI(screen, str(args.db or dbmod.default_db_path()), "demo")
        scanner.start()
        start = now_epoch()
        last_tick = time.monotonic()
        last_pump = 0
        try:
            while True:
                now = now_epoch()
                if args.duration and now - start >= args.duration:
                    break
                dt = time.monotonic() - last_tick
                last_tick = time.monotonic()
                if now - last_pump >= 1:
                    last_pump = now
                    scanner._tick(now)
                count += _drain(q, engine, conn, session_id)
                if not ui.paused:
                    ui.sweep_angle = sweep_advance(ui.sweep_angle, dt * 30)
                snaps = engine.snapshot(now)
                ui.elapsed = now - start
                ui.status = f"{len(snaps)} devices  {count} obs (synthetic)"
                y, x = screen.getmaxyx()
                ui.paint(snaps, [], x, y)
                key = ui.key()
                if key == "q":
                    break
                if key == "p":
                    ui.paused = not ui.paused
        except KeyboardInterrupt:
            pass
        finally:
            scanner.stop()

    ok = False
    try:
        code = curses.wrapper(run)
        ok = True
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
    finally:
        if session_id is not None:
            if ok:
                dbmod.end_session(conn, session_id, count)
            else:
                dbmod.delete_session(conn, session_id)
        conn.close()
    if not ok:
        return EXIT_ERROR
    print(f"\ndemo session stored: {count} observations")
    return code


def cmd_sessions(args) -> int:
    conn = _open_db(args)
    rows = dbmod.list_sessions(conn, limit=args.limit)
    if not rows:
        print("no sessions recorded yet")
        return EXIT_OK
    table = [
        [
            r["id"],
            fmt_time(r["started_at"]),
            fmt_time(r["ended_at"]) if r["ended_at"] else "-",
            r["observations"],
            r["devices"],
            r["label"] or "",
            r["backend"] or "",
        ]
        for r in rows
    ]
    print(_table(["ID", "STARTED", "ENDED", "OBS", "DEVICES", "LABEL", "BACKEND"], table))
    return EXIT_OK


def cmd_history(args) -> int:
    conn = _open_db(args)
    since = parse_time(args.since)
    until = parse_time(args.until)
    try:
        rows = dbmod.observations(
            conn, session_id=args.session, since=since, until=until, limit=args.limit
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    if args.device:
        target = args.device.lower()
        rows = [r for r in rows if target in r["address"] or target in (r["name"] or "").lower()]
    if not rows:
        print("no observations match")
        return EXIT_OK
    table = [
        [
            fmt_time(r["observed_at"]),
            r["address"],
            r["name"] or pseudonym(r["address"]),
            r["rssi"] if r["rssi"] is not None else "-",
            r["session_id"],
        ]
        for r in rows
    ]
    print(_table(["OBSERVED AT", "ADDRESS", "NAME", "RSSI dBm", "SESSION"], table))
    return EXIT_OK


def cmd_export(args) -> int:
    conn = _open_db(args)
    since = parse_time(args.since)
    until = parse_time(args.until)
    try:
        rows = dbmod.observations(
            conn, session_id=args.session, since=since, until=until, limit=args.limit
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    data = exportmod.to_json(rows, sanitize=args.sanitize) if args.format == "json" else exportmod.to_csv(rows, sanitize=args.sanitize)
    if args.output == "-":
        if not args.sanitize and rows:
            print(exportmod.SANITIZE_WARNING, file=sys.stderr)
        print(data)
    else:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(data)
        if not args.sanitize and rows:
            print(exportmod.SANITIZE_WARNING, file=sys.stderr)
        print(f"exported {len(rows)} observations to {args.output}")
    return EXIT_OK


def cmd_status(args) -> int:
    db_path = str(args.db or dbmod.default_db_path())
    try:
        conn = dbmod.connect(db_path)
        st = dbmod.stats(conn)
    except Exception as e:
        print(f"database error: {e}", file=sys.stderr)
        return EXIT_ERROR
    backend = "unavailable"
    try:
        from ble_radar.backends import BleakScannerBackend

        if BleakScannerBackend(None)._is_adapter_available():
            backend = "bleak (adapter found)"
    except Exception:
        pass
    print(f"database  : {db_path}")
    print(f"backend   : {backend}")
    print(f"sessions  : {st['sessions']}")
    print(f"devices   : {st['devices']}")
    print(f"observations: {st['observations']}")
    first = fmt_time(st["first"]) if st["first"] else "-"
    last = fmt_time(st["last"]) if st["last"] else "-"
    print(f"span      : {first} → {last}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    try:
        parser = _build_parser()
        args = parser.parse_args(argv)
        handlers = {
            "radar": cmd_radar,
            "record": cmd_record,
            "replay": cmd_replay,
            "demo": cmd_demo,
            "sessions": cmd_sessions,
            "history": cmd_history,
            "export": cmd_export,
            "status": cmd_status,
        }
        return handlers[args.command](args)
    except scanmod.ScannerError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_NO_BACKEND
    except KeyboardInterrupt:
        return EXIT_OK
    except SystemExit as e:
        return int(e.code or 0)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
