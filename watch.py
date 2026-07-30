#!/usr/bin/env python3
"""Automatiq price watcher — command-line entrypoint.

Common uses:
    python watch.py --print          # fetch once and show prices (no logging, no alerts)
    python watch.py --once           # one poll: record history + send any alerts
    python watch.py --watch          # poll continuously on the configured interval
    python watch.py --test-alert     # send a test alert through your configured channels

Handy overrides:
    python watch.py --watch --interval 120     # poll every 120s
    python watch.py --print --source mock      # try it without a real API token
"""

from __future__ import annotations

import argparse
import sys

from automatiq_watch.adapter import AdapterError, fetch_prices
from automatiq_watch.config import ConfigError, load_config, load_env
from automatiq_watch.notify import build_notifiers, notify_all
from automatiq_watch.poller import run_once, watch_loop
from automatiq_watch.threshold import Alert


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="watch.py",
        description="Watch Automatiq ticket prices and alert on your thresholds.",
    )
    p.add_argument("--config", default="config.yaml", help="Path to config file (default: config.yaml)")
    p.add_argument("--env", default=".env", help="Path to .env file (default: .env)")

    action = p.add_mutually_exclusive_group()
    action.add_argument("--once", action="store_true", help="Run a single poll (default)")
    action.add_argument("--watch", action="store_true", help="Poll continuously on the interval")
    action.add_argument("--print", dest="print_only", action="store_true",
                        help="Fetch once and print prices; no history, no alerts")
    action.add_argument("--test-alert", action="store_true",
                        help="Send a test alert through configured channels and exit")

    p.add_argument("--interval", type=int, default=None,
                   help="Override poll interval (seconds) for --watch")
    p.add_argument("--source", choices=["mock", "automatiq"], default=None,
                   help="Override the data source from config")
    return p


def _money(v) -> str:
    return f"${v:,.2f}" if v is not None else "—"


def _do_print(cfg) -> None:
    for watch in cfg.watches:
        print(f"\n=== {watch.name} ({watch.event_name or 'event ' + (watch.event_id or '?')}) ===")
        try:
            result = fetch_prices(cfg, watch)
        except AdapterError as exc:
            print(f"  ! fetch failed — {exc}")
            continue
        print(f"  listings: {result.count}")
        print(f"  lowest:   {_money(result.lowest_price)}")
        print(f"  average:  {_money(result.average_price)}")
        for l in sorted(result.listings, key=lambda x: x.price)[:5]:
            print(f"    - ${l.price:,.2f}  {l.label()}")


def _do_test_alert(cfg) -> None:
    notifiers = build_notifiers(cfg)
    demo = Alert(
        watch_name="TEST",
        kind="absolute",
        lowest_price=99.0,
        reference_price=100.0,
        listing=None,
        event_name="Test event",
        message="This is a test alert from the Automatiq price watcher. Channels are working.",
    )
    print(f"Sending a test alert through {len(notifiers)} channel(s)…")
    notify_all(notifiers, demo)
    print("Done.")


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    load_env(args.env)
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if args.source:
        cfg.source = args.source
    if args.interval is not None:
        cfg.poll_interval_seconds = args.interval

    if args.test_alert:
        _do_test_alert(cfg)
        return 0
    if args.print_only:
        _do_print(cfg)
        return 0
    if args.watch:
        try:
            watch_loop(cfg)
        except KeyboardInterrupt:
            print("\nStopped.")
        return 0

    # default action: a single poll
    run_once(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
