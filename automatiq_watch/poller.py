"""Orchestration: run one poll cycle, or loop on an interval.

Alerting is edge-triggered with a cooldown so you hear about a *crossing* once,
not on every poll while the price sits below your target:
  * absolute  — fires when price first crosses at/below the target; goes quiet
                until it recovers above the target (or the cooldown re-reminds).
  * relative  — fires on a fresh drop vs the previous check, rate-limited by the
                cooldown.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from . import history, threshold
from .adapter import AdapterError, fetch_prices
from .config import Config
from .notify import Notifier, build_notifiers, notify_all


def run_once(cfg: Config, notifiers: Optional[list[Notifier]] = None) -> None:
    """Poll every watch once: record history and send any due alerts."""
    if notifiers is None:
        notifiers = build_notifiers(cfg)

    state = history.load_state(cfg.data_dir)
    now = datetime.now(timezone.utc)

    for watch in cfg.watches:
        st = history.watch_state(state, watch.name)
        prev_lowest = st.get("last_lowest")

        try:
            result = fetch_prices(cfg, watch)
        except AdapterError as exc:
            print(f"  ! {watch.name}: fetch failed — {exc}")
            continue

        history.record(result, cfg.data_dir)

        low = result.lowest_price
        avg = result.average_price
        low_str = f"${low:,.2f}" if low is not None else "—"
        avg_str = f"${avg:,.2f}" if avg is not None else "—"
        print(f"  · {watch.name}: {result.count} listings | lowest {low_str} | avg {avg_str}")

        alerts = threshold.evaluate(watch, result, prev_lowest)
        _dispatch(alerts, watch.cooldown_seconds, st, now, notifiers)

        if low is not None:
            st["last_lowest"] = low
            st["last_average"] = avg
        st["last_poll_ts"] = now.isoformat(timespec="seconds")

    history.save_state(cfg.data_dir, state)


def watch_loop(cfg: Config) -> None:
    """Poll forever on cfg.poll_interval_seconds. Stop with Ctrl+C."""
    notifiers = build_notifiers(cfg)
    interval = cfg.poll_interval_seconds
    print(
        f"Watching {len(cfg.watches)} target(s) every {interval}s "
        f"(source={cfg.source}). Press Ctrl+C to stop."
    )
    while True:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        print(f"[{stamp}] polling…")
        run_once(cfg, notifiers)
        time.sleep(interval)


# ── edge-trigger + cooldown ──────────────────────────────────────────────────


def _dispatch(alerts, cooldown_seconds, st, now, notifiers) -> None:
    triggered = {a.kind: a for a in alerts}
    alert_state = st.setdefault("alerts", {})

    for kind in ("absolute", "relative"):
        bucket = alert_state.setdefault(kind, {"active": False, "last_alert_ts": None})
        if kind in triggered:
            if _should_fire(kind, bucket, now, cooldown_seconds):
                notify_all(notifiers, triggered[kind])
                bucket["last_alert_ts"] = now.isoformat(timespec="seconds")
            bucket["active"] = True
        elif kind == "absolute":
            # Price is back above target — reset so the next crossing alerts again.
            bucket["active"] = False


def _should_fire(kind: str, bucket: dict, now: datetime, cooldown_seconds: int) -> bool:
    if kind == "absolute" and not bucket.get("active"):
        return True  # fresh crossing into the target — always alert
    return _cooldown_elapsed(bucket.get("last_alert_ts"), now, cooldown_seconds)


def _cooldown_elapsed(last_iso: Optional[str], now: datetime, cooldown_seconds: int) -> bool:
    if not last_iso:
        return True
    try:
        last = datetime.fromisoformat(last_iso)
    except ValueError:
        return True
    return (now - last).total_seconds() >= cooldown_seconds
