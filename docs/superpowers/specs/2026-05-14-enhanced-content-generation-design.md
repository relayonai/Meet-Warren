# Enhanced Content Generation — Design Spec
**Date:** 2026-05-14
**Scope:** Blog post + newsletter generation pipeline
**Status:** Approved

---

## Goal

Deliver fully SEO/AEO-optimised, visually rich content in a single pipeline run — no post-hoc revision passes, no manual enrichment. The editor receives a finished piece ready to publish.

Two parallel improvements:
1. **SEO/AEO Agent** — baked into generation from the start, not bolted on after
2. **Visual Extraction Pass** — synthesises data from article content into styled visuals matched to each content type

---

## Pipeline Architecture

### Blog Post (4 passes)

```
Pass 1 — SEO/AEO Brief      src/seo_agent.py          (new)
Pass 2 — Outline            src/blog_generator.py     (existing, SEO-aware)
Pass 3 — Draft              src/blog_generator.py     (existing, AEO-native)
Pass 4 — Visual Extraction  src/visual_extractor.py   (new)
```

### Newsletter (2 passes)

```
Pass 1 — Draft              src/generator.py          (existing, light SEO signals)
Pass 2 — Visual Extraction  src/visual_extractor.py   (new, email-safe types only)
```

---

## Pass 1 — SEO/AEO Brief (`src/seo_agent.py`)

### Purpose
Fast, focused LLM call that runs before the outline. Produces a JSON contract consumed by every downstream pass as a directive.

### Inputs
- `article_summaries: list[dict]` — selected article records
- `editor_angle: str | None` — optional editorial framing
- `client`, `model` — standard Anthropic client

### Output schema
```json
{
  "primary_keyword": "string",
  "semantic_keywords": ["string", "..."],
  "target_h1": "string (40-60 chars, front-loaded keyword)",
  "faq_seeds": ["question phrased as real reader would search"],
  "aeo_signals": {
    "answer_first_targets": ["section headings that need a direct 1-sentence answer opener"],
    "speakable_candidates": ["short self-contained statements for voice/AI snippet extraction"],
    "citation_stats": ["stat + source to front-load per section, e.g. 'Inflation 2.6% (ONS, 2026)'"]
  },
  "schema_flags": ["FAQPage", "Speakable"],
  "meta_description_brief": "string (150-160 chars, includes one stat, ends with value prop)"
}
```

### Integration
The brief JSON is injected into both the outline prompt and draft prompt as a `★ SEO/AEO BRIEF` directive block — same pattern as the existing `★ EDITOR'S ANGLE` injection. Neither the outline nor draft prompts are restructured; the brief is prepended as a priority instruction.

The HTML renderer (`blog_to_html`) reads `schema_flags` from the brief and adds the relevant JSON-LD blocks (beyond the existing BlogPosting + FAQPage) to the `<head>`.

### Public API
```python
generate_seo_brief(
    article_summaries: list[dict],
    client: anthropic.Anthropic,
    model: str,
    *,
    editor_angle: str | None = None,
) -> dict | None
```
Returns `None` on parse failure — the pipeline falls back to running without a brief (graceful degradation, no hard stop).

---

## Pass 4 — Visual Extraction (`src/visual_extractor.py`)

Single module handles both blog and newsletter. Content type determines which visual types are permitted.

### Public API
```python
extract_visuals(
    content: dict,          # the generated blog/newsletter dict
    article_summaries: list[dict],
    kind: str,              # 'blog' | 'newsletter'
    client: anthropic.Anthropic,
    model: str,
) -> list[dict]             # visual_elements array, ready to merge into content
```

Returns an empty list on failure — content renders without visuals (graceful degradation).

---

## Visual Types — Blog

All rendered in `src/design_elements.py`. New types added alongside existing `render_table_html` and `render_chart_js`.

| Type | Renderer | Description |
|------|----------|-------------|
| `chart_bar` / `chart_line` / `chart_pie` | `render_chart_js()` (existing) | Chart.js canvas — time-series, magnitude, breakdowns |
| `table` | `render_table_html()` (existing) | Rate tables, allowance comparisons, fee schedules |
| `stat_card_row` | `render_stat_card_row()` (new) | Horizontal strip of 2–4 large-number callout cards |
| `comparison_card` | `render_comparison_card()` (new) | Interactive side-by-side card, 2–4 columns |
| `callout` | `render_callout()` (new) | Highlighted text block — key warnings, regulatory notes |

