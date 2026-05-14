# Enhanced Content Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the blog + newsletter pipeline to a 4-pass system that delivers SEO/AEO-optimised content with synthesised visual elements in a single run.

**Architecture:** Pass 1 produces a JSON SEO/AEO brief that shapes Passes 2–3 (outline + draft). Pass 4 mines the finished draft for data and synthesises rich visuals — Chart.js + interactive components for blogs, email-safe HTML-only elements for newsletters. Visual elements are populated by the extractor, not the draft LLM.

**Tech Stack:** Python 3.11, Anthropic SDK (`anthropic`), Plotly Dash, existing `src/_json.py` tolerant parser, `html.escape`, Chart.js 4.4 (CDN, already wired).

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `src/design_elements.py` | Add 3 blog renderers + 4 email-safe renderers |
| Create | `src/seo_agent.py` | Pass 1 — SEO/AEO brief generation |
| Create | `src/visual_extractor.py` | Pass 4 — visual element extraction + synthesis |
| Modify | `src/blog_generator.py` | Accept + inject `seo_brief`; remove `visual_elements` from draft schema |
| Modify | `src/generator.py` | Accept `seo_brief`; inject primary keyword hint |
| Modify | `src/formatter.py` | Inject email visual elements into newsletter HTML |
| Modify | `dashboard.py` | Add `seo_brief` + `visual_extract` pipeline stages |
| Create | `tests/test_design_elements.py` | Unit tests for renderers |
| Create | `tests/test_seo_agent.py` | Unit tests for SEO brief |
| Create | `tests/test_visual_extractor.py` | Unit tests for extractor |

---

## Task 1: Blog visual renderers in `src/design_elements.py`

**Files:**
- Modify: `src/design_elements.py` (append after `has_charts`)
- Create: `tests/test_design_elements.py`

Three new functions: `render_stat_card_row`, `render_comparison_card`, `render_callout`.

- [ ] **Step 1: Create the test file**

```python
# tests/test_design_elements.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.design_elements import (
    render_stat_card_row,
    render_comparison_card,
    render_callout,
    render_email_stat_row,
    render_email_table,
    render_email_divider_callout,
    render_email_visual,
)


# ── stat_card_row ────────────────────────────────────────────────────────────

def test_stat_card_row_renders_cards():
    ve = {
        "type": "stat_card_row",
        "after_section": 0,
        "cards": [
            {"label": "Base Rate", "value": "5.25%", "note": "BoE, Mar 2026"},
            {"label": "ISA Allowance", "value": "£20,000", "note": "2025/26"},
        ],
    }
    html = render_stat_card_row(ve)
    assert "5.25%" in html
    assert "Base Rate" in html
    assert "£20,000" in html
    assert "ISA Allowance" in html
    assert "BoE, Mar 2026" in html


def test_stat_card_row_empty_returns_empty():
    assert render_stat_card_row({"type": "stat_card_row", "cards": []}) == ""
    assert render_stat_card_row({}) == ""


# ── comparison_card ──────────────────────────────────────────────────────────

def test_comparison_card_renders_table():
    ve = {
        "type": "comparison_card",
        "after_section": 1,
        "title": "Cash ISA vs S&S ISA",
        "columns": ["Feature", "Cash ISA", "S&S ISA"],
        "rows": [
            ["Annual allowance", "£20,000", "£20,000"],
            ["Risk", "Low", "Medium–High"],
        ],
    }
    html = render_comparison_card(ve)
    assert "Cash ISA vs S&amp;S ISA" in html
    assert "Feature" in html
    assert "Annual allowance" in html
    assert "Medium" in html


def test_comparison_card_empty_returns_empty():
    assert render_comparison_card({"columns": [], "rows": []}) == ""


# ── callout ──────────────────────────────────────────────────────────────────

def test_callout_renders_body():
    ve = {
        "type": "callout",
        "after_section": 2,
        "icon": "⚠",
        "heading": "Regulatory note",
        "body": "This is not financial advice.",
    }
    html = render_callout(ve)
    assert "Regulatory note" in html
    assert "This is not financial advice." in html
    assert "⚠" in html


def test_callout_empty_body_returns_empty():
    assert render_callout({"icon": "⚠", "heading": "Note", "body": ""}) == ""
```

- [ ] **Step 2: Run tests to confirm they all fail**

```bash
cd "/Users/keremyilmaz/Warren Workflow" && source venv/bin/activate && python -m pytest tests/test_design_elements.py::test_stat_card_row_renders_cards tests/test_design_elements.py::test_comparison_card_renders_table tests/test_design_elements.py::test_callout_renders_body -v 2>&1 | head -30
```

Expected: `ImportError` — functions not defined yet.

- [ ] **Step 3: Add the three blog renderers to `src/design_elements.py`**

Append after the `has_charts` function (end of file):

