"""Persistence: append-only price history + a small state file.

Two logs so you get both views:
  * price_history.csv    — one summary row per poll (open in Excel / Sheets).
  * price_history.jsonl  — the full listing detail per poll (one JSON per line).

state.json holds the last-seen lowest price (the baseline for % drops) and
per-alert cooldown timestamps, so restarting the tool doesn't lose context.
"""

from __future__ import annotations

import csv
import json
import os
from typing import Any

from .models import PollResult

CSV_NAME = "price_history.csv"
JSONL_NAME = "price_history.jsonl"
STATE_NAME = "state.json"

CSV_COLUMNS = [
    "timestamp",
    "watch_name",
    "event_id",
    "event_name",
    "count",
    "lowest_price",
    "average_price",
    "lowest_section",
    "lowest_row",
    "lowest_qty",
]


def ensure_dir(data_dir: str) -> None:
    os.makedirs(data_dir, exist_ok=True)


def record(result: PollResult, data_dir: str) -> None:
    """Append one summary row (CSV) and one detail line (JSONL) for this poll."""
    ensure_dir(data_dir)
    _append_csv(result, os.path.join(data_dir, CSV_NAME))
    _append_jsonl(result, os.path.join(data_dir, JSONL_NAME))


def _append_csv(result: PollResult, path: str) -> None:
    low = result.lowest_listing
    write_header = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(
            {
                "timestamp": result.timestamp,
                "watch_name": result.watch_name,
                "event_id": result.event_id or "",
                "event_name": result.event_name or "",
                "count": result.count,
                "lowest_price": result.lowest_price if result.lowest_price is not None else "",
                "average_price": result.average_price if result.average_price is not None else "",
                "lowest_section": (low.section or "") if low else "",
                "lowest_row": (low.row or "") if low else "",
                "lowest_qty": (low.quantity if low and low.quantity is not None else ""),
            }
        )


def _append_jsonl(result: PollResult, path: str) -> None:
    payload = {
        "timestamp": result.timestamp,
        "watch_name": result.watch_name,
        "event_id": result.event_id,
        "event_name": result.event_name,
        "count": result.count,
        "lowest_price": result.lowest_price,
        "average_price": result.average_price,
        "listings": [
            {
                "price": l.price,
                "section": l.section,
                "row": l.row,
                "tier": l.tier,
                "quantity": l.quantity,
                "marketplace": l.marketplace,
                "listing_id": l.listing_id,
            }
            for l in result.listings
        ],
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")


# ── state.json ───────────────────────────────────────────────────────────────


def load_state(data_dir: str) -> dict[str, Any]:
    path = os.path.join(data_dir, STATE_NAME)
    if not os.path.exists(path):
        return {"watches": {}}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("watches"), dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"watches": {}}


def save_state(data_dir: str, state: dict[str, Any]) -> None:
    ensure_dir(data_dir)
    path = os.path.join(data_dir, STATE_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, path)  # atomic on Windows + POSIX


def watch_state(state: dict[str, Any], watch_name: str) -> dict[str, Any]:
    """Return (creating if needed) the mutable state bucket for one watch."""
    return state.setdefault("watches", {}).setdefault(
        watch_name,
        {"last_lowest": None, "last_average": None, "last_poll_ts": None, "alerts": {}},
    )
