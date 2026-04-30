"""Archive of generated outputs.

Every run of the Create page writes a sibling .json file alongside the
HTML/PDF/DOCX/MD/EML/TXT exports — that JSON is the source of truth for
the archive. This module just scans the output directory, parses those
sidecars, and exposes a clean API to the dashboard.

No SQLite, no separate index file: the filesystem is the database. To
delete an archived run, you delete every file with that basename.

Public API:
- list_archive(output_dir, *, kind=None, query=None,
               date_from=None, date_to=None) -> list[ArchiveEntry]
- get_entry(output_dir, basename) -> ArchiveEntry | None
- delete_entry(output_dir, basename) -> dict
- archive_stats(entries) -> dict
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)


# Filename pattern: <kind>-YYYY-MM-DD-vN.<ext>
# Example: blog-2026-04-30-v3.html
_FILENAME_RE = re.compile(
    r"^(?P<kind>newsletter|blog)-(?P<date>\d{4}-\d{2}-\d{2})-v(?P<version>\d+)$"
)

# Formats we know about, in display order. Anything else found on disk with
# the same basename is also surfaced under "other".
KNOWN_FORMATS = ("html", "pdf", "docx", "md", "eml", "txt", "json")


@dataclass
class ArchiveEntry:
    basename:         str            # 'blog-2026-04-30-v1'
    kind:             str            # 'blog' | 'newsletter'
    title:            str
    date:             str            # YYYY-MM-DD (from filename)
    version:          int
    generated_at:     str            # ISO timestamp from JSON
    compliance_grade: Optional[str] = None     # 'pass' | 'warn' | 'fail' | None
    compliance_pass_rate: Optional[float] = None
    quality_score:    Optional[int]   = None     # 0..100 (blog only)
    quality_grade:    Optional[str]   = None
    paths:            dict           = field(default_factory=dict)   # {ext: abs_path}
    input_article_count: int         = 0
    sections_count:   int            = 0
    word_count:       Optional[int]  = None     # rough, for blog only
    sources_cited_count: int         = 0
    json_path:        Optional[str]  = None     # convenience

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_basename(filename: str) -> Optional[dict]:
    """Returns {kind, date, version} if the filename matches our pattern."""
    stem = os.path.splitext(filename)[0]
    m = _FILENAME_RE.match(stem)
    if not m:
        return None
    return {
        "kind":     m.group("kind"),
        "date":     m.group("date"),
        "version":  int(m.group("version")),
        "stem":     stem,
    }


def _word_count_of(post: dict) -> Optional[int]:
    """Quick word-count for the blog body (intro + sections + conclusion)."""
    if not post:
        return None
    chunks = [post.get("intro", ""), post.get("conclusion", "")]
    chunks += [s.get("content", "") for s in post.get("sections", []) or []]
    text = " ".join(c or "" for c in chunks)
    if not text.strip():
        return None
    return len(re.findall(r"\w+", text))


def _build_entry(output_dir: str, basename: str) -> Optional[ArchiveEntry]:
    """Build an ArchiveEntry by reading the sidecar JSON + scanning siblings."""
    parsed = _parse_basename(basename + ".json")
    if not parsed:
        return None
    json_path = os.path.join(output_dir, basename + ".json")
    if not os.path.isfile(json_path):
        return None

    try:
        with open(json_path, encoding="utf-8") as f:
            sidecar = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Skipping unreadable sidecar %s: %s", json_path, e)
        return None

    result = sidecar.get("result") or {}
    cs = sidecar.get("compliance_summary") or {}

    # Discover sibling files for every known format.
    paths: dict[str, str] = {}
    for ext in KNOWN_FORMATS:
        p = os.path.join(output_dir, f"{basename}.{ext}")
        if os.path.isfile(p):
            paths[ext] = p

    quality_score = None
    quality_grade = None
    # Newer JSONs may carry a quality block; older ones won't.
    q = sidecar.get("quality") or {}
    if isinstance(q, dict):
        quality_score = q.get("total")
        quality_grade = q.get("grade")

    return ArchiveEntry(
        basename=parsed["stem"],
        kind=parsed["kind"],
        title=(sidecar.get("subject_or_title") or
               result.get("title") or
               result.get("subject_line") or
               "(untitled)"),
        date=parsed["date"],
        version=parsed["version"],
        generated_at=sidecar.get("generated_at_utc", ""),
        compliance_grade=cs.get("grade"),
        compliance_pass_rate=cs.get("pass_rate"),
        quality_score=quality_score,
        quality_grade=quality_grade,
        paths=paths,
        input_article_count=len(sidecar.get("input_articles") or []),
        sections_count=len(result.get("sections") or []),
        word_count=_word_count_of(result) if parsed["kind"] == "blog" else None,
        sources_cited_count=len(result.get("sources_cited") or []),
        json_path=json_path,
    )


def list_archive(
    output_dir: str,
    *,
    kind: Optional[str] = None,
    query: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
) -> list[ArchiveEntry]:
    """Scan output_dir, return matching entries newest-first.

    Filters are AND-combined. `query` is a case-insensitive substring match
    against the title.
    """
    if not os.path.isdir(output_dir):
        return []
    entries: list[ArchiveEntry] = []
    seen_basenames: set[str] = set()
    for filename in os.listdir(output_dir):
        if not filename.endswith(".json"):
            continue
        stem = filename[:-len(".json")]
        if stem in seen_basenames:
            continue
        seen_basenames.add(stem)
        entry = _build_entry(output_dir, stem)
        if entry is None:
            continue
        if kind and entry.kind != kind:
            continue
        if query:
            q = query.strip().lower()
            if q and q not in entry.title.lower():
                continue
        if date_from and entry.date < date_from:
            continue
        if date_to and entry.date > date_to:
            continue
        entries.append(entry)
    # Newest first by generated_at, then by date+version as a fallback.
    entries.sort(
        key=lambda e: (e.generated_at or "", e.date, e.version),
        reverse=True,
    )
    return entries


def get_entry(output_dir: str, basename: str) -> Optional[ArchiveEntry]:
    """Look up a single entry by its basename (e.g. 'blog-2026-04-30-v1')."""
    return _build_entry(output_dir, basename)


def delete_entry(output_dir: str, basename: str) -> dict:
    """Delete every file in output_dir with the given basename, regardless
    of extension. Returns counts of removed/skipped files.

    Refuses path traversal: basename must be alphanumeric+dashes only.
    """
    if not basename or not re.match(r"^[A-Za-z0-9_\-]+$", basename):
        return {"ok": False, "error": "Invalid basename."}
    if not os.path.isdir(output_dir):
        return {"ok": False, "error": "Output directory missing."}

    removed: list[str] = []
    failed:  list[str] = []
    for filename in os.listdir(output_dir):
        stem = os.path.splitext(filename)[0]
        if stem != basename:
            continue
        path = os.path.join(output_dir, filename)
        try:
            os.remove(path)
            removed.append(filename)
        except OSError as e:
            log.warning("Could not delete %s: %s", path, e)
            failed.append(f"{filename} ({e})")
    if not removed:
        return {"ok": False, "error": "No files matched that basename."}
    return {
        "ok":      not failed,
        "removed": removed,
        "failed":  failed,
        "count":   len(removed),
    }


def archive_stats(entries: list[ArchiveEntry]) -> dict:
    """Headline stats for the archive page."""
    if not entries:
        return {
            "total": 0, "by_kind": {}, "latest": None,
            "avg_compliance_pass_rate": None,
            "avg_quality_score": None,
            "compliance_grade_counts": {},
        }
    by_kind: dict[str, int] = {}
    pass_rates: list[float] = []
    quality_scores: list[int] = []
    grade_counts: dict[str, int] = {}
    for e in entries:
        by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
        if e.compliance_pass_rate is not None:
            pass_rates.append(e.compliance_pass_rate)
        if e.compliance_grade:
            grade_counts[e.compliance_grade] = grade_counts.get(e.compliance_grade, 0) + 1
        if e.quality_score is not None:
            quality_scores.append(e.quality_score)
    return {
        "total":   len(entries),
        "by_kind": by_kind,
        "latest":  entries[0].generated_at if entries else None,
        "avg_compliance_pass_rate": (sum(pass_rates) / len(pass_rates)
                                      if pass_rates else None),
        "avg_quality_score": (sum(quality_scores) / len(quality_scores)
                               if quality_scores else None),
        "compliance_grade_counts": grade_counts,
    }
