from __future__ import annotations

import json
import os
import sqlite3
from typing import Iterable, List, Optional

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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_published ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_category  ON articles(category);
"""


def get_connection(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def existing_urls(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT url FROM articles").fetchall()
    return {row["url"] for row in rows}


def insert_article(conn: sqlite3.Connection, article, summary: dict) -> bool:
    """Insert an article + its summary. Returns True if inserted, False if it already existed."""
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO articles
            (id, url, title, source, published_at, raw_content, summary, category, relevance_score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
) -> List[sqlite3.Row]:
    sql = "SELECT * FROM articles WHERE 1=1"
    params: list = []
    if category:
        sql += " AND LOWER(category) = LOWER(?)"
        params.append(category)
    if since:
        sql += " AND (published_at >= ? OR (published_at IS NULL AND created_at >= ?))"
        params.extend([since, since])
    sql += " ORDER BY COALESCE(published_at, created_at) DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()