```python
# ---------------------------------------------------------------------------
# Blog visual renderers — stat cards, comparison cards, callouts
# ---------------------------------------------------------------------------

def render_stat_card_row(ve: dict) -> str:
    """Render a horizontal strip of 2–4 large-number stat cards."""
    cards = ve.get("cards") or []
    if not cards:
        return ""
    card_html = ""
    for card in cards:
        label = escape(str(card.get("label", "")))
        value = escape(str(card.get("value", "")))
        note  = escape(str(card.get("note", "")))
        note_html = (
            f'<div style="font-size:11px;color:{MUTED};margin-top:4px;">{note}</div>'
            if note else ""
        )
        card_html += (
            f'<div style="flex:1;min-width:120px;text-align:center;padding:16px 12px;'
            f'background:#ffffff;border:1px solid {BORDER};border-radius:8px;">'
            f'<div style="font-size:11px;font-weight:700;letter-spacing:0.1em;'
            f'text-transform:uppercase;color:{MUTED};margin-bottom:6px;">{label}</div>'
            f'<div style="font-size:28px;font-weight:800;color:{NAVY};line-height:1;">{value}</div>'
            f'{note_html}'
            f'</div>'
        )
    return (
        f'<div style="margin:28px 0;padding:20px 22px;background:{SOFT_BG};'
        f'border:1px solid {BORDER};border-radius:10px;">'
        f'<div style="display:flex;flex-wrap:wrap;gap:12px;">'
        f'{card_html}'
        f'</div></div>'
    )


def render_comparison_card(ve: dict) -> str:
    """Render an interactive side-by-side comparison table."""
    title   = ve.get("title", "")
    columns = ve.get("columns") or []
    rows    = ve.get("rows") or []
    if not columns or not rows:
        return ""
    title_html = (
        f'<div style="font-size:12px;font-weight:700;letter-spacing:0.1em;'
        f'text-transform:uppercase;color:{MUTED};margin-bottom:12px;">{escape(title)}</div>'
        if title else ""
    )
    header_cells = "".join(
        f'<th style="padding:10px 14px;text-align:left;'
        f'background:{"#c9a227" if i > 0 else NAVY};color:#ffffff;'
        f'font-size:13px;font-weight:700;border:1px solid {BORDER};white-space:nowrap;">'
        f'{escape(str(c))}</th>'
        for i, c in enumerate(columns)
    )
    body_rows = ""
    for i, row in enumerate(rows):
        style = _TD_ALT if i % 2 else _TD_BASE
        cells = "".join(f'<td style="{style}">{escape(str(c))}</td>' for c in row)
        body_rows += f'<tr>{cells}</tr>'
    return (
        f'<div style="margin:28px 0;padding:20px 22px;background:{SOFT_BG};'
        f'border:1px solid {BORDER};border-radius:10px;">'
        f'{title_html}'
        f'<div style="overflow-x:auto;">'
        f'<table style="width:100%;border-collapse:collapse;">'
        f'<thead><tr>{header_cells}</tr></thead>'
        f'<tbody>{body_rows}</tbody>'
        f'</table></div></div>'
    )


def render_callout(ve: dict) -> str:
    """Render a highlighted callout box for warnings or key notes."""
    icon    = escape(str(ve.get("icon", "ℹ")))
    heading = escape(str(ve.get("heading", "")))
    body    = escape(str(ve.get("body", "")))
    if not body:
        return ""
    heading_html = (
        f'<div style="font-size:13px;font-weight:700;color:{NAVY};margin-bottom:6px;">'
        f'{icon} {heading}</div>'
        if heading
        else f'<div style="font-size:16px;margin-bottom:6px;">{icon}</div>'
    )
    return (
        f'<aside style="margin:24px 0;padding:18px 22px;'
        f'border-left:4px solid {ACCENT};background:#fdf6e3;border-radius:0 8px 8px 0;">'
        f'{heading_html}'
        f'<div style="font-size:15px;line-height:1.65;color:{INK};">{body}</div>'
        f'</aside>'
    )
```

- [ ] **Step 4: Run tests — expect all to pass**

```bash
cd "/Users/keremyilmaz/Warren Workflow" && source venv/bin/activate && python -m pytest tests/test_design_elements.py::test_stat_card_row_renders_cards tests/test_design_elements.py::test_stat_card_row_empty_returns_empty tests/test_design_elements.py::test_comparison_card_renders_table tests/test_design_elements.py::test_comparison_card_empty_returns_empty tests/test_design_elements.py::test_callout_renders_body tests/test_design_elements.py::test_callout_empty_body_returns_empty -v
```

Expected: `6 passed`.

- [ ] **Step 5: Update `_render_ves` dispatcher in `src/blog_generator.py` to handle new types**

In `blog_to_html()`, find the `_render_ves` inner function (around line 746) and add the three new types:

```python
# Existing in blog_generator.py — find _render_ves and extend it:
from .design_elements import (
    is_markdown_table, markdown_table_chunk_to_html,
    render_table_html, render_chart_js, has_charts, CHARTJS_CDN,
    render_stat_card_row, render_comparison_card, render_callout,   # ADD THIS LINE
)

# Inside _render_ves (around line 747-756):
def _render_ves(ves: list) -> str:
    out = ""
    for ve in ves:
        vtype = ve.get("type", "")
        if vtype == "table":
            out += render_table_html(ve)
        elif vtype.startswith("chart_"):
            cid = f"wc-chart-{_chart_counter[0]}"
            _chart_counter[0] += 1
            out += render_chart_js(ve, cid)
        elif vtype == "stat_card_row":
            out += render_stat_card_row(ve)
        elif vtype == "comparison_card":
            out += render_comparison_card(ve)
        elif vtype == "callout":
            out += render_callout(ve)
    return out
```

- [ ] **Step 6: Commit**

```bash
cd "/Users/keremyilmaz/Warren Workflow" && git add src/design_elements.py src/blog_generator.py tests/test_design_elements.py && git commit -m "feat: add blog visual renderers (stat card, comparison card, callout)"
```

---

## Task 2: Email-safe visual renderers in `src/design_elements.py`

**Files:**
- Modify: `src/design_elements.py` (append after Task 1 additions)
- Modify: `tests/test_design_elements.py` (tests already written in Task 1 — they cover these functions)

- [ ] **Step 1: Run the email renderer tests to confirm they fail**

```bash
cd "/Users/keremyilmaz/Warren Workflow" && source venv/bin/activate && python -m pytest tests/test_design_elements.py::test_email_stat_row_renders -v 2>&1 | head -20
```

Expected: `ImportError` or `FAILED`.

- [ ] **Step 2: Add the email-safe renderer tests to `tests/test_design_elements.py`**

Append to the existing test file:

