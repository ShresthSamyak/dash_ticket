"""Threshold evaluation — turns a poll result into zero or more alerts.

Pure functions only: no I/O, no state mutation. Edge-triggering and cooldown
(deciding whether an alert should actually be *sent*) live in the poller, so
this module is trivial to reason about and test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .config import Watch
from .models import Listing, PollResult


@dataclass
class Alert:
    watch_name: str
    kind: str  # "absolute" | "relative"
    lowest_price: float
    reference_price: Optional[float]
    listing: Optional[Listing]
    event_name: Optional[str]
    message: str


def evaluate(watch: Watch, result: PollResult, prev_lowest: Optional[float]) -> list[Alert]:
    """Return the alerts this poll triggers (before cooldown/edge filtering)."""
    alerts: list[Alert] = []
    low = result.lowest_price
    if low is None:
        return alerts  # nothing listed / all filtered out

    th = watch.thresholds
    label = result.event_name or watch.name
    low_listing = result.lowest_listing
    where = f" ({low_listing.label()})" if low_listing else ""

    if th.absolute_below is not None and low <= th.absolute_below:
        alerts.append(
            Alert(
                watch_name=watch.name,
                kind="absolute",
                lowest_price=low,
                reference_price=th.absolute_below,
                listing=low_listing,
                event_name=result.event_name,
                message=(
                    f"{label}: lowest price ${low:,.2f}{where} is at/below your "
                    f"${th.absolute_below:,.2f} target."
                ),
            )
        )

    if th.relative_drop_pct is not None and prev_lowest:
        drop_pct = (prev_lowest - low) / prev_lowest * 100
        if drop_pct >= th.relative_drop_pct:
            alerts.append(
                Alert(
                    watch_name=watch.name,
                    kind="relative",
                    lowest_price=low,
                    reference_price=prev_lowest,
                    listing=low_listing,
                    event_name=result.event_name,
                    message=(
                        f"{label}: lowest price dropped {drop_pct:.1f}% "
                        f"(${prev_lowest:,.2f} -> ${low:,.2f}){where}."
                    ),
                )
            )

    return alerts
