"""Load and validate config.yaml + .env into typed objects.

Kept deliberately strict-but-friendly: a bad config should fail fast with a
message a non-developer can act on, not a stack trace deep inside a poll loop.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

try:
    import yaml
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit(
        "Missing dependency 'PyYAML'. Install requirements first:\n"
        "    pip install -r requirements.txt"
    ) from exc


class ConfigError(Exception):
    """Raised when config.yaml is missing required fields or malformed."""


# ── Typed config tree ────────────────────────────────────────────────────────


@dataclass
class Filters:
    sections: list[str] = field(default_factory=list)
    rows: list[str] = field(default_factory=list)
    tiers: list[str] = field(default_factory=list)
    max_quantity: Optional[int] = None


@dataclass
class Thresholds:
    absolute_below: Optional[float] = None
    relative_drop_pct: Optional[float] = None


@dataclass
class Watch:
    name: str
    event_id: Optional[str] = None
    event_name: Optional[str] = None
    filters: Filters = field(default_factory=Filters)
    thresholds: Thresholds = field(default_factory=Thresholds)
    cooldown_seconds: int = 3600


@dataclass
class AutomatiqConfig:
    base_url: str = "https://app.sync.automatiq.com"
    listings_endpoint: str = ""
    auth_scheme: str = "bearer"  # bearer | token | header | query
    auth_header: str = "Authorization"
    token_env: str = "AUTOMATIQ_API_TOKEN"


@dataclass
class EmailConfig:
    smtp_host_env: str = "SMTP_HOST"
    smtp_port_env: str = "SMTP_PORT"
    smtp_user_env: str = "SMTP_USERNAME"
    smtp_pass_env: str = "SMTP_PASSWORD"
    from_env: str = "ALERT_EMAIL_FROM"
    to_env: str = "ALERT_EMAIL_TO"


@dataclass
class AlertsConfig:
    channels: list[str] = field(default_factory=lambda: ["log"])
    slack_webhook_env: str = "SLACK_WEBHOOK_URL"
    email: EmailConfig = field(default_factory=EmailConfig)


@dataclass
class Config:
    source: str = "mock"
    poll_interval_seconds: int = 300
    data_dir: str = "data"
    automatiq: AutomatiqConfig = field(default_factory=AutomatiqConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    watches: list[Watch] = field(default_factory=list)


# ── .env loader (no third-party dependency) ──────────────────────────────────


def load_env(path: str = ".env") -> None:
    """Load KEY=VALUE lines from a .env file into os.environ.

    Existing environment variables win, so anything already exported is not
    overwritten. Missing file is fine — env vars may be set another way.
    """
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


# ── config.yaml loader ───────────────────────────────────────────────────────


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _parse_watch(raw: object, index: int) -> Watch:
    if not isinstance(raw, dict):
        raise ConfigError(f"watches[{index}] must be a mapping, got {type(raw).__name__}")
    name = raw.get("name")
    if not name:
        raise ConfigError(f"watches[{index}] is missing a 'name'")

    f = raw.get("filters") or {}
    t = raw.get("thresholds") or {}
    return Watch(
        name=str(name),
        event_id=(str(raw["event_id"]) if raw.get("event_id") is not None else None),
        event_name=raw.get("event_name"),
        filters=Filters(
            sections=[str(s) for s in _as_list(f.get("sections"))],
            rows=[str(r) for r in _as_list(f.get("rows"))],
            tiers=[str(x) for x in _as_list(f.get("tiers"))],
            max_quantity=f.get("max_quantity"),
        ),
        thresholds=Thresholds(
            absolute_below=t.get("absolute_below"),
            relative_drop_pct=t.get("relative_drop_pct"),
        ),
        cooldown_seconds=int(raw.get("cooldown_seconds", 3600)),
    )


def load_config(path: str = "config.yaml") -> Config:
    if not os.path.exists(path):
        raise ConfigError(
            f"Config file not found: {path}\n"
            "Copy config.example.yaml to config.yaml and edit it."
        )
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError("Top level of config.yaml must be a mapping.")

    aq = data.get("automatiq") or {}
    al = data.get("alerts") or {}
    email = al.get("email") or {}

    watches_raw = data.get("watches") or []
    if not watches_raw:
        raise ConfigError("config.yaml has no 'watches' — add at least one event to watch.")

    cfg = Config(
        source=str(data.get("source", "mock")).lower(),
        poll_interval_seconds=int(data.get("poll_interval_seconds", 300)),
        data_dir=str(data.get("data_dir", "data")),
        automatiq=AutomatiqConfig(
            base_url=aq.get("base_url", "https://app.sync.automatiq.com").rstrip("/"),
            listings_endpoint=aq.get("listings_endpoint", ""),
            auth_scheme=str(aq.get("auth_scheme", "bearer")).lower(),
            auth_header=aq.get("auth_header", "Authorization"),
            token_env=aq.get("token_env", "AUTOMATIQ_API_TOKEN"),
        ),
        alerts=AlertsConfig(
            channels=[str(c).lower() for c in _as_list(al.get("channels")) or ["log"]],
            slack_webhook_env=al.get("slack_webhook_env", "SLACK_WEBHOOK_URL"),
            email=EmailConfig(
                smtp_host_env=email.get("smtp_host_env", "SMTP_HOST"),
                smtp_port_env=email.get("smtp_port_env", "SMTP_PORT"),
                smtp_user_env=email.get("smtp_user_env", "SMTP_USERNAME"),
                smtp_pass_env=email.get("smtp_pass_env", "SMTP_PASSWORD"),
                from_env=email.get("from_env", "ALERT_EMAIL_FROM"),
                to_env=email.get("to_env", "ALERT_EMAIL_TO"),
            ),
        ),
        watches=[_parse_watch(w, i) for i, w in enumerate(watches_raw)],
    )

    if cfg.source not in ("mock", "automatiq"):
        raise ConfigError(f"source must be 'mock' or 'automatiq', got '{cfg.source}'")
    if cfg.poll_interval_seconds < 1:
        raise ConfigError("poll_interval_seconds must be >= 1")
    return cfg