```python
# ── email_stat_row ───────────────────────────────────────────────────────────

def test_email_stat_row_renders():
    ve = {
        "type": "email_stat_row",
        "after_section": -1,
        "cards": [
            {"label": "Inflation", "value": "2.6%", "note": "ONS, Mar 2026"},
            {"label": "Base Rate",  "value": "5.25%", "note": "BoE"},
        ],
    }
    html = render_email_stat_row(ve)
    assert "2.6%" in html
    assert "Inflation" in html
    assert "5.25%" in html
    assert "<script" not in html
    assert "<canvas" not in html


def test_email_stat_row_empty_returns_empty():
    assert render_email_stat_row({"cards": []}) == ""


# ── email_table ──────────────────────────────────────────────────────────────

def test_email_table_renders():
    ve = {
        "type": "email_table",
        "after_section": 0,
        "title": "Best-buy savings",
        "headers": ["Provider", "Rate"],
        "rows": [["Nationwide", "4.75%"], ["Barclays", "4.50%"]],
    }
    html = render_email_table(ve)
    assert "Nationwide" in html
    assert "4.75%" in html
    assert "<script" not in html
    assert 'style="' in html  # all styles inline


def test_email_table_empty_returns_empty():
    assert render_email_table({"headers": [], "rows": []}) == ""


# ── email_divider_callout ────────────────────────────────────────────────────

def test_email_divider_callout_renders():
    ve = {
        "type": "email_divider_callout",
        "after_section": 1,
        "heading": "Key takeaway",
        "body": "Rates are falling but mortgage costs remain high.",
    }
    html = render_email_divider_callout(ve)
    assert "Key takeaway" in html
    assert "Rates are falling" in html
    assert "<script" not in html


def test_email_divider_callout_empty_body_returns_empty():
    assert render_email_divider_callout({"heading": "Note", "body": ""}) == ""


# ── render_email_visual dispatcher ──────────────────────────────────────────

def test_render_email_visual_dispatches_correctly():
    assert render_email_visual({"type": "email_stat_row", "cards": []}) == ""
    assert render_email_visual({"type": "unknown_type"}) == ""
    ve = {"type": "email_divider_callout", "heading": "H", "body": "Body text here."}
    assert "Body text here." in render_email_visual(ve)
```

- [ ] **Step 3: Run new tests to confirm they fail**

```bash
cd "/Users/keremyilmaz/Warren Workflow" && source venv/bin/activate && python -m pytest tests/test_design_elements.py::test_email_stat_row_renders tests/test_design_elements.py::test_email_table_renders tests/test_design_elements.py::test_email_divider_callout_renders -v 2>&1 | head -20
```

Expected: `ImportError`.

- [ ] **Step 4: Add email-safe renderers to `src/design_elements.py`**

Append after the blog renderers added in Task 1:

```python
# ---------------------------------------------------------------------------
# Email-safe visual renderers — no JS, no Canvas, max-width 600px
# All styles must be inline. Safe for Outlook, Apple Mail, Gmail.
# ---------------------------------------------------------------------------

def render_email_stat_row(ve: dict) -> str:
    """Render a compact stat row as an inline HTML table (email-safe)."""
    cards = ve.get("cards") or []
    if not cards:
        return ""
    cells = ""
    for card in cards:
        label = escape(str(card.get("label", "")))
        value = escape(str(card.get("value", "")))
        note  = escape(str(card.get("note", "")))
        note_td = (
            f'<br><span style="font-size:10px;color:#5a6478;">{note}</span>'
            if note else ""
        )
        cells += (
            f'<td style="padding:14px 16px;text-align:center;border:1px solid #e6e9ef;'
            f'background:#ffffff;">'
            f'<div style="font-size:10px;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:0.08em;color:#5a6478;margin-bottom:4px;">{label}</div>'
            f'<div style="font-size:24px;font-weight:800;color:#0b2545;line-height:1;">'
            f'{value}</div>{note_td}'
            f'</td>'
        )
    return (
        f'<table width="100%" cellpadding="0" cellspacing="8" border="0" '
        f'style="margin:16px 0;max-width:600px;">'
        f'<tr>{cells}</tr>'
        f'</table>'
    )


def render_email_table(ve: dict) -> str:
    """Render a plain bordered comparison table (email-safe)."""
    title   = ve.get("title", "")
    headers = ve.get("headers") or []
    rows    = ve.get("rows") or []
    if not headers and not rows:
        return ""
    _TH = ("padding:8px 12px;text-align:left;background:#0b2545;color:#ffffff;"
           "font-size:12px;font-weight:700;border:1px solid #e6e9ef;")
    _TD = "padding:8px 12px;font-size:12px;color:#1a1f36;border:1px solid #e6e9ef;"
    _TD_ALT = _TD + "background:#f6f8fb;"
    title_html = (
        f'<div style="font-size:11px;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:0.08em;color:#5a6478;margin-bottom:8px;">{escape(title)}</div>'
        if title else ""
    )
    ths = "".join(f'<th style="{_TH}">{escape(str(h))}</th>' for h in headers)
    trs = ""
    for i, row in enumerate(rows):
        style = _TD_ALT if i % 2 else _TD
        cells = "".join(f'<td style="{style}">{escape(str(c))}</td>' for c in row)
        trs += f'<tr>{cells}</tr>'
    return (
        f'<div style="margin:16px 0;">'
        f'{title_html}'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="max-width:600px;border-collapse:collapse;">'
        f'<thead><tr>{ths}</tr></thead>'
        f'<tbody>{trs}</tbody>'
        f'</table></div>'
    )


def render_email_divider_callout(ve: dict) -> str:
    """Render a styled blockquote-style callout box (email-safe)."""
    heading = escape(str(ve.get("heading", "")))
    body    = escape(str(ve.get("body", "")))
    if not body:
        return ""
    heading_html = (
        f'<div style="font-size:13px;font-weight:700;color:#0b2545;margin-bottom:6px;">'
        f'{heading}</div>'
        if heading else ""
    )
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:16px 0;max-width:600px;">'
        f'<tr><td style="padding:14px 18px;border-left:4px solid #c9a227;'
        f'background:#fdf6e3;">'
        f'{heading_html}'
        f'<div style="font-size:14px;line-height:1.55;color:#1a1f36;">{body}</div>'
        f'</td></tr></table>'
    )


def render_email_visual(ve: dict) -> str:
    """Dispatcher for email-safe visual types."""
    vtype = ve.get("type", "")
    if vtype == "email_stat_row":
        return render_email_stat_row(ve)
    elif vtype == "email_table":
        return render_email_table(ve)
    elif vtype == "email_divider_callout":
        return render_email_divider_callout(ve)
    return ""
```

- [ ] **Step 5: Run all design_elements tests**

```bash
cd "/Users/keremyilmaz/Warren Workflow" && source venv/bin/activate && python -m pytest tests/test_design_elements.py -v
```

Expected: `14 passed`.

- [ ] **Step 6: Commit**

```bash
cd "/Users/keremyilmaz/Warren Workflow" && git add src/design_elements.py tests/test_design_elements.py && git commit -m "feat: add email-safe visual renderers (stat row, table, callout)"
```

