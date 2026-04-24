from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    published_at TEXT,
    raw_content TEXT,
    summary TEXT,
    category TEXT,
    relevance_score INTEGER,
    scrape_frequency TEXT DEFAULT 'daily',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_published ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_category  ON articles(category);

CREATE TABLE IF NOT EXISTS source_log (
    source_key  TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    frequency   TEXT NOT NULL DEFAULT 'daily',
    last_scraped_at TEXT
);
"""

# Applied in order; each is silently skipped if the change already exists.
_MIGRATIONS = [
    "ALTER TABLE articles ADD COLUMN scrape_frequency TEXT DEFAULT 'daily'",
    "CREATE INDEX IF NOT EXISTS idx_frequency ON articles(scrape_frequency)",
    "ALTER TABLE articles ADD COLUMN compliance_grade TEXT",
    "ALTER TABLE articles ADD COLUMN compliance_notes TEXT",
    "CREATE INDEX IF NOT EXISTS idx_compliance_grade ON articles(compliance_grade)",
]

FREQ_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}


def get_connection(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
            conn.commit()
        except sqlite3.OperationalError:
            pass


def existing_urls(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT url FROM articles").fetchall()
    return {row["url"] for row in rows}


def insert_article(
    conn: sqlite3.Connection,
    article,
    summary: dict,
    frequency: str = "daily",
) -> bool:
    compliance_grade = summary.get("compliance_grade")
    compliance_notes = summary.get("compliance_notes")
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO articles
            (id, url, title, source, published_at, raw_content,
             summary, category, relevance_score, scrape_frequency,
             compliance_grade, compliance_notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            article.id,
            article.url,
            summary.get("title") or article.title,
            article.source,
            article.published_at,
            article.raw_content,
            json.dumps(summary, ensure_ascii=False),
            summary.get("category"),
            int(summary.get("relevance_score") or 0),
            frequency,
            compliance_grade,
            json.dumps(compliance_notes or [], ensure_ascii=False) if compliance_notes else None,
        ),
    )
    conn.commit()
    return cur.rowcount > 0


def query_articles(
    conn: sqlite3.Connection,
    *,
    limit: int = 20,
    category: Optional[str] = None,
    since: Optional[str] = None,
    frequency: Optional[str] = None,
    ids: Optional[List[str]] = None,
) -> List[sqlite3.Row]:
    sql = "SELECT * FROM articles WHERE 1=1"
    params: list = []
    if category:
        sql += " AND LOWER(category) = LOWER(?)"
        params.append(category)
    if since:
        sql += " AND (published_at >= ? OR (published_at IS NULL AND created_at >= ?))"
        params.extend([since, since])
    if frequency:
        sql += " AND scrape_frequency = ?"
        params.append(frequency)
    if ids:
        placeholders = ",".join("?" * len(ids))
        sql += f" AND id IN ({placeholders})"
        params.extend(ids)
    sql += " ORDER BY COALESCE(published_at, created_at) DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
# Source schedule log
# ---------------------------------------------------------------------------

def get_source_log(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """Return all rows from source_log, ordered by source_name."""
    return conn.execute(
        "SELECT * FROM source_log ORDER BY source_name"
    ).fetchall()


def is_source_due(conn: sqlite3.Connection, source_key: str, frequency: str) -> bool:
    """Return True if this source has never been scraped or is past its interval."""
    row = conn.execute(
        "SELECT last_scraped_at FROM source_log WHERE source_key = ?",
        (source_key,),
    ).fetchone()
    if not row or not row["last_scraped_at"]:
        return True
    days_required = FREQ_DAYS.get(frequency, 1)
    last = datetime.fromisoformat(row["last_scraped_at"])
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - last).days
    return elapsed >= days_required


def mark_source_scraped(
    conn: sqlite3.Connection,
    source_key: str,
    source_name: str,
    frequency: str,
) -> None:
    """Upsert the last_scraped_at timestamp for a source."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO source_log (source_key, source_name, frequency, last_scraped_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(source_key) DO UPDATE SET
            source_name     = excluded.source_name,
            frequency       = excluded.frequency,
            last_scraped_at = excluded.last_scraped_at
        """,
        (source_key, source_name, frequency, now),
    )
    conn.commit()
