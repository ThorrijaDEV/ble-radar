"""CLI tests (headless commands only — curses paths need a TTY)."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from ble_radar import db as dbmod
from ble_radar import __version__
from ble_radar.cli import EXIT_ERROR, EXIT_OK, _replay_from, main
from ble_radar.scan import Observation


class TestInvocation:
    """The CLI must stay invocable (module fallback) — regression for the
    'ble-radar: command not found' report: the entry point lives in the
    venv, so `python -m ble_radar` is the documented fallback."""

    def _run(self, *argv, env=None):
        env_full = dict(os.environ)
        env_full.pop("BLE_RADAR_DB", None)
        if env:
            env_full.update(env)
        return subprocess.run(
            [sys.executable, "-m", "ble_radar", *argv],
            capture_output=True,
            text=True,
            timeout=60,
            env=env_full,
        )

    def test_module_version(self):
        p = self._run("--version")
        assert p.returncode == 0
        assert __version__ in p.stdout

    def test_module_status(self, tmp_path):
        p = self._run("status", env={"BLE_RADAR_DB": str(tmp_path / "r.db")})
        assert p.returncode == 0
        assert "database" in p.stdout

    def test_module_sessions_empty(self, tmp_path):
        p = self._run("sessions", env={"BLE_RADAR_DB": str(tmp_path / "r.db")})
        assert p.returncode == 0
        assert "no sessions" in p.stdout

    def test_module_help(self):
        p = self._run("--help")
        assert p.returncode == 0
        assert "radar" in p.stdout


class TestReplayFrom:
    def test_none(self):
        assert _replay_from(None, 1000) is None

    def test_relative_offset(self):
        assert _replay_from("+2m", 1000) == 1000 + 120
        assert _replay_from("+1h", 1000) == 1000 + 3600

    def test_absolute_passthrough(self):
        assert _replay_from("2026-08-11", 1000) is not None


def _seed(conn, n=3):
    sid = dbmod.start_session(conn, "test", "demo")
    for i in range(n):
        dbmod.record_observation(
            conn,
            sid,
            Observation(
                address=f"aa:bb:cc:dd:ee:0{i}",
                name=f"Dev-{i}",
                rssi=-40 - i,
                seen_at=1_000_000 + i,
            ),
        )
    dbmod.end_session(conn, sid, n)
    return sid


class TestSessions:
    def test_empty(self, tmp_path):
        assert main(["sessions", "--db", str(tmp_path / "r.db")]) == EXIT_OK

    def test_lists_session(self, tmp_path):
        db = str(tmp_path / "r.db")
        conn = dbmod.connect(db)
        _seed(conn)
        conn.close()
        assert main(["sessions", "--db", db]) == EXIT_OK


class TestHistory:
    def test_shows_rows(self, tmp_path, capsys):
        db = str(tmp_path / "r.db")
        conn = dbmod.connect(db)
        _seed(conn)
        conn.close()
        assert main(["history", "--db", db]) == EXIT_OK
        out = capsys.readouterr().out
        assert "Dev-0" in out
        assert "aa:bb:cc:dd:ee:00" in out

    def test_filter_by_name(self, tmp_path, capsys):
        db = str(tmp_path / "r.db")
        conn = dbmod.connect(db)
        _seed(conn)
        conn.close()
        assert main(["history", "--db", db, "--device", "dev-1"]) == EXIT_OK
        out = capsys.readouterr().out
        assert "Dev-1" in out
        assert "Dev-0" not in out

    def test_no_match(self, tmp_path, capsys):
        db = str(tmp_path / "r.db")
        conn = dbmod.connect(db)
        _seed(conn)
        conn.close()
        assert main(["history", "--db", db, "--device", "zzz"]) == EXIT_OK
        assert "no observations match" in capsys.readouterr().out


class TestExport:
    def test_json_default(self, tmp_path, capsys):
        db = str(tmp_path / "r.db")
        conn = dbmod.connect(db)
        _seed(conn)
        conn.close()
        assert main(["export", "--db", db]) == EXIT_OK
        data = json.loads(capsys.readouterr().out)
        assert len(data) == 3

    def test_csv_to_file(self, tmp_path):
        db = str(tmp_path / "r.db")
        out = str(tmp_path / "out.csv")
        conn = dbmod.connect(db)
        _seed(conn)
        conn.close()
        assert main(["export", "--db", db, "--format", "csv", "--output", out]) == EXIT_OK
        with open(out) as fh:
            content = fh.read()
        assert content.startswith("observed_at,address,name,rssi,session_id")

    def test_sanitize(self, tmp_path, capsys):
        db = str(tmp_path / "r.db")
        conn = dbmod.connect(db)
        _seed(conn)
        conn.close()
        assert main(["export", "--db", db, "--sanitize"]) == EXIT_OK
        data = json.loads(capsys.readouterr().out)
        assert data[0]["address"] != "aa:bb:cc:dd:ee:00"

    def test_empty_export(self, tmp_path):
        db = str(tmp_path / "r.db")
        dbmod.connect(db).close()
        assert main(["export", "--db", db]) == EXIT_OK


class TestStatus:
    def test_empty_db(self, tmp_path, capsys):
        db = str(tmp_path / "r.db")
        dbmod.connect(db).close()
        assert main(["status", "--db", db]) == EXIT_OK
        out = capsys.readouterr().out
        assert "observations" in out
        assert "database" in out

    def test_seeded_db(self, tmp_path, capsys):
        db = str(tmp_path / "r.db")
        conn = dbmod.connect(db)
        _seed(conn)
        conn.close()
        main(["status", "--db", db])
        out = capsys.readouterr().out
        assert "sessions  : 1" in out
        assert "devices   : 3" in out
        assert "observations: 3" in out


class TestReplayErrors:
    def test_no_sessions(self, tmp_path, capsys):
        db = str(tmp_path / "r.db")
        dbmod.connect(db).close()
        assert main(["replay", "--db", db]) == EXIT_ERROR
        assert "no sessions" in capsys.readouterr().err

    def test_invalid_session_id(self, tmp_path, capsys):
        db = str(tmp_path / "r.db")
        dbmod.connect(db).close()
        assert main(["replay", "abc", "--db", db]) == EXIT_ERROR

    def test_empty_session(self, tmp_path, capsys):
        db = str(tmp_path / "r.db")
        conn = dbmod.connect(db)
        sid = dbmod.start_session(conn, "empty")
        dbmod.end_session(conn, sid, 0)
        conn.close()
        assert main(["replay", "latest", "--db", db]) == EXIT_ERROR
        assert "no observations" in capsys.readouterr().err


class TestErrors:
    def test_unknown_command(self, tmp_path):
        assert main(["frobnicate"]) == 2

    def test_invalid_time(self, tmp_path, capsys):
        db = str(tmp_path / "r.db")
        conn = dbmod.connect(db)
        _seed(conn)
        conn.close()
        assert main(["history", "--db", db, "--since", "nonsense"]) == EXIT_ERROR

    def test_help_runs(self, capsys):
        assert main(["--help"]) == 0
        assert "radar" in capsys.readouterr().out