---

## Task 3: SEO/AEO Brief module (`src/seo_agent.py`)

**Files:**
- Create: `src/seo_agent.py`
- Create: `tests/test_seo_agent.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_seo_agent.py
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch
from src.seo_agent import generate_seo_brief

_ARTICLES = [
    {
        "title": "Bank of England holds rates at 5.25%",
        "url": "https://bbc.co.uk/news/business/boe",
        "source": "BBC News",
        "summary": "The MPC voted to hold the base rate at 5.25% in March 2026.",
        "key_points": ["Rate held at 5.25%", "MPC split 6-3", "Next meeting May"],
        "excerpt": "The Bank of England kept rates on hold...",
    }
]

_VALID_BRIEF = {
    "primary_keyword": "Bank of England base rate 2026",
    "semantic_keywords": ["mortgage rates UK", "MPC decision"],
    "target_h1": "Bank of England Holds Rate at 5.25%: What It Means for UK Mortgages",
    "faq_seeds": ["Will the Bank of England cut rates in 2026?"],
    "aeo_signals": {
        "answer_first_targets": ["What did the MPC decide?"],
        "speakable_candidates": ["The Bank of England held rates at 5.25% in March 2026."],
        "citation_stats": ["Base rate 5.25% (BoE, Mar 2026)"],
    },
    "schema_flags": ["FAQPage", "Speakable"],
    "meta_description_brief": "The Bank of England held rates at 5.25% in March 2026. Here is what it means for mortgage holders and savers.",
}


def _mock_client(response_text: str):
    msg = MagicMock()
    msg.content = [MagicMock(text=response_text)]
    client = MagicMock()
    client.messages.create.return_value = msg
    return client


def test_generate_seo_brief_returns_dict():
    client = _mock_client(json.dumps(_VALID_BRIEF))
    result = generate_seo_brief(_ARTICLES, client, "claude-sonnet-4-5")
    assert isinstance(result, dict)
    assert result["primary_keyword"] == "Bank of England base rate 2026"
    assert "semantic_keywords" in result
    assert "aeo_signals" in result
    assert "schema_flags" in result


def test_generate_seo_brief_injects_editor_angle():
    client = _mock_client(json.dumps(_VALID_BRIEF))
    generate_seo_brief(_ARTICLES, client, "claude-sonnet-4-5", editor_angle="Focus on first-time buyers")
    call_kwargs = client.messages.create.call_args
    prompt = call_kwargs[1]["messages"][0]["content"]
    assert "Focus on first-time buyers" in prompt


def test_generate_seo_brief_returns_none_on_api_error():
    client = MagicMock()
    import anthropic
    client.messages.create.side_effect = anthropic.APIError("boom", request=MagicMock(), body=None)
    result = generate_seo_brief(_ARTICLES, client, "claude-sonnet-4-5")
    assert result is None


def test_generate_seo_brief_returns_none_on_bad_json():
    client = _mock_client("not valid json {{{")
    result = generate_seo_brief(_ARTICLES, client, "claude-sonnet-4-5")
    assert result is None


def test_generate_seo_brief_returns_none_for_empty_articles():
    client = _mock_client(json.dumps(_VALID_BRIEF))
    result = generate_seo_brief([], client, "claude-sonnet-4-5")
    assert result is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd "/Users/keremyilmaz/Warren Workflow" && source venv/bin/activate && python -m pytest tests/test_seo_agent.py -v 2>&1 | head -20
```

Expected: `ImportError` — module does not exist yet.

- [ ] **Step 3: Create `src/seo_agent.py`**

```python
"""SEO/AEO Brief — Pass 1 of the blog generation pipeline.

Analyses selected article summaries and returns a JSON contract consumed
by Passes 2 (outline) and 3 (draft) as a priority directive.

Public API:
    generate_seo_brief(article_summaries, client, model, *, editor_angle=None) -> dict | None
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import anthropic

from ._json import parse_json_response

log = logging.getLogger(__name__)

_SYSTEM = (
    "You are an SEO and AEO (Answer Engine Optimisation) strategist for Warren, "
    "a UK personal finance publisher. You analyse article data and produce a concise "
    "JSON brief that shapes a long-form blog post to rank on Google and be cited by "
    "AI answer engines (Perplexity, ChatGPT Search, Google AI Overviews). "
    "Return ONLY valid JSON. No prose, no markdown fences."
)

_TEMPLATE = """Analyse the article records below and produce a SEO/AEO brief for a UK personal-finance blog post.

{angle_note}

Return ONLY a JSON object matching this schema exactly:
{{
  "primary_keyword": "string (main search term, 2-5 words, UK English, e.g. 'ISA allowance 2026')",
  "semantic_keywords": ["string"],
  "target_h1": "string (40-60 chars, primary keyword front-loaded)",
  "faq_seeds": [
    "string (real question a UK reader would search, e.g. 'How much can I put in an ISA in 2026?')"
  ],
  "aeo_signals": {{
    "answer_first_targets": [
      "string (section heading that needs a direct 1-sentence answer opener)"
    ],
    "speakable_candidates": [
      "string (short self-contained statement for voice/AI extraction, <= 25 words)"
    ],
    "citation_stats": [
      "string (specific stat + source, e.g. 'Inflation fell to 2.6% (ONS, Mar 2026)')"
    ]
  }},
  "schema_flags": ["FAQPage", "Speakable"],
  "meta_description_brief": "string (150-160 chars, one stat, ends with value prop)"
}}

Rules:
- primary_keyword: highest-traffic term this post should rank for
- semantic_keywords: 4-7 related LSI terms (no repeats of primary)
- faq_seeds: 3-4 questions phrased exactly as a UK reader would type into Google
- aeo_signals.answer_first_targets: 2-3 section headings needing answer-first treatment
- aeo_signals.speakable_candidates: 2-3 short statements ideal for voice search
- aeo_signals.citation_stats: 2-4 specific stats with inline attribution
- schema_flags: include "FAQPage" if faq_seeds >= 3, always include "Speakable"

Article records:
{articles_json}
"""


def generate_seo_brief(
    article_summaries: list[dict],
    client: anthropic.Anthropic,
    model: str,
    *,
    editor_angle: Optional[str] = None,
) -> Optional[dict]:
    """Generate an SEO/AEO brief from article summaries.

    Returns None on failure — the pipeline degrades gracefully.
    """
    if not article_summaries:
        return None

    angle_note = ""
    if editor_angle and editor_angle.strip():
        angle_note = (
            f"★ EDITOR'S ANGLE (brief must serve this framing): "
            f"{editor_angle.strip()}"
        )

    prompt = _TEMPLATE.format(
        angle_note=angle_note,
        articles_json=json.dumps(article_summaries, ensure_ascii=False, indent=2),
    )
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=1500,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
        brief = parse_json_response(text)
    except (anthropic.APIError, json.JSONDecodeError, IndexError, AttributeError) as exc:
        log.warning("SEO brief generation failed (will proceed without): %s", exc)
        return None

    if not isinstance(brief, dict) or "primary_keyword" not in brief:
        log.warning("SEO brief missing required keys — skipping.")
        return None
    return brief
```

