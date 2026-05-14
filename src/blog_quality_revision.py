"""Quality-driven revision loop for generated blog posts.

Uses the existing 100-pt analyser (src/blog_quality.py) as the oracle,
then asks Claude to rewrite the weakest category until the post clears a
target score or we hit the iteration cap.

Public API:
- revise_for_quality(post, *, client, model, kind, target_score=78,
                      max_iterations=2, progress_cb=None) -> dict
  Returns {final_post, audit, iterations, revised}.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Optional

import anthropic

from .blog_quality import quick_score
from ._json import parse_json_response
from .brand_voice import voice_block

log = logging.getLogger(__name__)


# Threshold below which we'll attempt a revision pass.
DEFAULT_TARGET_SCORE = 85
DEFAULT_CATEGORY_FLOOR_PCT = 0.50   # 50% of category max → flag for rework

# Max ratio (current / max) — categories below this become rewrite candidates.
_CATEGORY_LABEL = {
    "content":   "Content Quality",
    "seo":       "SEO Optimization",
    "eeat":      "E-E-A-T Signals",
    "technical": "Technical Elements",
    "ai":        "AI Citation Readiness",
}


def _weakest_category(score: dict) -> Optional[str]:
    """Return the short-key of the lowest-percentage category, or None if all
    categories are at or above the floor."""
    cats = score.get("categories", {}) or {}
    maxs = score.get("max_per_category", {}) or {}
    worst_key, worst_pct = None, 1.0
    for k, v in cats.items():
        m = maxs.get(k, 1) or 1
        pct = v / m
        if pct < worst_pct:
            worst_pct, worst_key = pct, k
    if worst_pct >= DEFAULT_CATEGORY_FLOOR_PCT:
        return None
    return worst_key


def _issue_summary(score: dict, category: str, *, max_n: int = 6) -> str:
    """Pull the analyser's prioritised fix list for the target category."""
    issues = score.get("top_issues", []) or []
    cat_full = {
        "content": "content", "seo": "seo", "eeat": "eeat",
        "technical": "technical", "ai": "ai_citation",
    }.get(category, category)
    matched = [i for i in issues
               if (i.get("category") or "").lower().startswith(cat_full)]
    if not matched:
        matched = issues
    return "\n".join(
        f"- [{i.get('severity','?')}] {i.get('issue','')}"
        for i in matched[:max_n]
    )


_REVISER_PERSONA = (
    "You are a senior UK personal-finance editor. You revise existing blog "
    "posts to lift specific quality signals (depth, citations, E-E-A-T, "
    "structure, AI-citation friendliness) without changing the underlying "
    "thesis. You return ONLY valid JSON. No prose outside the object."
)

_REVISER_TEMPLATE = """Revise this blog post to lift the {category_label} score.

The 100-point quality analyser flagged the following specific issues that
must be addressed in the revision:

{issue_summary}

REVISION CONSTRAINTS:
- Preserve the title, subtitle, byline, sources_cited URLs (verified), and
  overall thesis. You may improve any of these but DO NOT change the topic.
- Keep total word count within ±20% of the original — focus on quality lift,
  not length.
- All numerical claims (£ amounts, percentages, dates) must remain identical
  unless you are CORRECTING an obvious factual error and adding an inline
  citation in '(Source, year)' format from the existing sources_cited list.
- Use UK English, £ for currency, UK institution names (HMRC, FCA, Bank of
  England, ONS).
- Do not introduce any banned topic (cryptocurrency, regulated investment
  recommendations) or AI-trigger words ("delve", "leverage", "navigate the
  landscape", "in today's fast-paced world").

Return a JSON object with this EXACT shape (same keys as the original post):
{{
  "title":            "string",
  "subtitle":         "string",
  "meta_description": "string (140-160 chars)",
  "byline":           "string",
  "key_takeaways":    ["string", "..."],
  "intro":            "string (paragraphs separated by \\n\\n)",
  "sections": [
    {{ "heading": "string", "content": "string", "pull_quote": "string-or-empty" }}
  ],
  "conclusion":    "string",
  "faqs":          [{{"question": "string", "answer": "string"}}],
  "sources_cited": [{{"title": "string", "url": "string", "source": "string"}}],
  "seo_tags":      ["string", "..."],
  "_changes_made": ["short bullet describing each substantive change", "..."]
}}

ORIGINAL POST (JSON):
{post_json}
"""


