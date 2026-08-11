"""Tests for util helpers: hashing, pseudonyms, time parsing, smoothing."""

from __future__ import annotations

import pytest

from ble_radar.util import (
    ema,
    fmt_duration,
    hash_address,
    normalize_address,
    parse_time,
    pseudonym,
    stable_angle,
)


class TestNormalize:
    def test_lowercases(self):
        assert normalize_address("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"

    def test_accepts_valid(self):
        assert normalize_address("00:11:22:33:44:55") == "00:11:22:33:44:55"

    @pytest.mark.parametrize(
        "bad",
        ["", "aa:bb:cc:dd:ee", "aa:bb:cc:dd:ee:ff:00", "aabbccddeeff", "ZZ:11:22:33:44:55"],
    )
    def test_rejects_invalid(self, bad):
        with pytest.raises(ValueError):
            normalize_address(bad)


class TestHashing:
    def test_deterministic(self):
        a = "AA:BB:CC:DD:EE:01"
        assert hash_address(a) == hash_address(a)

    def test_12_hex_chars(self):
        h = hash_address("aa:bb:cc:dd:ee:ff")
        assert len(h) == 12
        int(h, 16)  # no raise

    def test_normalizes_before_hashing(self):
        assert hash_address("AA:BB:CC:DD:EE:01") == hash_address("aa:bb:cc:dd:ee:01")

    def test_distinct_addresses(self):
        assert hash_address("aa:bb:cc:dd:ee:01") != hash_address("aa:bb:cc:dd:ee:02")

    def test_pseudonym_prefix(self):
        p = pseudonym("aa:bb:cc:dd:ee:01")
        assert p.startswith("D-")
        assert len(p) == 6

    def test_pseudonym_matches_hash_suffix(self):
        addr = "aa:bb:cc:dd:ee:01"
        assert pseudonym(addr) == f"D-{hash_address(addr)[-4:]}"


class TestAngle:
    def test_in_range(self):
        for addr in ["aa:bb:cc:dd:ee:01", "00:11:22:33:44:55", "ff:ee:dd:cc:bb:aa"]:
            assert 0.0 <= stable_angle(addr) < 360.0

    def test_stable(self):
        assert stable_angle("aa:bb:cc:dd:ee:01") == stable_angle("aa:bb:cc:dd:ee:01")


class TestEma:
    def test_first_value(self):
        assert ema(None, 5.0) == 5.0

    def test_mix(self):
        assert ema(10.0, 0.0, alpha=0.5) == 5.0

    def test_alpha_one(self):
        assert ema(10.0, 2.0, alpha=1.0) == 2.0


class TestTimeParsing:
    def test_offsets(self, clock):
        assert parse_time("1h", now=1_000_000) == 1_000_000 - 3600
        assert parse_time("30m", now=1_000_000) == 1_000_000 - 1800
        assert parse_time("2d", now=1_000_000) == 1_000_000 - 172800
        assert parse_time("90s", now=1_000_000) == 1_000_000 - 90

    def test_none_and_empty(self):
        assert parse_time(None) is None
        assert parse_time("  ") is None

    def test_invalid(self):
        with pytest.raises(ValueError):
            parse_time("banana")

    def test_absolute(self):
        t = parse_time("2026-08-11")
        assert t is not None
        assert t % 86400 == 0  # midnight


class TestFmtDuration:
    def test_seconds(self):
        assert fmt_duration(42) == "42s"

    def test_minutes(self):
        assert fmt_duration(245) == "4m05s"

    def test_hours(self):
        assert fmt_duration(3 * 3600 + 720) == "3h12m"

    def test_zero(self):
        assert fmt_duration(0) == "0s"

    def test_negative_clamped(self):
        assert fmt_duration(-5) == "0s"