- [ ] **Step 4: Run tests — expect all to pass**

```bash
cd "/Users/keremyilmaz/Warren Workflow" && source venv/bin/activate && python -m pytest tests/test_seo_agent.py -v
```

Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
cd "/Users/keremyilmaz/Warren Workflow" && git add src/seo_agent.py tests/test_seo_agent.py && git commit -m "feat: add SEO/AEO brief generator (Pass 1)"
```

---

## Task 4: Visual extractor module (`src/visual_extractor.py`)

**Files:**
- Create: `src/visual_extractor.py`
- Create: `tests/test_visual_extractor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_visual_extractor.py
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock
from src.visual_extractor import extract_visuals, _content_to_text

_BLOG = {
    "title": "ISA Allowance 2026: What You Need to Know",
    "intro": "The ISA allowance stays at £20,000 for the 2026/27 tax year.",
    "sections": [
        {"heading": "Cash ISA vs S&S ISA", "content": "Cash ISAs offer 4.75% AER from leading providers."},
        {"heading": "What the rate hold means", "content": "With rates at 5.25%, savers benefit but borrowers pay more."},
    ],
    "conclusion": "Act before the April deadline to maximise your allowance.",
}

_NEWSLETTER = {
    "subject_line": "Rate hold, ISA deadline, spring budget fallout",
    "intro": "The Bank of England held rates at 5.25%.",
    "sections": [
        {
            "heading": "Savings",
            "summary": "Rates remain high for cash savers.",
            "articles": [
                {"blurb": "NS&I Premium Bond rate 4.4%.", "why_it_matters": "Best easy-access rate in a decade."}
            ],
            "commentary": "Consider locking in before cuts arrive.",
        }
    ],
}

_ARTICLES = [{"title": "BoE rate hold", "summary": "BoE held at 5.25%", "excerpt": ""}]

_BLOG_VISUALS = {
    "visual_elements": [
        {"type": "stat_card_row", "after_section": -1,
         "cards": [{"label": "ISA Allowance", "value": "£20,000", "note": "2026/27"}]},
        {"type": "comparison_card", "after_section": 0, "title": "ISA types",
         "columns": ["Feature", "Cash ISA", "S&S ISA"],
         "rows": [["Allowance", "£20,000", "£20,000"]]},
    ]
}

_EMAIL_VISUALS = {
    "visual_elements": [
        {"type": "email_stat_row", "after_section": -1,
         "cards": [{"label": "Base Rate", "value": "5.25%", "note": "BoE, Mar 2026"}]},
    ]
}


def _mock_client(response_json: dict):
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps(response_json))]
    client = MagicMock()
    client.messages.create.return_value = msg
    return client


def test_extract_visuals_blog_returns_list():
    client = _mock_client(_BLOG_VISUALS)
    result = extract_visuals(_BLOG, _ARTICLES, "blog", client, "claude-sonnet-4-5")
    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["type"] == "stat_card_row"


def test_extract_visuals_newsletter_returns_list():
    client = _mock_client(_EMAIL_VISUALS)
    result = extract_visuals(_NEWSLETTER, _ARTICLES, "newsletter", client, "claude-sonnet-4-5")
    assert isinstance(result, list)
    assert result[0]["type"] == "email_stat_row"


def test_extract_visuals_caps_blog_at_four():
    many = {"visual_elements": [{"type": "callout", "after_section": i, "body": f"body {i}"} for i in range(10)]}
    client = _mock_client(many)
    result = extract_visuals(_BLOG, _ARTICLES, "blog", client, "claude-sonnet-4-5")
    assert len(result) <= 4


def test_extract_visuals_caps_newsletter_at_two():
    many = {"visual_elements": [{"type": "email_stat_row", "after_section": i, "cards": []} for i in range(5)]}
    client = _mock_client(many)
    result = extract_visuals(_NEWSLETTER, _ARTICLES, "newsletter", client, "claude-sonnet-4-5")
    assert len(result) <= 2


def test_extract_visuals_returns_empty_on_api_error():
    import anthropic
    client = MagicMock()
    client.messages.create.side_effect = anthropic.APIError("boom", request=MagicMock(), body=None)
    result = extract_visuals(_BLOG, _ARTICLES, "blog", client, "claude-sonnet-4-5")
    assert result == []


def test_content_to_text_blog():
    text = _content_to_text(_BLOG, "blog")
    assert "ISA Allowance 2026" in text
    assert "Cash ISA vs S&S ISA" in text
    assert "April deadline" in text


def test_content_to_text_newsletter():
    text = _content_to_text(_NEWSLETTER, "newsletter")
    assert "Rate hold" in text
    assert "NS&I Premium Bond" in text
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd "/Users/keremyilmaz/Warren Workflow" && source venv/bin/activate && python -m pytest tests/test_visual_extractor.py -v 2>&1 | head -20
```

Expected: `ImportError`.

- [ ] **Step 3: Create `src/visual_extractor.py`**

```python
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
from typing import Optional

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
```

- [ ] **Step 4: Run tests — expect all to pass**

```bash
cd "/Users/keremyilmaz/Warren Workflow" && source venv/bin/activate && python -m pytest tests/test_visual_extractor.py -v
```

Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
cd "/Users/keremyilmaz/Warren Workflow" && git add src/visual_extractor.py tests/test_visual_extractor.py && git commit -m "feat: add visual extraction pass (Pass 4)"
```

