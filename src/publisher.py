from __future__ import annotations

import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List

from .config import Config

log = logging.getLogger(__name__)


def _save_local(html: str, text: str, subject: str, output_dir: str) -> List[str]:
    os.makedirs(output_dir, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    base = os.path.join(output_dir, f"newsletter-{stamp}")
    html_path = base + ".html"
    text_path = base + ".txt"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    with open(text_path, "w", encoding="utf-8") as f:
        f.write(text)
    return [html_path, text_path]


def _send_gmail(html: str, text: str, subject: str, cfg: Config) -> None:
    if not (cfg.gmail_user and cfg.gmail_app_password and cfg.gmail_to):
        raise RuntimeError("Gmail enabled but GMAIL_USER / GMAIL_APP_PASSWORD / GMAIL_TO not all set.")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg.gmail_user
    msg["To"] = cfg.gmail_to
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(cfg.gmail_user, cfg.gmail_app_password)
        smtp.send_message(msg)


def _save_notion(text: str, subject: str, cfg: Config) -> None:
    if not (cfg.notion_token and cfg.notion_database_id):
        raise RuntimeError("Notion enabled but NOTION_TOKEN / NOTION_DATABASE_ID not set.")
    from notion_client import Client  # local import to keep startup cheap

    notion = Client(auth=cfg.notion_token)
    blocks = []
    for paragraph in text.split("\n\n"):
        chunk = paragraph.strip()
        if not chunk:
            continue
        blocks.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": chunk[:1900]}}]
                },
            }
        )
    notion.pages.create(
        parent={"database_id": cfg.notion_database_id},
        properties={"title": {"title": [{"text": {"content": subject[:200]}}]}},
        children=blocks[:100],
    )


def publish(html: str, text: str, subject: str, cfg: Config) -> List[str]:
    """Dispatch to all enabled outputs. Returns a list of human-readable result lines."""
    results: List[str] = []

    if cfg.enable_local:
        try:
            paths = _save_local(html, text, subject, cfg.output_dir)
            results.append(f"local: wrote {', '.join(paths)}")
        except Exception as exc:
            results.append(f"local: FAILED ({exc})")
            log.exception("Local save failed")

    if cfg.enable_gmail:
        try:
            _send_gmail(html, text, subject, cfg)
            results.append(f"gmail: sent to {cfg.gmail_to}")
        except Exception as exc:
            results.append(f"gmail: FAILED ({exc})")
            log.exception("Gmail send failed")

    if cfg.enable_notion:
        try:
            _save_notion(text, subject, cfg)
            results.append(f"notion: page created in {cfg.notion_database_id}")
        except Exception as exc:
            results.append(f"notion: FAILED ({exc})")
            log.exception("Notion save failed")

    if not results:
        results.append("no outputs enabled — set ENABLE_LOCAL/ENABLE_GMAIL/ENABLE_NOTION in .env")
    return results
