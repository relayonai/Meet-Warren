"""Visual extraction pass — Pass 4 of the blog generation pipeline.

Mines generated content for data and synthesises styled visual elements.

Blog types:       stat_card_row, comparison_card, callout, table,
                  chart_bar, chart_line, chart_pie
Newsletter types: email_stat_row, email_table, email_divider_callout

Public API:
    extract_visuals(content, article_summaries, kind, client, model) -> list[dict]
    _content_to_text(content, kind) -> str  (exposed for testing)
"""
from __future__ import annotations

import json
import logging

import anthropic

from ._json import parse_json_response

log = logging.getLogger(__name__)

_MAX_BLOG_VISUALS = 4
_MAX_NEWSLETTER_VISUALS = 2

_SYSTEM_BLOG = (
    "You are a data visualisation specialist for Warren, a UK personal finance publisher. "
    "You read a finished blog post and mine it for numbers, percentages, £ figures, and "
    "comparisons that can become rich visual elements. "
    "Return ONLY valid JSON. Never fabricate data — only use figures explicitly present "
    "in the content or source articles provided."
)

_SYSTEM_NEWSLETTER = (
    "You are a data visualisation specialist for Warren, a UK personal finance newsletter. "
    "You read a finished newsletter and identify 1-2 key data points for email-safe visuals "
    "(no JavaScript, no Canvas, max-width 600px, inline styles only). "
    "Return ONLY valid JSON. Never fabricate data — only use figures in the content or sources."
)

_BLOG_TEMPLATE = """Read the blog post below and produce up to {max_visuals} visual elements.

Available types:
1. stat_card_row   — 2-4 large-number stat cards (key figures at a glance)
2. comparison_card — side-by-side table comparing 2-4 options
3. callout         — highlighted box for regulatory notes or key warnings
4. table           — dense tabular data (rates, allowances, fees)
5. chart_bar       — bar chart for magnitude comparisons
6. chart_line      — line chart for time-series / trends
7. chart_pie       — pie/doughnut for share or breakdown data

Rules:
- Maximum {max_visuals} elements total
- NEVER fabricate — only use figures present in the content or article records
- after_section: 0-based index; -1 = after intro. Space visuals across the post.
- Prefer stat_card_row as the first visual (high impact)
- Use comparison_card when 2+ options appear side-by-side
- Use callout only for genuinely important regulatory or actionable notes

Return ONLY a JSON object:
{{
  "visual_elements": [
    {{"type": "stat_card_row", "after_section": <int>,
      "cards": [{{"label": "string", "value": "string", "note": "string"}}]}},
    {{"type": "comparison_card", "after_section": <int>, "title": "string",
      "columns": ["Feature", "Option A", "Option B"],
      "rows": [["label", "val", "val"]]}},
    {{"type": "callout", "after_section": <int>,
      "icon": "⚠", "heading": "string", "body": "string (<=60 words)"}},
    {{"type": "table", "after_section": <int>, "title": "string",
      "headers": ["Col A", "Col B"], "rows": [["v", "v"]]}},
    {{"type": "chart_bar", "after_section": <int>, "title": "string",
      "labels": ["label"], "values": [0.0], "unit": "string"}}
  ]
}}

BLOG POST:
{content_text}

SOURCE ARTICLES (for data verification):
{articles_json}
"""

_NEWSLETTER_TEMPLATE = """Read the newsletter below and produce up to {max_visuals} email-safe visual elements.

Email-safe types only (no JS, no Canvas, all styles inline, max-width 600px):
1. email_stat_row        — 2-4 key figures as a compact stat row
2. email_table           — plain bordered table for rates or comparisons
3. email_divider_callout — styled blockquote box for a key highlight

Rules:
- Maximum {max_visuals} elements total
- NEVER fabricate — only use figures from the content or article records
- after_section: 0-based index; -1 = before sections
- Keep it simple — prefer 1 element if data is thin

Return ONLY a JSON object:
{{
  "visual_elements": [
    {{"type": "email_stat_row", "after_section": <int>,
      "cards": [{{"label": "string", "value": "string", "note": "string"}}]}},
    {{"type": "email_table", "after_section": <int>, "title": "string",
      "headers": ["string"], "rows": [["string"]]}},
    {{"type": "email_divider_callout", "after_section": <int>,
      "heading": "string", "body": "string (<=50 words)"}}
  ]
}}

NEWSLETTER:
{content_text}

SOURCE ARTICLES:
{articles_json}
"""


def _content_to_text(content: dict, kind: str) -> str:
    """Flatten the content dict to plain text for the extractor prompt."""
    parts: list[str] = []
    if kind == "blog":
        parts.append(content.get("title", ""))
        parts.append(content.get("intro", ""))
        for s in content.get("sections", []) or []:
            parts.append(s.get("heading", ""))
            parts.append(s.get("content", ""))
        parts.append(content.get("conclusion", ""))
    else:
        parts.append(content.get("subject_line", ""))
        parts.append(content.get("intro", ""))
        for s in content.get("sections", []) or []:
            parts.append(s.get("heading", ""))
            parts.append(s.get("summary", ""))
            for a in s.get("articles", []) or []:
                parts.append(a.get("blurb", ""))
                parts.append(a.get("why_it_matters", ""))
            parts.append(s.get("commentary", ""))
    return "\n\n".join(p for p in parts if p)


def extract_visuals(
    content: dict,
    article_summaries: list[dict],
    kind: str,
    client: anthropic.Anthropic,
    model: str,
) -> list[dict]:
    """Extract and synthesise visual elements from generated content.

    Returns [] on failure — content renders without visuals (graceful degradation).
    """
    content_text = _content_to_text(content, kind)
    if not content_text.strip():
        return []

    is_blog     = kind == "blog"
    max_visuals = _MAX_BLOG_VISUALS if is_blog else _MAX_NEWSLETTER_VISUALS
    system      = _SYSTEM_BLOG if is_blog else _SYSTEM_NEWSLETTER
    template    = _BLOG_TEMPLATE if is_blog else _NEWSLETTER_TEMPLATE

    prompt = template.format(
        max_visuals=max_visuals,
        content_text=content_text[:8000],
        articles_json=json.dumps(article_summaries, ensure_ascii=False, indent=2)[:3000],
    )
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
        data = parse_json_response(text)
    except (anthropic.APIError, json.JSONDecodeError, IndexError, AttributeError) as exc:
        log.warning("Visual extraction failed (non-fatal): %s", exc)
        return []

    elements = data.get("visual_elements") or []
    if not isinstance(elements, list):
        return []
    return elements[:max_visuals]
