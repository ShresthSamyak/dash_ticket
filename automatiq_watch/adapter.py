"""The Automatiq adapter — the ONLY place that knows the Automatiq API.

fetch_prices() returns a normalized PollResult; nothing downstream cares whether
the data came from the live API or the mock. When we confirm the real endpoint
and response shape (via live browser inspection or a sample response), the change
lives entirely inside `_automatiq_fetch` / `_normalize_automatiq` below.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone

from .config import AutomatiqConfig, Config, Filters, Watch
from .models import Listing, PollResult


class AdapterError(Exception):
    """Raised when the live API cannot be reached or its response cannot be mapped."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Public entrypoint ────────────────────────────────────────────────────────


def fetch_prices(cfg: Config, watch: Watch) -> PollResult:
    """Fetch current listings for one watch and return them normalized + filtered."""
    if cfg.source == "mock":
        listings = _mock_listings(watch)
    elif cfg.source == "automatiq":
        listings = _automatiq_fetch(cfg.automatiq, watch)
    else:  # pragma: no cover - config validation should prevent this
        raise AdapterError(f"Unknown source: {cfg.source!r}")

    listings = _apply_filters(listings, watch.filters)
    return PollResult(
        watch_name=watch.name,
        timestamp=_now_iso(),
        event_id=watch.event_id,
        event_name=watch.event_name,
        listings=listings,
    )


# ── Shared filtering (applied to mock and live results alike) ────────────────


def _apply_filters(listings: list[Listing], f: Filters) -> list[Listing]:
    def keep(l: Listing) -> bool:
        if f.sections and (l.section or "") not in f.sections:
            return False
        if f.rows and (l.row or "") not in f.rows:
            return False
        if f.tiers and (l.tier or "") not in f.tiers:
            return False
        if f.max_quantity is not None and l.quantity is not None and l.quantity < f.max_quantity:
            return False
        return True

    return [l for l in listings if keep(l)]


# ── Mock source (no token, runs anywhere; used for testing the pipeline) ─────


def _mock_listings(watch: Watch) -> list[Listing]:
    """Generate plausible listings that move between polls.

    Prices wander in the ~$120–260 range so the example thresholds
    (absolute_below: 150, relative_drop_pct: 10) actually fire sometimes.
    """
    sections = watch.filters.sections or ["104", "105", "212", "301"]
    listings: list[Listing] = []
    for section in sections:
        base = 130 + (hash(section) % 90)  # stable-ish base per section
        for i in range(random.randint(2, 4)):
            price = round(base + random.uniform(-25, 60), 2)
            listings.append(
                Listing(
                    price=price,
                    section=str(section),
                    row=random.choice(["A", "B", "C", "12", "20"]),
                    tier=None,
                    quantity=random.choice([1, 2, 2, 4]),
                    marketplace="mock",
                    listing_id=f"mock-{section}-{i}",
                )
            )
    return listings


# ── Live Automatiq source ────────────────────────────────────────────────────


def _auth(session_headers: dict, params: dict, aq: AutomatiqConfig, token: str) -> None:
    """Attach the token to the outgoing request per the configured scheme."""
    scheme = aq.auth_scheme
    if scheme == "bearer":
        session_headers[aq.auth_header] = f"Bearer {token}"
    elif scheme == "token":
        session_headers[aq.auth_header] = f"Token {token}"
    elif scheme == "header":
        session_headers[aq.auth_header] = token
    elif scheme == "query":
        params[aq.auth_header] = token
    else:
        raise AdapterError(f"Unknown auth_scheme: {scheme!r}")


def _automatiq_fetch(aq: AutomatiqConfig, watch: Watch) -> list[Listing]:
    import os

    import requests  # imported lazily so mock mode has zero hard dependency on it

    token = os.environ.get(aq.token_env, "").strip()
    if not token:
        raise AdapterError(
            f"No API token found. Set {aq.token_env} in your .env "
            "(Automatiq -> Company Settings -> API)."
        )
    if "PLACEHOLDER" in aq.listings_endpoint or not aq.listings_endpoint:
        raise AdapterError(
            "The Automatiq endpoint is not configured yet. Confirm the real "
            "listings endpoint + response shape (live inspection or a sample "
            "response), then set automatiq.listings_endpoint in config.yaml and "
            "complete the field mapping in _normalize_automatiq()."
        )

    url = aq.base_url + aq.listings_endpoint.format(event_id=watch.event_id or "")
    headers = {"Accept": "application/json"}
    # ── TODO(confirm at setup): real query params for event / section filtering ──
    params: dict = {}
    if watch.event_id:
        params["event_id"] = watch.event_id

    _auth(headers, params, aq, token)

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        raise AdapterError(f"Automatiq request failed: {exc}") from exc
    except ValueError as exc:
        raise AdapterError(f"Automatiq returned non-JSON response: {exc}") from exc

    return _normalize_automatiq(payload)


def _normalize_automatiq(payload) -> list[Listing]:
    """Map Automatiq's raw JSON into Listing objects.

    ── TODO(confirm at setup) ───────────────────────────────────────────────
    The exact response shape is confirmed against the live API. Until then this
    does a best-effort, defensive mapping that tries the most common field names.
    Once the real shape is known, replace the guesswork below with the exact
    paths — that is the entire "wire up the real endpoint" step.
    """
    rows = _find_listing_array(payload)
    if rows is None:
        raise AdapterError(
            "Could not locate a listings array in the Automatiq response. "
            "Update _normalize_automatiq() with the real response shape."
        )

    listings: list[Listing] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        price = _first_number(item, ("price", "unit_price", "amount", "lowest_price", "list_price"))
        if price is None:
            continue
        listings.append(
            Listing(
                price=float(price),
                section=_first_str(item, ("section", "section_name", "sec")),
                row=_first_str(item, ("row", "row_name")),
                tier=_first_str(item, ("tier", "zone", "level")),
                quantity=_first_int(item, ("quantity", "qty", "available_quantity")),
                marketplace=_first_str(item, ("marketplace", "exchange", "source")),
                listing_id=_first_str(item, ("id", "listing_id", "uuid")),
                raw=item,
            )
        )
    return listings


# ── Small tolerant extractors used by the defensive mapper ───────────────────


def _find_listing_array(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("listings", "data", "results", "tickets", "items", "inventory"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return None


def _first_number(item: dict, keys):
    for k in keys:
        v = item.get(k)
        if isinstance(v, (int, float)):
            return v
        if isinstance(v, str):
            try:
                return float(v.replace("$", "").replace(",", ""))
            except ValueError:
                continue
    return None


def _first_int(item: dict, keys):
    v = _first_number(item, keys)
    return int(v) if v is not None else None


def _first_str(item: dict, keys):
    for k in keys:
        v = item.get(k)
        if v not in (None, ""):
            return str(v)
    return None
