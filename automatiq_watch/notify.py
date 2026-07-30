"""Alert delivery. Log is always on; Slack and email are opt-in via config+env.

A channel that is enabled but missing its credentials prints a one-time warning
and is skipped — the tool keeps running and still records alerts to the log.
"""

from __future__ import annotations

import os
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText

from .config import Config
from .threshold import Alert


class Notifier:
    name = "base"

    def send(self, alert: Alert) -> bool:  # returns True on success
        raise NotImplementedError


class LogNotifier(Notifier):
    """Prints to the console and appends to <data_dir>/alerts.log."""

    name = "log"

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "alerts.log")

    def send(self, alert: Alert) -> bool:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        line = f"[{stamp}] ALERT ({alert.kind}) {alert.message}"
        print("🔔 " + line)
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass
        return True


class SlackNotifier(Notifier):
    name = "slack"

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send(self, alert: Alert) -> bool:
        import requests

        text = f":ticket: *Price alert* — {alert.message}"
        try:
            resp = requests.post(self.webhook_url, json={"text": text}, timeout=15)
            resp.raise_for_status()
            return True
        except requests.RequestException as exc:
            print(f"  ! Slack send failed: {exc}")
            return False


class EmailNotifier(Notifier):
    name = "email"

    def __init__(self, host: str, port: int, user: str, password: str, sender: str, recipient: str):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.sender = sender
        self.recipient = recipient

    def send(self, alert: Alert) -> bool:
        msg = MIMEText(alert.message)
        msg["Subject"] = f"Ticket price alert: {alert.event_name or alert.watch_name}"
        msg["From"] = self.sender
        msg["To"] = self.recipient
        try:
            with smtplib.SMTP(self.host, self.port, timeout=20) as server:
                server.starttls()
                if self.user:
                    server.login(self.user, self.password)
                server.sendmail(self.sender, [self.recipient], msg.as_string())
            return True
        except (smtplib.SMTPException, OSError) as exc:
            print(f"  ! Email send failed: {exc}")
            return False


def build_notifiers(cfg: Config) -> list[Notifier]:
    """Construct the notifier list from config + environment.

    The log channel is always included so there is always an audit trail and a
    visible alert, even before Slack/email are configured.
    """
    notifiers: list[Notifier] = [LogNotifier(cfg.data_dir)]
    channels = set(cfg.alerts.channels)

    if "slack" in channels:
        webhook = os.environ.get(cfg.alerts.slack_webhook_env, "").strip()
        if webhook:
            notifiers.append(SlackNotifier(webhook))
        else:
            print(f"  ! Slack enabled but {cfg.alerts.slack_webhook_env} is not set — skipping Slack.")

    if "email" in channels:
        e = cfg.alerts.email
        host = os.environ.get(e.smtp_host_env, "").strip()
        sender = os.environ.get(e.from_env, "").strip()
        recipient = os.environ.get(e.to_env, "").strip()
        if host and sender and recipient:
            notifiers.append(
                EmailNotifier(
                    host=host,
                    port=int(os.environ.get(e.smtp_port_env, "587") or 587),
                    user=os.environ.get(e.smtp_user_env, "").strip(),
                    password=os.environ.get(e.smtp_pass_env, ""),
                    sender=sender,
                    recipient=recipient,
                )
            )
        else:
            print(f"  ! Email enabled but SMTP env vars are incomplete — skipping email.")

    return notifiers


def notify_all(notifiers: list[Notifier], alert: Alert) -> None:
    for n in notifiers:
        n.send(alert)