---

## Task 5: Update `src/blog_generator.py`

**Files:**
- Modify: `src/blog_generator.py`

Three changes:
1. Remove `visual_elements` from the draft LLM JSON schema in `USER_TEMPLATE`
2. Add `_seo_brief_block()` helper + inject brief into outline and draft prompts
3. Add `seo_brief` parameter to `generate_blog_post()`

- [ ] **Step 1: Remove `visual_elements` from `USER_TEMPLATE`**

In `src/blog_generator.py`, find lines 105–119 and replace:

```python
# FIND (lines 105-119):
  "seo_tags": ["string", "..."],
  "visual_elements": []
  // 0–3 data visuals to place inline with sections. Leave [] when article data
  // doesn't support a genuine visual. NEVER fabricate numbers — only cite figures
  // explicitly present in the input records.
  //
  // Table shape (for rate/fee/allowance comparisons):
  // {{"after_section":0, "type":"table", "title":"string", "headers":["Col A","Col B"], "rows":[["v","v"]]}}
  //
  // Chart shape (for time-series or magnitude comparisons):
  // {{"after_section":1, "type":"chart_bar|chart_line|chart_pie",
  //   "title":"string", "labels":["Jan 25"], "values":[5.25], "unit":"% AER"}}
  //
  // after_section: 0-based section index to insert after (-1 = after intro)
  ,
  "hero_image_prompt":

# REPLACE WITH:
  "seo_tags": ["string", "..."],
  "hero_image_prompt":
```

- [ ] **Step 2: Add `_seo_brief_block()` helper**

Add this function after `_outline_to_prompt_block` (around line 344):

```python
def _seo_brief_block(brief: Optional[dict]) -> str:
    """Format the SEO/AEO brief as a priority directive block for prompts."""
    if not brief:
        return ""
    kw       = brief.get("primary_keyword", "")
    h1       = brief.get("target_h1", "")
    semantic = ", ".join(brief.get("semantic_keywords", []))
    meta     = brief.get("meta_description_brief", "")
    faqs     = "\n".join(f"  - {q}" for q in brief.get("faq_seeds", []))
    aeo      = brief.get("aeo_signals", {}) or {}
    ans_first = "\n".join(f"  - {s}" for s in aeo.get("answer_first_targets", []))
    speakable = "\n".join(f"  - {s}" for s in aeo.get("speakable_candidates", []))
    stats     = "\n".join(f"  - {s}" for s in aeo.get("citation_stats", []))
    return (
        f"\n★ SEO/AEO BRIEF (priority directive — every section serves this):\n"
        f"Primary keyword: {kw}\n"
        f"Target H1: {h1}\n"
        f"Semantic keywords: {semantic}\n"
        f"Meta description brief: {meta}\n"
        f"FAQ seeds (use these verbatim as faqs):\n{faqs}\n"
        f"Answer-first sections (open each with a 1-sentence direct answer):\n{ans_first}\n"
        f"Speakable candidates (include near-verbatim):\n{speakable}\n"
        f"Stats to cite inline:\n{stats}\n"
    )
```

- [ ] **Step 3: Add `seo_brief` parameter to `_outline_blog_post()` and inject it**

Find the `_outline_blog_post` signature (around line 269) and update:

```python
# FIND:
def _outline_blog_post(
    article_summaries: List[dict],
    client: anthropic.Anthropic,
    model: str,
    *,
    editor_angle: Optional[str],
    today_human: str,
) -> Optional[dict]:

# REPLACE WITH:
def _outline_blog_post(
    article_summaries: List[dict],
    client: anthropic.Anthropic,
    model: str,
    *,
    editor_angle: Optional[str],
    today_human: str,
    seo_brief: Optional[dict] = None,
) -> Optional[dict]:
```

Inside `_outline_blog_post`, find where `angle_note` is built and prepend the SEO block:

```python
# FIND (around line 279-283):
    angle_note = ""
    if editor_angle and editor_angle.strip():
        angle_note = (
            f"★ EDITOR'S ANGLE (the outline must serve this lens): "
            f"{editor_angle.strip()}\n"
        )

# REPLACE WITH:
    angle_note = _seo_brief_block(seo_brief)
    if editor_angle and editor_angle.strip():
        angle_note += (
            f"★ EDITOR'S ANGLE (the outline must serve this lens): "
            f"{editor_angle.strip()}\n"
        )
```

- [ ] **Step 4: Add `seo_brief` parameter to `generate_blog_post()` and wire it**

Find the `generate_blog_post` signature (around line 347) and add the parameter:

```python
# FIND:
def generate_blog_post(
    article_summaries: List[dict],
    client: anthropic.Anthropic,
    model: str,
    *,
    existing_posts: Optional[List[dict]] = None,
    editor_angle: Optional[str] = None,
    progress_cb=None,
) -> Optional[dict]:

# REPLACE WITH:
def generate_blog_post(
    article_summaries: List[dict],
    client: anthropic.Anthropic,
    model: str,
    *,
    existing_posts: Optional[List[dict]] = None,
    editor_angle: Optional[str] = None,
    seo_brief: Optional[dict] = None,
    progress_cb=None,
) -> Optional[dict]:
```

Inside `generate_blog_post`, find where `angle_note` is built (around line 378) and prepend the SEO block:

```python
# FIND:
    angle_note = ""
    if editor_angle and editor_angle.strip():
        angle_note = (
            "\n★ EDITOR'S ANGLE (priority framing — drive the title, intro, and "
            f"section selection from this lens): {editor_angle.strip()}\n"
        )

# REPLACE WITH:
    angle_note = _seo_brief_block(seo_brief)
    if editor_angle and editor_angle.strip():
        angle_note += (
            "\n★ EDITOR'S ANGLE (priority framing — drive the title, intro, and "
            f"section selection from this lens): {editor_angle.strip()}\n"
        )
```

Find the outline call (around line 393) and pass `seo_brief`:

