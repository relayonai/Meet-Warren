from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

import anthropic
from dotenv import load_dotenv


def _split_csv(value: str | None) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_schedules(value: str | None) -> dict:
    """Parse 'Source Name=frequency,...' into a dict.
    Frequency must be one of: daily, weekly, monthly.
    """
    if not value:
        return {}
    result = {}
    for item in value.split(","):
        if "=" in item:
            key, freq = item.split("=", 1)
            result[key.strip()] = freq.strip().lower()
    return result


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str
    anthropic_auth_token: str
    anthropic_model: str
    rss_feeds: List[str]
    http_sources: List[str]
    govuk_orgs: List[str]
    source_schedules: dict
    min_relevance_score: int
    db_path: str
    output_dir: str
    enable_local: bool
    enable_gmail: bool
    enable_notion: bool
    gmail_user: str
    gmail_app_password: str
    gmail_to: str
    notion_token: str
    notion_database_id: str


def load_config() -> Config:
    load_dotenv(override=True)

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    auth_token = (
        os.getenv("ANTHROPIC_AUTH_TOKEN", "").strip()
        or os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    )
    if not api_key and not auth_token:
        raise RuntimeError(
            "Set ANTHROPIC_API_KEY (from console.anthropic.com) "
            "or ANTHROPIC_AUTH_TOKEN (an OAuth bearer token) in .env."
        )

    return Config(
        anthropic_api_key=api_key,
        anthropic_auth_token=auth_token,
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514").strip(),
        rss_feeds=_split_csv(os.getenv("RSS_FEEDS")),
        http_sources=_split_csv(os.getenv("HTTP_SOURCES")),
        govuk_orgs=_split_csv(os.getenv("GOVUK_ORGS")),
        source_schedules=_parse_schedules(os.getenv("SOURCE_SCHEDULES")),
        min_relevance_score=int(os.getenv("MIN_RELEVANCE_SCORE", "6")),
        db_path=os.getenv("DB_PATH", "./data/articles.db").strip(),
        output_dir=os.getenv("OUTPUT_DIR", "./output").strip(),
        enable_local=_bool(os.getenv("ENABLE_LOCAL"), default=True),
        enable_gmail=_bool(os.getenv("ENABLE_GMAIL"), default=False),
        enable_notion=_bool(os.getenv("ENABLE_NOTION"), default=False),
        gmail_user=os.getenv("GMAIL_USER", "").strip(),
        gmail_app_password=os.getenv("GMAIL_APP_PASSWORD", "").strip(),
        gmail_to=os.getenv("GMAIL_TO", "").strip(),
        notion_token=os.getenv("NOTION_TOKEN", "").strip(),
        notion_database_id=os.getenv("NOTION_DATABASE_ID", "").strip(),
    )


def build_anthropic_client(cfg: "Config") -> anthropic.Anthropic:
    """Instantiate an Anthropic client, preferring API key but falling back to OAuth bearer token."""
    if cfg.anthropic_api_key:
        return anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    return anthropic.Anthropic(auth_token=cfg.anthropic_auth_token)
