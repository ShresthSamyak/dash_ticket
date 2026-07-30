"""Normalized data shapes shared across the tool.

Everything downstream of the adapter speaks in these types, so the rest of the
code never has to know what Automatiq's raw JSON looks like.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Listing:
    """A single offer on the marketplace, normalized to a common shape."""

    price: float
    section: Optional[str] = None
    row: Optional[str] = None
    tier: Optional[str] = None
    quantity: Optional[int] = None
    marketplace: Optional[str] = None
    listing_id: Optional[str] = None
    # Original payload for this listing, kept for debugging. Not written to CSV.
    raw: Optional[dict] = field(default=None, repr=False)

    def label(self) -> str:
        parts = []
        if self.section:
            parts.append(f"Sec {self.section}")
        if self.row:
            parts.append(f"Row {self.row}")
        if self.tier:
            parts.append(self.tier)
        if self.quantity:
            parts.append(f"x{self.quantity}")
        return " ".join(parts) or "(listing)"


@dataclass
class PollResult:
    """The outcome of one poll of one watch: the matching listings + summary."""

    watch_name: str
    timestamp: str  # UTC ISO-8601, e.g. "2026-07-30T14:05:00+00:00"
    event_id: Optional[str] = None
    event_name: Optional[str] = None
    listings: list[Listing] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.listings)

    @property
    def lowest_price(self) -> Optional[float]:
        return min((l.price for l in self.listings), default=None)

    @property
    def average_price(self) -> Optional[float]:
        prices = [l.price for l in self.listings]
        return round(sum(prices) / len(prices), 2) if prices else None

    @property
    def lowest_listing(self) -> Optional[Listing]:
        return min(self.listings, key=lambda l: l.price, default=None)
