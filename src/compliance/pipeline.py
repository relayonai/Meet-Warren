"""Compliance pipeline — orchestrates grade → analyse → enforce → re-grade.

Public entrypoints:
- ensure_compliant(content, kind, ...): full loop for generated outputs
- scan_article(article, summary, ...):  lightweight pre-store check for scraped news
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import anthropic

from .analyzer import analyze_findings
from .enforcer import revise_content
from .grader import grade_content
from .rulebook import load_rulebook

log = logging.getLogger(__name__)


_COMPLIANCE_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS compliance_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    content_type  TEXT NOT NULL,    -- 'article'|'newsletter'|'blog'
    content_ref   TEXT,             -- article id or output filename
    grade         TEXT NOT NULL,    -- 'pass'|'warn'|'fail'
    pass_rate     REAL,
    failed_count  INTEGER,
    iterations    INTEGER DEFAULT 0,
    revised       INTEGER DEFAULT 0,
    grading_json  TEXT,
    analysis_json TEXT,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_compliance_type  ON compliance_log(content_type);
CREATE INDEX IF NOT EXISTS idx_compliance_grade ON compliance_log(grade);
"""

_ARTICLE_MIGRATIONS = [
    "ALTER TABLE articles ADD COLUMN compliance_grade TEXT",
    "ALTER TABLE articles ADD COLUMN compliance_notes TEXT",
]


def init_compliance_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(_COMPLIANCE_LOG_SCHEMA)
    conn.commit()
    for sql in _ARTICLE_MIGRATIONS:
        try:
            conn.execute(sql)
            conn.commit()
        except sqlite3.OperationalError:
            pass


def _log_to_db(
    conn: Optional[sqlite3.Connection],
    *,
    content_type: str,
    content_ref: str,
    grading: dict,
    analysis: Optional[dict],
    iterations: int,
    revised: bool,
) -> None:
    if conn is None:
        return
    try:
        init_compliance_tables(conn)
        s = grading.get("summary", {})
        conn.execute(
            "INSERT INTO compliance_log (content_type, content_ref, grade, pass_rate, "
            "failed_count, iterations, revised, grading_json, analysis_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (content_type, content_ref, s.get("grade", "?"),
             float(s.get("pass_rate", 0.0)), int(s.get("failed", 0)),
             iterations, 1 if revised else 0,
             json.dumps(grading, ensure_ascii=False),
             json.dumps(analysis or {}, ensure_ascii=False)),
        )
        conn.commit()
    except Exception as e:
        log.warning("Could not write compliance_log: %s", e)


# ---------------------------------------------------------------------------
# For generated outputs (newsletter / blog)
# ---------------------------------------------------------------------------

def ensure_compliant(
    content: str,
    *,
    kind: str,                                # 'newsletter' | 'blog'
    client: anthropic.Anthropic,
    model: str = "claude-sonnet-4-5",
    max_iterations: int = 1,                  # one revision is enough in practice
    conn: Optional[sqlite3.Connection] = None,
    content_ref: str = "",
    grader_model: Optional[str] = None,       # smaller/faster model for grade+revise
    fast_path_if_hard_clean: bool = True,     # skip principle LLM when no hard fails
    progress_cb=None,                         # optional callable(stage:str)
) -> dict:
    """Full grade→analyse→enforce loop. Returns:
    {
      "final_content": str,
      "final_grade": dict (grading.json structure),
      "analysis": dict,
      "iterations": int,
      "revised": bool,
      "audit": [{iteration, grade, changes}]
    }
    """
    rb = load_rulebook()
    g_model = grader_model or model

    def _grade_fast_or_full(text: str) -> dict:
        """Run hard rules first; if everything passes, skip the principle LLM call."""
        if fast_path_if_hard_clean:
            preview = grade_content(text, kind=kind, rulebook=rb,
                                    skip_principles=True, client=None)
            if preview["summary"]["failed"] == 0:
                # No hard violations — assume compliant, mark grade pass.
                preview["_fast_path"] = True
                return preview
        return grade_content(text, kind=kind, client=client, model=g_model, rulebook=rb)

    audit = []
    iteration = 0
    current = content
    if progress_cb: progress_cb("Grading initial content")
    grading = _grade_fast_or_full(current)
    audit.append({"iteration": 0, "grade": grading["summary"]["grade"], "changes": []})

    analysis = analyze_findings(grading)
    revised_any = False

    while grading["summary"]["grade"] != "pass" and iteration < max_iterations:
        iteration += 1
        if progress_cb: progress_cb(f"Revising (iteration {iteration})")
        result = revise_content(current, analysis, client=client, model=g_model, rulebook=rb)
        if result["revised_content"] == current and not result["changes_made"]:
            log.info("No further revisions possible at iteration %d.", iteration)
            break
        current = result["revised_content"]
        revised_any = True
        if progress_cb: progress_cb(f"Re-grading (iteration {iteration})")
        grading = _grade_fast_or_full(current)
        analysis = analyze_findings(grading)
        audit.append({
            "iteration": iteration,
            "grade":     grading["summary"]["grade"],
            "changes":   result["changes_made"],
        })

    _log_to_db(conn,
               content_type=kind, content_ref=content_ref,
               grading=grading, analysis=analysis,
               iterations=iteration, revised=revised_any)

    return {
        "final_content": current,
        "final_grade":   grading,
        "analysis":      analysis,
        "iterations":    iteration,
        "revised":       revised_any,
        "audit":         audit,
    }


# ---------------------------------------------------------------------------
# For scraped articles
# ---------------------------------------------------------------------------

def scan_article(
    article_text: str,
    *,
    article_id: str,
    title: str = "",
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """Lightweight pre-store check. Hard-rule pass only — no LLM call.
    News content gets banned-topic violations downgraded to advisory severity.
    """
    grading = grade_content(article_text + ("\n" + title if title else ""),
                            kind="article", skip_principles=True)
    s = grading["summary"]
    _log_to_db(conn,
               content_type="article", content_ref=article_id,
               grading=grading, analysis=None,
               iterations=0, revised=False)
    return {
        "grade":     s["grade"],
        "pass_rate": s["pass_rate"],
        "failed":    s["failed"],
        "notes":     [
            f"§{e['section']}: {e['text']}" for e in grading["expectations"] if not e["passed"]
        ][:6],
        "grading":   grading,
    }
