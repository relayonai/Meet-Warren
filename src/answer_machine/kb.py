"""Knowledge-base loader for the User Answer Machine.

Reads three source documents and turns them into a structured snapshot:
- Brand Narrative (.docx)  -> voice/tone block
- FAQs (.docx)             -> [{question, answer_long, answer_short}]
- Meta Comments (.xlsx)    -> [{platform, comment, response, sentiment, ...}]

The parsed snapshot is cached as JSON on disk so the dashboard doesn't have
to re-read .docx/.xlsx on every request. Re-parsing is automatic when any
source file's mtime is newer than the cache mtime.
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

# Resolve paths relative to repo root regardless of cwd.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
SOURCES_DIR = os.path.join(_REPO, "data", "answer_machine_sources")
CACHE_PATH  = os.path.join(_REPO, "data", "answer_machine_kb.json")

PATH_BRAND   = os.path.join(SOURCES_DIR, "brand_narrative.docx")
PATH_FAQS    = os.path.join(SOURCES_DIR, "faqs.docx")
PATH_COMMENTS = os.path.join(SOURCES_DIR, "meta_comments.xlsx")


@dataclass
class FAQEntry:
    question: str
    answer_long: str
    answer_short: str
    section: str = ""


@dataclass
class CommentExample:
    platform: str
    sentiment: str
    comment: str
    response: str
    is_dm: bool = False
    date: str = ""
    account: str = ""


@dataclass
class KnowledgeBase:
    extracted_at: str
    brand_narrative: str
    brand_voice_principles: list[str] = field(default_factory=list)
    faqs: list[FAQEntry] = field(default_factory=list)
    comment_examples: list[CommentExample] = field(default_factory=list)
    sources: dict = field(default_factory=dict)   # {name: {path, mtime}}

    def to_dict(self) -> dict:
        return {
            "extracted_at": self.extracted_at,
            "brand_narrative": self.brand_narrative,
            "brand_voice_principles": self.brand_voice_principles,
            "faqs": [asdict(f) for f in self.faqs],
            "comment_examples": [asdict(c) for c in self.comment_examples],
            "sources": self.sources,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "KnowledgeBase":
        return cls(
            extracted_at=d.get("extracted_at", ""),
            brand_narrative=d.get("brand_narrative", ""),
            brand_voice_principles=d.get("brand_voice_principles", []) or [],
            faqs=[FAQEntry(**f) for f in d.get("faqs", []) or []],
            comment_examples=[CommentExample(**c) for c in d.get("comment_examples", []) or []],
            sources=d.get("sources", {}) or {},
        )


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _docx_paragraphs(path: str) -> list[str]:
    """Return non-empty paragraph strings from a .docx file."""
    from docx import Document
    doc = Document(path)
    return [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]


def _parse_brand(path: str) -> tuple[str, list[str]]:
    """Extract the brand narrative + a short list of distilled principles."""
    paras = _docx_paragraphs(path)
    full = "\n".join(paras)

    # Heuristic distillation: any short, declarative line under "Our Edge",
    # "Mission", or "Evolution" sections we treat as a tone principle.
    principles: list[str] = []
    capture_keys = ("mission", "evolution", "edge", "voice", "tone", "philosophy",
                    "we don't", "we are", "who we are", "narrative")
    for p in paras:
        low = p.lower()
        if any(k in low for k in capture_keys) and 20 < len(p) < 320:
            principles.append(p)
    # De-dupe while preserving order.
    seen = set(); deduped = []
    for p in principles:
        if p not in seen:
            seen.add(p); deduped.append(p)
    return full, deduped[:12]


def _parse_faqs(path: str) -> list[FAQEntry]:
    """The FAQ doc uses a question line followed by 'Longer answer:' /
    'Shorter answer:' blocks. We chunk along those markers.
    """
    paras = _docx_paragraphs(path)
    entries: list[FAQEntry] = []
    section = ""
    i = 0
    n = len(paras)

    SHORT_HEAD = re.compile(r"^shorter answer\s*:?$", re.I)
    LONG_HEAD  = re.compile(r"^longer answer\s*:?$", re.I)
    Q_HINT     = re.compile(r"\?$|^(why|what|how|do|does|is|can|are|will)\b", re.I)

    def is_question(line: str) -> bool:
        if line.endswith("?"):
            return True
        return bool(Q_HINT.match(line)) and len(line) < 180

    while i < n:
        line = paras[i]

        # Section headings: short, no question mark, no answer marker
        if not is_question(line) and not SHORT_HEAD.match(line) and not LONG_HEAD.match(line):
            if len(line) < 60 and not line.endswith(":"):
                section = line
            i += 1
            continue

        # Question line
        if is_question(line):
            question = line
            answer_long_parts: list[str] = []
            answer_short_parts: list[str] = []
            mode = None  # 'long' | 'short' | None
            j = i + 1
            while j < n:
                nxt = paras[j]
                if LONG_HEAD.match(nxt):
                    mode = "long"; j += 1; continue
                if SHORT_HEAD.match(nxt):
                    mode = "short"; j += 1; continue
                # Stop when we hit the next question
                if is_question(nxt):
                    break
                # Append to current bucket
                if mode == "long":
                    answer_long_parts.append(nxt)
                elif mode == "short":
                    answer_short_parts.append(nxt)
                else:
                    # No marker — treat first paragraph as long, second as short
                    if not answer_long_parts:
                        answer_long_parts.append(nxt)
                    elif not answer_short_parts:
                        answer_short_parts.append(nxt)
                j += 1

            entries.append(FAQEntry(
                question=question,
                answer_long="\n".join(answer_long_parts).strip(),
                answer_short="\n".join(answer_short_parts).strip(),
                section=section,
            ))
            i = j
            continue

        i += 1

    # Drop entries where both answers are empty (mis-parsed).
    return [e for e in entries if (e.answer_long or e.answer_short)]


def _parse_comments(path: str) -> list[CommentExample]:
    """Read the Meta comments xlsx. Schema (col A..I):
    Platform | Date | Account name | comment vs DM | comment | sentiment |
    action needed? | Action taken | response copy
    """
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    examples: list[CommentExample] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        cells = list(row) + [None] * (9 - len(row))
        platform, date, account, kind, comment, sentiment, _, _, response = cells[:9]
        if not comment or not str(comment).strip():
            continue
        if not response or not str(response).strip():
            continue   # No exemplar reply, skip
        date_str = ""
        if isinstance(date, datetime):
            date_str = date.date().isoformat()
        elif date:
            date_str = str(date).split(" ")[0]
        examples.append(CommentExample(
            platform=str(platform or "").strip(),
            sentiment=str(sentiment or "").strip(),
            comment=str(comment).strip(),
            response=str(response).strip(),
            is_dm=str(kind or "").strip().lower() == "dm",
            date=date_str,
            account=str(account or "").strip(),
        ))
    return examples


# ---------------------------------------------------------------------------
# Build / cache
# ---------------------------------------------------------------------------

def _source_mtimes() -> dict:
    out = {}
    for label, path in (("brand", PATH_BRAND), ("faqs", PATH_FAQS), ("comments", PATH_COMMENTS)):
        out[label] = {
            "path": path,
            "mtime": os.path.getmtime(path) if os.path.isfile(path) else 0.0,
            "exists": os.path.isfile(path),
        }
    return out


def _cache_is_fresh() -> bool:
    if not os.path.isfile(CACHE_PATH):
        return False
    cache_mt = os.path.getmtime(CACHE_PATH)
    for s in _source_mtimes().values():
        if s["exists"] and s["mtime"] > cache_mt:
            return False
    return True


def build_knowledge_base() -> KnowledgeBase:
    """Parse all three source docs and persist a cached JSON snapshot."""
    if not os.path.isfile(PATH_BRAND):
        raise FileNotFoundError(f"Brand narrative missing: {PATH_BRAND}")
    if not os.path.isfile(PATH_FAQS):
        raise FileNotFoundError(f"FAQs missing: {PATH_FAQS}")

    brand_text, principles = _parse_brand(PATH_BRAND)
    faqs = _parse_faqs(PATH_FAQS)
    comments = _parse_comments(PATH_COMMENTS) if os.path.isfile(PATH_COMMENTS) else []

    kb = KnowledgeBase(
        extracted_at=datetime.utcnow().isoformat(),
        brand_narrative=brand_text,
        brand_voice_principles=principles,
        faqs=faqs,
        comment_examples=comments,
        sources=_source_mtimes(),
    )
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(kb.to_dict(), f, indent=2, ensure_ascii=False, default=str)
    log.info("Answer-machine KB built: %d FAQs, %d comments, %d principles",
             len(faqs), len(comments), len(principles))
    return kb


def load_knowledge_base(force_rebuild: bool = False) -> KnowledgeBase:
    """Return a KnowledgeBase, rebuilding from source docs if cache is stale."""
    if force_rebuild or not _cache_is_fresh():
        return build_knowledge_base()
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return KnowledgeBase.from_dict(json.load(f))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Cache unreadable (%s); rebuilding", e)
        return build_knowledge_base()