### `stat_card_row` schema
```json
{
  "type": "stat_card_row",
  "after_section": 0,
  "cards": [
    {"label": "Base Rate", "value": "5.25%", "note": "Bank of England, Mar 2026"},
    {"label": "ISA Allowance", "value": "£20,000", "note": "2025/26 tax year"}
  ]
}
```

### `comparison_card` schema
```json
{
  "type": "comparison_card",
  "after_section": 1,
  "title": "Cash ISA vs Stocks & Shares ISA",
  "columns": ["Feature", "Cash ISA", "Stocks & Shares ISA"],
  "rows": [
    ["Annual allowance", "£20,000", "£20,000"],
    ["Returns", "Fixed interest", "Market-dependent"],
    ["Risk", "Low", "Medium–High"]
  ]
}
```

### `callout` schema
```json
{
  "type": "callout",
  "after_section": 2,
  "icon": "⚠",
  "heading": "Regulatory note",
  "body": "string"
}
```

### Extraction rules enforced in prompt
- Only use figures explicitly present in the draft or source articles — never fabricate
- Maximum 4 visual elements per blog post
- Position by `after_section` index (0-based); `-1` = after intro
- All colours from Warren design system: navy `#0b2545`, gold `#c9a227`, soft background `#f6f8fb`

---

## Visual Types — Newsletter (Email-Safe)

All styles must be inline (`style=""`). No `<script>`, no `<canvas>`, no external URLs. Max table width 600px.

| Type | Renderer | Description |
|------|----------|-------------|
| `email_stat_row` | `render_email_stat_row()` (new) | Inline table of 2–4 stat cells — large number + label |
| `email_table` | `render_email_table()` (new) | Plain bordered HTML table, fully inline-styled |
| `email_divider_callout` | `render_email_divider_callout()` (new) | Styled blockquote-style box for key highlights |

Maximum 2 visual elements per newsletter — keep it simple, reduce rendering bugs.

---

## Changes to Existing Modules

### `src/blog_generator.py`
- `generate_blog_post()` receives an optional `seo_brief: dict | None` parameter
- If brief is present, inject `★ SEO/AEO BRIEF` block into outline prompt and draft prompt
- Remove `visual_elements` from the draft LLM prompt schema — the field is still present in the final post dict, but it is now populated exclusively by Pass 4 (visual extractor), not by the draft LLM
- `blog_to_html()` reads `schema_flags` from `seo_brief` (passed through the post dict) and adds extra JSON-LD blocks

### `src/generator.py`
- `generate_newsletter()` receives light SEO signals (primary keyword only) as a prompt hint — no full brief pass for newsletters
- Remove `visual_elements` from newsletter schema if present

### `src/design_elements.py`
- Add: `render_stat_card_row()`, `render_comparison_card()`, `render_callout()`
- Add: `render_email_stat_row()`, `render_email_table()`, `render_email_divider_callout()`
- Add: `render_email_visual()` — dispatcher for email-safe types

### `dashboard.py` (Create page pipeline)
- Insert SEO brief stage before outline: `seo_brief → collect → draft → verify → quality_loop → brand_review → compliance → export → quality → done`
- Insert visual extraction stage after draft: runs immediately after `draft`, before `verify`
- Update `_CP_STAGES` progress labels accordingly

---

## Error Handling & Degradation

| Failure point | Behaviour |
|---------------|-----------|
| SEO brief LLM call fails | `generate_seo_brief()` returns `None`; pipeline continues without brief |
| Visual extraction fails | `extract_visuals()` returns `[]`; content renders without visuals |
| Individual visual parse error | Skip that visual element, log warning, continue |
| Newsletter visual count > 2 | Truncate to first 2, log info |

---

## Out of Scope

- Press release / case study / landing page templates (separate spec)
- SEO performance tracking / analytics connector (Phase 3)
- Automated A/B testing of SEO variants
- Real-time keyword research via external API (future)