def _revise_once(post: dict, score: dict, target_category: str,
                 client: anthropic.Anthropic, model: str) -> Optional[dict]:
    """Single LLM revision pass for the given category. Returns revised post
    dict (with _changes_made appended) or None on failure."""
    prompt = _REVISER_TEMPLATE.format(
        category_label=_CATEGORY_LABEL.get(target_category, target_category),
        issue_summary=_issue_summary(score, target_category),
        post_json=json.dumps(post, ensure_ascii=False, indent=2),
    )
    try:
        # Cap output at a generous size — the original + revisions can grow.
        max_tok = min(16000, max(6000, len(json.dumps(post)) // 3 + 2000))
        resp = client.messages.create(
            model=model,
            max_tokens=max_tok,
            system=[
                {"type": "text",
                 "text": voice_block(include_past_replies=True, max_replies=4),
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": _REVISER_PERSONA},
            ],
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
        revised = parse_json_response(text)
    except (anthropic.APIError, json.JSONDecodeError, IndexError, AttributeError) as exc:
        log.warning("Quality revision pass failed: %s", exc)
        return None

    # Validate: must contain the structural keys, otherwise we have nothing.
    if not isinstance(revised, dict):
        return None
    for k in ("title", "intro", "sections", "conclusion"):
        if k not in revised:
            log.warning("Revised post missing key '%s' — discarding revision.", k)
            return None

    # Defensive merge: anything the LLM omitted falls back to the original.
    # Without this, an LLM that returns only the structural keys (title/
    # sections/etc) silently drops sources_cited/byline/meta_description/
    # seo_tags/faqs/key_takeaways and the downstream export crashes or
    # produces a stripped-down post. Keys present in `revised` override.
    merged = {**post, **revised}

    # Server-controlled fields ALWAYS keep the original — the LLM must not
    # touch dates, reading_time (computed from word count), or our internal
    # bookkeeping like _outline.
    for k in ("published_iso", "published_human", "reading_time_minutes",
              "_diversity_warning", "_outline", "_seo_brief"):
        if k in post:
            merged[k] = post[k]

    # Recompute reading_time from the revised body so the displayed minutes
    # reflect the actual revision, not the original word count.
    try:
        from .blog_generator import _compute_reading_time
        merged["reading_time_minutes"] = _compute_reading_time(merged)
    except Exception:
        pass

    return merged


def _render_md_for_score(post: dict) -> str:
    """Render a post dict to the same markdown format the analyser scores
    (delegates to the canonical exporter so we stay in lock-step)."""
    from .exporters import to_markdown
    return to_markdown(post, kind="blog")


def revise_for_quality(
    post: dict,
    *,
    client: anthropic.Anthropic,
    model: str,
    kind: str = "blog",
    target_score: int = DEFAULT_TARGET_SCORE,
    max_iterations: int = 3,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """Iteratively rewrite the weakest category until score ≥ target_score
    or we hit max_iterations. Newsletter is a no-op (analyser is blog-tuned).

    Returns:
        {
          "final_post":    dict,         # the (possibly revised) post
          "final_score":   dict,         # quick_score result
          "initial_score": int,          # starting total for the audit
          "iterations":    int,          # how many revision rounds we ran
          "revised":       bool,         # did anything actually change
          "audit": [
            {iteration, total, grade, target_category, changes: [...]}
          ],
        }
    """
    if kind != "blog":
        return {"final_post": post, "final_score": None,
                "initial_score": None, "iterations": 0,
                "revised": False, "audit": []}

    # Initial score
    if progress_cb: progress_cb("scoring initial draft")
    score = quick_score(_render_md_for_score(post), suffix=".md")
    audit = [{"iteration": 0, "total": score.get("total", 0),
              "grade": score.get("grade", "?"),
              "target_category": None, "changes": []}]
    initial_total = score.get("total", 0)
    revised_any = False
    current = post
    iteration = 0

    while iteration < max_iterations and score.get("total", 0) < target_score:
        target_cat = _weakest_category(score)
        if target_cat is None:
            log.info("All categories above floor — no further revisions worth attempting.")
            break
        iteration += 1
        if progress_cb:
            progress_cb(f"revising {_CATEGORY_LABEL.get(target_cat, target_cat)} "
                        f"(iteration {iteration}/{max_iterations})")
        revised = _revise_once(current, score, target_cat, client, model)
        if revised is None:
            log.info("Iteration %d revision failed; keeping previous version.", iteration)
            break
        # Score the revision; only adopt if it actually improved.
        if progress_cb: progress_cb(f"re-scoring after iteration {iteration}")
        new_score = quick_score(_render_md_for_score(revised), suffix=".md")
        improved = new_score.get("total", 0) > score.get("total", 0)
        audit.append({
            "iteration":       iteration,
            "total":           new_score.get("total", 0),
            "grade":           new_score.get("grade", "?"),
            "target_category": target_cat,
            "changes":         revised.pop("_changes_made", []),
            "improved":        improved,
        })
        if improved:
            current = revised
            score = new_score
            revised_any = True
        else:
            log.info("Iteration %d did not improve score (%d → %d); keeping previous.",
                     iteration, score.get("total", 0), new_score.get("total", 0))
            break

    return {
        "final_post":    current,
        "final_score":   score,
        "initial_score": initial_total,
        "iterations":    iteration,
        "revised":       revised_any,
        "audit":         audit,
    }