```python
# FIND:
    outline = _outline_blog_post(
        article_summaries, client, model,
        editor_angle=editor_angle, today_human=today_human,
    )

# REPLACE WITH:
    outline = _outline_blog_post(
        article_summaries, client, model,
        editor_angle=editor_angle, today_human=today_human,
        seo_brief=seo_brief,
    )
```

Store brief in post dict so `blog_to_html` can read `schema_flags`:

```python
# FIND (around line 446):
    if outline:
        # Stash the outline so the audit JSON can show how the post was planned.
        data["_outline"] = outline
    return data

# REPLACE WITH:
    if outline:
        data["_outline"] = outline
    if seo_brief:
        data["_seo_brief"] = seo_brief
    return data
```

- [ ] **Step 5: Update `blog_to_html()` to add Speakable JSON-LD when flagged**

Find `_seo_head()` (around line 682) and extend the JSON-LD generation:

```python
# In _seo_head(post), find where ld_blocks is built (around line 709):
    ld_blocks = "".join(
        f'\n  <script type="application/ld+json">{json.dumps(d, ensure_ascii=False)}</script>'
        for d in build_jsonld(post)
    )

# REPLACE WITH:
    schema_flags = (post.get("_seo_brief") or {}).get("schema_flags") or []
    ld_list = build_jsonld(post)
    if "Speakable" in schema_flags:
        ld_list.append({
            "@context": "https://schema.org",
            "@type": "SpeakableSpecification",
            "cssSelector": [".tldr", "h1", "h2"],
        })
    ld_blocks = "".join(
        f'\n  <script type="application/ld+json">{json.dumps(d, ensure_ascii=False)}</script>'
        for d in ld_list
    )
```

- [ ] **Step 6: Smoke-test the import**

```bash
cd "/Users/keremyilmaz/Warren Workflow" && source venv/bin/activate && python -c "from src.blog_generator import generate_blog_post, _seo_brief_block; print('OK')"
```

Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
cd "/Users/keremyilmaz/Warren Workflow" && git add src/blog_generator.py && git commit -m "feat: inject SEO/AEO brief into blog outline + draft; remove visual_elements from LLM schema"
```

---

## Task 6: Update `src/generator.py` + `src/formatter.py`

**Files:**
- Modify: `src/generator.py` — accept `seo_brief`, inject primary keyword hint
- Modify: `src/formatter.py` — inject email visual elements into newsletter HTML

### Part A — `src/generator.py`

- [ ] **Step 1: Add `seo_brief` parameter to `generate_newsletter()`**

Find the `USER_TEMPLATE` string (around line 65) and add a `{seo_note}` slot after `{angle_note}`:

```python
# FIND (around line 71-73 in USER_TEMPLATE):
{diversity_note}{angle_note}
Today's edition date is: {today_human}.

# REPLACE WITH:
{diversity_note}{angle_note}{seo_note}
Today's edition date is: {today_human}.
```

Find `generate_newsletter` signature (around line 117) and add `seo_brief`:

```python
# FIND:
def generate_newsletter(
    article_summaries: List[dict], client: anthropic.Anthropic, model: str,
    *, editor_angle: Optional[str] = None,
) -> Optional[dict]:

# REPLACE WITH:
def generate_newsletter(
    article_summaries: List[dict], client: anthropic.Anthropic, model: str,
    *, editor_angle: Optional[str] = None, seo_brief: Optional[dict] = None,
) -> Optional[dict]:
```

Inside `generate_newsletter`, before `prompt = USER_TEMPLATE.format(...)`, add:

```python
    seo_note = ""
    if seo_brief and seo_brief.get("primary_keyword"):
        seo_note = (
            f"\n★ SEO HINT: Primary keyword for this edition: "
            f"'{seo_brief['primary_keyword']}'. "
            f"Use it naturally in subject_line and intro where appropriate.\n"
        )
```

Add `seo_note=seo_note` to the `USER_TEMPLATE.format(...)` call.

### Part B — `src/formatter.py`

- [ ] **Step 2: Add email visual injection to `to_html()`**

Add the import at the top of `formatter.py`:

```python
from .design_elements import render_email_visual
```

Find the sections loop (around line 253):

```python
# FIND:
    sections_html = "".join(_section_block(s) for s in newsletter.get("sections", []))

# REPLACE WITH:
    _email_ves = newsletter.get("visual_elements") or []
    _ve_by_section: dict[int, list] = {}
    _ve_pre: list = []
    for _ve in _email_ves:
        _ai = _ve.get("after_section")
        try:
            _ai = int(_ai)
        except (TypeError, ValueError):
            continue
        if _ai == -1:
            _ve_pre.append(_ve)
        else:
            _ve_by_section.setdefault(_ai, []).append(_ve)

    _pre_html = "".join(render_email_visual(v) for v in _ve_pre)
    sections_html = ""
    for _i, _s in enumerate(newsletter.get("sections", []) or []):
        sections_html += _section_block(_s)
        for _v in _ve_by_section.get(_i, []):
            sections_html += render_email_visual(_v)
    sections_html = _pre_html + sections_html
```

- [ ] **Step 3: Smoke-test the imports**

```bash
cd "/Users/keremyilmaz/Warren Workflow" && source venv/bin/activate && python -c "from src.generator import generate_newsletter; from src.formatter import to_html; print('OK')"
```

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
cd "/Users/keremyilmaz/Warren Workflow" && git add src/generator.py src/formatter.py && git commit -m "feat: inject SEO hint into newsletter; render email visuals in formatter"
```

---

## Task 7: Wire pipeline in `dashboard.py`

**Files:**
- Modify: `dashboard.py`

Add two new pipeline stages (`seo_brief`, `visual_extract`) and wire the new module calls into `_run_generation_job`.

- [ ] **Step 1: Add imports at the top of `dashboard.py`**

Find the existing import block (around lines 21-39) and add:

```python
from src.seo_agent import generate_seo_brief
from src.visual_extractor import extract_visuals
```

- [ ] **Step 2: Update `_CR_STAGES`**

Find `_CR_STAGES` (around line 989) and replace:

