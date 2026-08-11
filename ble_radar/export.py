"""Dataset export with optional privacy sanitization (address hashing)."""

from __future__ import annotations

import csv
import io
import json

from ble_radar.util import hash_address, normalize_address

SANITIZE_WARNING = (
    "warning: exporting raw BLE addresses; consider --sanitize to hash them"
)


def observations_to_records(rows) -> list[dict]:
    records = []
    for row in rows:
        records.append(
            {
                "observed_at": row["observed_at"],
                "address": row["address"],
                "name": row["name"] or "",
                "rssi": row["rssi"],
                "session_id": row["session_id"],
            }
        )
    return records


def to_json(rows, sanitize: bool = False) -> str:
    records = observations_to_records(rows)
    if sanitize:
        for rec in records:
            rec["address"] = hash_address(normalize_address(rec["address"]))
    return json.dumps(records, indent=2, ensure_ascii=False)


def to_csv(rows, sanitize: bool = False) -> str:
    records = observations_to_records(rows)
    buf = io.StringIO()
    if not records:
        return "observed_at,address,name,rssi,session_id\n"
    writer = csv.DictWriter(buf, fieldnames=list(records[0].keys()))
    writer.writeheader()
    for rec in records:
        if sanitize:
            rec["address"] = hash_address(normalize_address(rec["address"]))
        writer.writerow(rec)
    return buf.getvalue()
