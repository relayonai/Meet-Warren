"""Internal-link corpus + suggester for blog posts.

Scans Warren's output/ directory for prior blog generations (the *.json sidecar
written by the dashboard), and returns a compact corpus the blog generator can
weave 3–5 contextual links into. Closes the SEO gap surfaced by the quality
analyser, which gave 0/4 on Internal Linking when posts had no prior siblings.

Public API:
- load_published_corpus(output_dir, *, exclude_basename=None, link_prefix='/blog/')
    -> list[{slug, url, title, blurb, published_iso}]
- format_for_prompt(corpus, *, max_items=12) -> str
"""
from __future__ import annotations

import glob
import json
import logging
import os
from typing import Optional

log = logging.getLogger(__name__)


def _safe_load(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Could not parse %s: %s", path, e)
        return None


def _short_blurb(s: str, max_len: int = 140) -> str:
    s = (s or "").strip().replace("\n", " ")
    if len(s) <= max_len:
        return s
    cut = s[:max_len].rsplit(" ", 1)[0]
    return cut + "…"


def load_published_corpus(
    output_dir: str,
    *,
    exclude_basename: Optional[str] = None,
    link_prefix: str = "/blog/",
) -> list[dict]:
    """Scan output_dir for blog-*.json sidecars and return a corpus list.

    Each entry: {slug, url, title, blurb, published_iso}

    `exclude_basename` lets the caller skip the post currently being generated
    (e.g. 'blog-2026-04-29-v3'); pass without the .json suffix.
    `link_prefix` is what the slug is appended to to form the URL — defaults
    to '/blog/' which is a sensible site-relative default. Override at deploy
    time if you publish under a different path.
    """
    if not output_dir or not os.path.isdir(output_dir):
        return []

    pattern = os.path.join(output_dir, "blog-*.json")
    paths = sorted(glob.glob(pattern), reverse=True)  # newest first

    corpus: list[dict] = []
    for p in paths:
        basename = os.path.splitext(os.path.basename(p))[0]
        if exclude_basename and basename == exclude_basename:
            continue
        data = _safe_load(p)
        if not data:
            continue
        if (data.get("kind") or "").lower() != "blog":
            continue
        result = data.get("result") or {}
        title = (result.get("title")
                 or data.get("title")
                 or data.get("subject_or_title")
                 or "").strip()
        if not title:
            continue
        blurb = _short_blurb(
            result.get("meta_description")
            or result.get("subtitle")
            or result.get("intro", "")
        )
        corpus.append({
            "slug":          basename,
            "url":           f"{link_prefix.rstrip('/')}/{basename}",
            "title":         title,
            "blurb":         blurb,
            "published_iso": result.get("published_iso", ""),
        })
    return corpus


def format_for_prompt(corpus: list[dict], *, max_items: int = 12) -> str:
    """Render the corpus as a plain-text block for inclusion in an LLM prompt.

    Returns an empty string if the corpus is empty so the caller can append it
    unconditionally without producing dangling section headers.
    """
    if not corpus:
        return ""
    items = corpus[:max_items]
    lines = [
        "RELATED WARREN POSTS (use 3–5 of these as inline internal links in your draft):",
    ]
    for item in items:
        line = f"- [{item['title']}]({item['url']})"
        if item.get("blurb"):
            line += f"  — {item['blurb']}"
        lines.append(line)
    lines.append(
        "Weave 3–5 of those links naturally into the body where the topic "
        "overlaps. Use descriptive anchor text — never 'click here' or 'this "
        "article'. Use markdown link syntax exactly as shown above. Do NOT "
        "invent links to posts that aren't on this list."
    )
    return "\n".join(lines)