```python
# FIND:
_CR_STAGES = [
    ("collect",      "Loading article context"),
    ("draft",        "Drafting with Claude"),
    ("verify",       "Verifying source URLs"),
    ("quality_loop",  "Quality-revision loop (blog only)"),
    ("brand_review",  "Brand voice audit"),
    ("compliance",    "Compliance grading + revision"),
    ("export",       "Rendering PDF, DOCX, Markdown, EML"),
    ("quality",      "Final 100-pt rubric score"),
    ("done",         "Done"),
]

# REPLACE WITH:
_CR_STAGES = [
    ("collect",         "Loading article context"),
    ("seo_brief",       "SEO/AEO brief (Pass 1)"),
    ("draft",           "Drafting with Claude (Pass 2–3)"),
    ("visual_extract",  "Extracting visual elements (Pass 4)"),
    ("verify",          "Verifying source URLs"),
    ("quality_loop",    "Quality-revision loop (blog only)"),
    ("brand_review",    "Brand voice audit"),
    ("compliance",      "Compliance grading + revision"),
    ("export",          "Rendering PDF, DOCX, Markdown, EML"),
    ("quality",         "Final 100-pt rubric score"),
    ("done",            "Done"),
]
```

- [ ] **Step 3: Add SEO brief stage to `_run_generation_job`**

Find `_run_generation_job` (around line 1137). After `client = build_anthropic_client(cfg)` and before `_set_stage(job_id, "draft", ...)`, insert:

```python
        # --- Pass 1: SEO/AEO brief -------------------------------------------
        _set_stage(job_id, "seo_brief", sub="analysing keyword + AEO signals")
        seo_brief = None
        try:
            seo_brief = generate_seo_brief(
                summaries, client, cfg.anthropic_model,
                editor_angle=editor_angle,
            )
        except Exception as e:
            print(f"SEO brief failed (non-fatal): {e}")
```

- [ ] **Step 4: Pass `seo_brief` to blog + newsletter generators**

Find the newsletter call (around line 1149):

```python
# FIND:
            result = generate_newsletter(summaries, client, cfg.anthropic_model,
                                          editor_angle=editor_angle)

# REPLACE WITH:
            result = generate_newsletter(summaries, client, cfg.anthropic_model,
                                          editor_angle=editor_angle,
                                          seo_brief=seo_brief)
```

Find the blog call (around line 1162):

```python
# FIND:
            result = generate_blog_post(
                summaries, client, cfg.anthropic_model,
                existing_posts=existing_corpus,
                editor_angle=editor_angle,
                progress_cb=lambda s: _set_stage(job_id, "draft", sub=s),
            )

# REPLACE WITH:
            result = generate_blog_post(
                summaries, client, cfg.anthropic_model,
                existing_posts=existing_corpus,
                editor_angle=editor_angle,
                seo_brief=seo_brief,
                progress_cb=lambda s: _set_stage(job_id, "draft", sub=s),
            )
```

- [ ] **Step 5: Add visual extraction stage after draft**

Find the source verification block (around line 1177: `# --- Source verification`). Insert the visual extraction stage **before** it:

```python
        # --- Pass 4: Visual extraction ---------------------------------------
        _set_stage(job_id, "visual_extract",
                   sub=f"mining data from {kind} for visual elements")
        try:
            visual_elements = extract_visuals(
                result, summaries, kind, client, cfg.anthropic_model,
            )
            if visual_elements:
                result["visual_elements"] = visual_elements
                if kind == "blog":
                    out_html = blog_to_html(result)
                    out_text = blog_to_text(result)
                else:
                    from src.formatter import to_html as nl_to_html, to_text as nl_to_text
                    out_html = nl_to_html(result)
                    out_text = nl_to_text(result)
        except Exception as e:
            print(f"Visual extraction failed (non-fatal): {e}")
```

- [ ] **Step 6: Smoke-test the dashboard imports**

```bash
cd "/Users/keremyilmaz/Warren Workflow" && source venv/bin/activate && python -c "import dashboard; print('dashboard imports OK')"
```

Expected: `dashboard imports OK`.

- [ ] **Step 7: Start the dashboard and verify the new pipeline stages appear**

```bash
cd "/Users/keremyilmaz/Warren Workflow" && source venv/bin/activate && python dashboard.py
```

Open `http://127.0.0.1:8050/create`. Select 1+ articles, pick Blog Post, click Generate. Confirm the progress bar shows:
- `SEO/AEO brief (Pass 1)`
- `Drafting with Claude (Pass 2–3)`
- `Extracting visual elements (Pass 4)`

- [ ] **Step 8: Commit**

```bash
cd "/Users/keremyilmaz/Warren Workflow" && git add dashboard.py && git commit -m "feat: wire SEO brief + visual extraction stages into generation pipeline"
```

---

## Task 8: Run full test suite and push

- [ ] **Step 1: Run all tests**

```bash
cd "/Users/keremyilmaz/Warren Workflow" && source venv/bin/activate && python -m pytest tests/ -v
```

Expected: all tests pass. Fix any failures before proceeding.

- [ ] **Step 2: Push feature branch**

```bash
cd "/Users/keremyilmaz/Warren Workflow" && git push origin feature/enhanced-content-generation
```

- [ ] **Step 3: Open a pull request**

```bash
gh pr create \
  --title "feat: 4-pass content pipeline with SEO/AEO agent + visual extraction" \
  --body "$(cat <<'EOF'
## Summary
- Pass 1: SEO/AEO brief (`src/seo_agent.py`) — primary keyword, FAQ seeds, AEO answer-first signals
- Pass 4: Visual extraction (`src/visual_extractor.py`) — mines draft for data, synthesises blog or email-safe visuals
- New blog renderers: stat_card_row, comparison_card, callout (`src/design_elements.py`)
- New email-safe renderers: email_stat_row, email_table, email_divider_callout
- `visual_elements` removed from draft LLM schema — now populated exclusively by Pass 4
- Dashboard pipeline: 2 new stages (seo_brief, visual_extract)

## Test plan
- [ ] Run `pytest tests/` — all tests pass
- [ ] Generate a blog post end-to-end — confirm SEO/AEO BRIEF stage completes
- [ ] Confirm visual elements appear in generated HTML (stat cards, comparison tables)
- [ ] Generate a newsletter — confirm email-safe visuals render without `<script>` tags
- [ ] Verify graceful degradation: pull network plug before SEO brief call, confirm pipeline continues
EOF
)"
```
