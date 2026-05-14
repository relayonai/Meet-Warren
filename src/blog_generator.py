from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from html import escape
from typing import List, Optional

import anthropic

from ._json import parse_json_response
from .brand_voice import voice_block
from .design_elements import (
    is_markdown_table, markdown_table_chunk_to_html,
    render_table_html, render_chart_js, has_charts, CHARTJS_CDN,
)

log = logging.getLogger(__name__)


def _diversity_warning_blog(articles: List[dict]) -> Optional[str]:
    if not articles:
        return None
    counts = Counter((a.get("source") or "Unknown") for a in articles)
    top, n = counts.most_common(1)[0]
    if n / max(len(articles), 1) > 0.5 and len(articles) >= 4:
        return (
            f"Source balance: {n}/{len(articles)} of the candidate articles come from "
            f"{top}. Treat that outlet as ONE perspective and explicitly attribute "
            f"each fact to its source so the analysis isn't a paraphrase of one outlet."
        )
    return None


def _word_count(post: dict) -> int:
    chunks = [post.get("intro", ""), post.get("conclusion", "")]
    chunks += [s.get("content", "") for s in post.get("sections", []) or []]
    chunks += [s.get("pull_quote", "") for s in post.get("sections", []) or []]
    chunks += [t for t in (post.get("key_takeaways") or [])]
    chunks += [(f.get("question", "") + " " + f.get("answer", ""))
               for f in (post.get("faqs") or [])]
    text = " ".join(c for c in chunks if c)
    return len(re.findall(r"\w+", text))


def _compute_reading_time(post: dict) -> int:
    """Reading time at 220 wpm, clamped to [3, 25] minutes."""
    wc = _word_count(post)
    return max(3, min(25, round(wc / 220)))

_JOURNALIST_PERSONA = (
    "You are a senior UK personal-finance journalist with 15 years at outlets like the FT and MoneyWeek. "
    "You synthesise — never paraphrase — and your analysis carries practical implications "
    "for British readers managing mortgages, ISAs, pensions, tax, and savings. "
    "You return ONLY valid JSON. No markdown fences, no prose outside the JSON object."
)


def _system_prompt() -> str:
    """Brand-voice prelude + journalist persona. Resolved at call time so KB
    edits propagate without a process restart."""
    return voice_block(include_past_replies=True, max_replies=6) + "\n\n" + _JOURNALIST_PERSONA


# Kept for backward compat — anything importing SYSTEM_PROMPT gets the
# journalist persona only (without the dynamic voice block).
SYSTEM_PROMPT = _JOURNALIST_PERSONA

USER_TEMPLATE = """Write a long-form UK personal-finance blog post synthesising the article records below.

Each input article carries: title, url, source, published_at, category, relevance_score,
a `summary`, `key_points` (3-5 bullets), and an `excerpt` (raw text from the original).
USE the key_points and excerpt — synthesise across them, do not paraphrase a single
summary. Attribute facts to their original source.

{diversity_note}{angle_note}
Today's publication date is: {today_human}.

Return a JSON object that EXACTLY matches this schema (do not add or omit keys):
{{
  "title":    "string (compelling SEO-friendly headline, <= 90 chars)",
  "subtitle": "string (supporting deck / dek, <= 160 chars)",
  "meta_description": "string (140-160 chars, used for <meta name=description> and OpenGraph)",
  "byline":   "string (e.g. 'By the Warren Editorial Desk')",
  "reading_time_minutes": integer (will be overwritten server-side from word count — give your best guess),
  "key_takeaways": ["string", "..."]   // 3-5 punchy bullets the reader gets in 30 seconds
  ,
  "intro":    "string (2-3 paragraphs setting context, separated by \\n\\n)",
  "sections": [
    {{
      "heading": "string (clear H2-level subheading)",
      "content": "string (2-4 paragraphs of analysis and practical advice, separated by \\n\\n)",
      "pull_quote": "string (one striking sentence pulled out as a callout, OR empty string)"
    }}
  ],
  "conclusion": "string (1-2 paragraphs with a clear takeaway for UK readers)",
  "faqs": [
    {{"question": "string", "answer": "string (1-3 sentences)"}}
  ],
  "sources_cited": [
    {{"title": "string", "url": "string", "source": "string"}}
  ],
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
  "hero_image_prompt": "string (1-2 sentence brief for a UK personal finance
    editorial illustration — describe scene, mood, colours. No text overlays.
    Example: 'A split-screen showing a rising interest rate graph on the left
    and a worried homeowner reviewing mortgage paperwork on the right, muted
    blues and greys, editorial photography style.')"
}}

Requirements:
- Write in a confident, authoritative but accessible UK tone (avoid Americanisms).
- Use £ for currency. Reference UK institutions by name (HMRC, FCA, Bank of England, ONS).
- 3-5 sections. Each section has 2-4 paragraphs. Add a pull_quote where one is genuinely striking; otherwise "".
- key_takeaways: 3-5 bullets. Each starts with a strong verb or noun, no fluff.
- faqs: 3-4 genuine reader questions raised by the material, with concise answers.
- sources_cited: every input article that meaningfully informs the post (title, url, source).
- seo_tags: 5-8 lowercase tags relevant to UK personal finance.
- Do NOT copy sentences verbatim from the inputs — synthesise and analyse.

— QUALITY RUBRIC (drives the post-publish 100-pt analyser score) —
Hit these targets so the analyser scores the post 85+:

CONTENT (30 pts)

PARAGRAPH LENGTH — MANDATORY:
Each section MUST open with a standalone "anchor paragraph" of 120–180 words.
Count your words. If the anchor paragraph is under 100 or over 200 words, rewrite it.

GOOD (131 words): "The Bank of England's decision to hold rates at 4.75% in March 2026
surprised most economists, who had forecast a cut following February's softer inflation
print. For mortgage holders on tracker rates, this means another month of elevated
payments — the average two-year tracker is costing roughly £180 more per month than it
did in 2021, according to UK Finance (2026). But the picture is more nuanced than the
headline suggests. Fixed-rate borrowers who locked in during 2023 and 2024 are
increasingly rolling off onto products that are cheaper than their expiring deals,
provided they remortgage promptly. The critical question for the next six months is
whether the MPC can thread the needle between taming residual services inflation and
avoiding a housing-market slowdown."
BAD: Three-sentence paragraph followed by a bullet list. Too short — AI engines cannot cite it.

SENTENCE LENGTH — HARD LIMITS:
- Maximum single sentence: 30 words. Split anything longer into two sentences.
- Average target: 15–20 words per sentence.
- Never use more than two subordinate clauses in one sentence.

GOOD (18 words): "Mortgage rates have fallen steadily this year, but the gap between
best buys and standard variable rates remains unusually wide."
BAD (47 words): "While the Bank of England's decision to maintain its base rate at
4.75% in March was widely anticipated by markets following the stronger-than-expected
services inflation data released in February, it nonetheless disappointed homeowners
who had been hoping for relief on their monthly payments." → SPLIT THIS.

TRANSITION WORDS — TARGET 20–30% of sentences:
Start at least 1 in 4 sentences with a transition word or phrase.
Use: However, Therefore, As a result, Meanwhile, By contrast, Notably, That said,
In practice, For example, Crucially, Beyond this, On balance, In turn, Importantly.
Do NOT use: Furthermore, Moreover, Leverage, Delve, Navigate the landscape.

GOOD flow: "Inflation fell to 2.6% in March (ONS, 2026). However, wage growth remained
at 5.6% (ONS, 2026), keeping the MPC cautious. As a result, further rate cuts look
unlikely before August."
BAD flow: "Inflation fell to 2.6% in March. Wage growth remained at 5.6%. Rate cuts
look unlikely before August." — no transitions, reads as a disconnected list of facts.

- Flesch reading ease: aim 60–70 (acceptable 55–75). Short sentences are the lever.
- Passive voice ≤10%. Avoid AI-trigger words ("delve", "leverage", "navigate the
  landscape", "in today's fast-paced world", "it's important to note").
- Originality markers: include at least two of [first-person observation, ORIGINAL DATA,
  "in our analysis", "we tested", "we found", "our reading of"].
- Engagement: include at least 2 rhetorical questions and 2 concrete worked
  examples or scenarios in the body.

SEO (25 pts)
- Title 40–60 chars, front-loaded keyword, no clickbait.
- meta_description 150–160 chars, includes one statistic, ends with a value prop.
- Each H2 contains the primary keyword OR a closely related phrase.

INLINE CITATIONS — HARD REQUIREMENT:
After EVERY number, percentage, £ figure, or institutional claim, add "(Source, year)"
immediately — no exceptions. Do not group citations at the end of a paragraph.

GOOD: "Inflation fell to 2.6% in March 2026 (ONS, 2026), its lowest reading since the
post-pandemic peak, but wage growth remained at 5.6% (ONS, 2026), keeping the MPC
cautious."
BAD: "Inflation fell to 2.6% and wages grew 5.6%." — no citations, fails E-E-A-T.

Every source in sources_cited MUST appear at least once as an inline citation.
Minimum 5 inline citations total across the post.

E-E-A-T (15 pts)
- Use first-person plural ("we") where it reflects analytical experience
  (e.g. "in our reading of the FCA's 2026 guidance...", "our analysis finds...").
  Include at least 2 such first-person experience phrases.
- Cite tier-1 UK sources where possible (gov.uk, BoE, FCA, ONS, FT, Reuters).
- Include at least one quoted phrase from a named institution or report.

TECHNICAL / AI CITATION (15 + 15 pts)
- Use lists, comparison tables (in markdown), and numbered steps freely —
  these formats earn 2.5× more AI citations than prose paragraphs.
- Front-load each section with a 1-sentence answer, then expand. AI engines
  preferentially cite answer-first paragraphs.
- FAQs must be genuine reader questions, not rephrased section headings.

Hard bans (will fail compliance):
- Crypto / cryptocurrency / Bitcoin / Ethereum advice (banned topic §2.4).
- Words: "guaranteed", "risk-free", "best ever", "you must", "guaranteed to".
- Personalised investment advice. Frame as scenarios, not recommendations.

{related_posts_block}

Article records (JSON):
{articles_json}
"""


# ---------------------------------------------------------------------------
# Pass 1 — outline. A small, structured plan the draft pass fills in.
# ---------------------------------------------------------------------------

_OUTLINE_TEMPLATE = """Plan the blog post — DO NOT WRITE IT YET.

You'll see article records below. Produce a tight structural outline that
the draft pass will fill in. Aim for 4–6 sections, each one earning its
place. Rule of thumb: every section advances a distinct claim, no filler.

{angle_note}
Today is {today_human}. Today's UK personal-finance reader is the audience.

Return ONE JSON object with this exact shape:
{{
  "working_title":   "string (working headline, can refine in draft pass)",
  "thesis":          "string (the single argument the whole post defends, 1 sentence)",
  "audience_note":   "string (1 sentence on who this serves and what they get from reading)",
  "sections": [
    {{
      "heading":     "string (H2-style)",
      "claim":       "string (the one thing this section proves, 1 sentence)",
      "key_points":  ["string", "string", "..."],
      "sources_to_use": ["url-from-input or 'general knowledge'", "..."]
    }}
  ],
  "must_include_stats": ["string (specific stat with attribution to weave in)", "..."],
  "must_include_examples": ["string (concrete worked example or scenario)", "..."],
  "tone_notes": "string (1-2 sentences on register, given the angle)"
}}

Article records:
{articles_json}
"""


def _outline_blog_post(
    article_summaries: List[dict],
    client: anthropic.Anthropic,
    model: str,
    *,
    editor_angle: Optional[str],
    today_human: str,
) -> Optional[dict]:
    """Pass 1: produce a structured outline. Returns None on parse failure."""
    angle_note = ""
    if editor_angle and editor_angle.strip():
        angle_note = (
            f"★ EDITOR'S ANGLE (the outline must serve this lens): "
            f"{editor_angle.strip()}\n"
        )
    prompt = _OUTLINE_TEMPLATE.format(
        angle_note=angle_note,
        today_human=today_human,
        articles_json=json.dumps(article_summaries, ensure_ascii=False, indent=2),
    )
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=2000,
            system=[
                {"type": "text",
                 "text": voice_block(include_past_replies=True, max_replies=6),
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": _JOURNALIST_PERSONA},
            ],
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
        outline = parse_json_response(text)
    except (anthropic.APIError, json.JSONDecodeError, IndexError, AttributeError) as exc:
        log.warning("Blog outline pass failed (will fall back to one-shot): %s", exc)
        return None
    # Light validation
    if not isinstance(outline, dict) or "sections" not in outline:
        log.warning("Outline missing 'sections' key — falling back to one-shot.")
        return None
    return outline


def _outline_to_prompt_block(outline: dict) -> str:
    """Render the outline as a structured prompt block to drop into the draft pass."""
    parts = ["\n--- APPROVED OUTLINE (write the draft to this plan) ---"]
    if outline.get("working_title"):
        parts.append(f"Working title: {outline['working_title']}")
    if outline.get("thesis"):
        parts.append(f"Thesis: {outline['thesis']}")
    if outline.get("audience_note"):
        parts.append(f"Audience: {outline['audience_note']}")
    if outline.get("tone_notes"):
        parts.append(f"Tone: {outline['tone_notes']}")
    parts.append("\nSections to write (in order):")
    for i, s in enumerate(outline.get("sections", []) or [], 1):
        parts.append(f"\n{i}. {s.get('heading','')}")
        if s.get("claim"):
            parts.append(f"   Claim: {s['claim']}")
        for kp in s.get("key_points", []) or []:
            parts.append(f"   - {kp}")
        srcs = s.get("sources_to_use") or []
        if srcs:
            parts.append(f"   Use: {', '.join(srcs)}")
    if outline.get("must_include_stats"):
        parts.append("\nStats to weave in (cite source inline like '(HMRC, 2026)'):")
        for st in outline["must_include_stats"]:
            parts.append(f"  - {st}")
    if outline.get("must_include_examples"):
        parts.append("\nWorked examples / scenarios to include:")
        for ex in outline["must_include_examples"]:
            parts.append(f"  - {ex}")
    parts.append("\nWrite the draft now, following the outline above. Each section "
                 "should expand the claim with depth — not just restate the key points.")
    return "\n".join(parts)


def generate_blog_post(
    article_summaries: List[dict],
    client: anthropic.Anthropic,
    model: str,
    *,
    existing_posts: Optional[List[dict]] = None,
    editor_angle: Optional[str] = None,
    progress_cb=None,
) -> Optional[dict]:
    """Generate a blog post.

    Args:
        article_summaries: scraped article context for the body.
        client / model:    anthropic client + model name.
        existing_posts:    optional corpus of prior Warren blog posts for the
                           internal-link suggester (see src/internal_links.py).
                           When provided, the prompt instructs the LLM to weave
                           3–5 inline links into the draft using markdown link
                           syntax. Empty / None disables the suggestion.
        editor_angle:      optional 1–2 sentence brief telling the LLM how to
                           frame the piece. Becomes a priority instruction at
                           the top of the user prompt.
    """
    if not article_summaries:
        return None

    today = datetime.now(timezone.utc)
    today_human = today.strftime("%a %d %B %Y")
    div = _diversity_warning_blog(article_summaries) or ""
    diversity_note = f"⚠ {div}\n" if div else ""

    angle_note = ""
    if editor_angle and editor_angle.strip():
        angle_note = (
            "\n★ EDITOR'S ANGLE (priority framing — drive the title, intro, and "
            f"section selection from this lens): {editor_angle.strip()}\n"
        )

    related_posts_block = ""
    if existing_posts:
        # Local import keeps the optional dep out of cold start.
        from .internal_links import format_for_prompt
        related_posts_block = format_for_prompt(existing_posts)

    # --- PASS 1: outline ----------------------------------------------------
    if progress_cb: progress_cb("Outlining (pass 1 of 2)")
    outline = _outline_blog_post(
        article_summaries, client, model,
        editor_angle=editor_angle, today_human=today_human,
    )
    outline_block = _outline_to_prompt_block(outline) if outline else ""

    # --- PASS 2: draft (filled-in to the outline if it succeeded) -----------
    if progress_cb: progress_cb("Drafting (pass 2 of 2)")
    prompt = USER_TEMPLATE.format(
        articles_json=json.dumps(article_summaries, ensure_ascii=False, indent=2),
        today_human=today_human,
        diversity_note=diversity_note,
        angle_note=angle_note + outline_block,
        related_posts_block=related_posts_block,
    )
    try:
        # Use cache_control on the (large, static) brand-voice block so
        # repeat generations within ~5 min hit the Anthropic prompt cache
        # at 90% off.
        resp = client.messages.create(
            model=model,
            max_tokens=8000,
            system=[
                {"type": "text", "text": voice_block(include_past_replies=True, max_replies=6),
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": _JOURNALIST_PERSONA},
            ],
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
        data = parse_json_response(text)
    except (anthropic.APIError, json.JSONDecodeError, IndexError, AttributeError) as exc:
        log.error("Blog post generation failed: %s", exc)
        return None

    for key in ("title", "subtitle", "intro", "sections", "conclusion", "seo_tags"):
        if key not in data:
            log.error("Generated blog post missing key '%s'", key)
            return None
    # backfill new fields softly so older callers / partial outputs still render
    data.setdefault("byline", "By the Warren Editorial Desk")
    data.setdefault("key_takeaways", [])
    data.setdefault("faqs", [])
    data.setdefault("sources_cited", [])
    data.setdefault("meta_description", (data.get("subtitle") or "")[:160])
    data.setdefault("visual_elements", [])
    data.setdefault("hero_image_prompt", "")

    # --- Server-side post-processing -----------------------------------------
    data["reading_time_minutes"] = _compute_reading_time(data)
    data["published_iso"] = today.date().isoformat()
    data["published_human"] = today_human
    data["_diversity_warning"] = _diversity_warning_blog(article_summaries)
    if outline:
        # Stash the outline so the audit JSON can show how the post was planned.
        data["_outline"] = outline
    return data


# ---------------------------------------------------------------------------
# HTML rendering — magazine-grade long-form layout
# ---------------------------------------------------------------------------

NAVY     = "#0b2545"
INK      = "#1a1f36"
MUTED    = "#5a6478"
BORDER   = "#e6e9ef"
ACCENT   = "#c9a227"
ACCENT_BG = "#fdf6e3"
SOFT_BG  = "#f6f8fb"

FONT_BODY = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',"
    "Arial,sans-serif"
)
FONT_DISPLAY = (
    "Georgia,'Times New Roman','Iowan Old Style',Cambria,serif"
)


def _paras(text: str) -> str:
    parts = []
    for chunk in (text or "").split("\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if is_markdown_table(chunk):
            parts.append(markdown_table_chunk_to_html(chunk))
        else:
            parts.append(f'<p style="margin:0 0 16px 0;">{escape(chunk)}</p>')
    return "".join(parts)


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in (text or "").lower()).strip("-")


def _section_html(s: dict, idx: int) -> str:
    heading = escape(s.get("heading", "") or "")
    body    = _paras(s.get("content", "") or "")
    quote   = (s.get("pull_quote") or "").strip()
    quote_html = (
        f'<blockquote style="margin:24px 0;padding:18px 24px;border-left:4px solid {ACCENT};'
        f'background:{ACCENT_BG};font-family:{FONT_DISPLAY};font-size:20px;line-height:1.45;'
        f'color:{NAVY};font-style:italic;">"{escape(quote)}"</blockquote>'
        if quote else ""
    )
    return (
        f'<section id="sec-{idx}" style="margin:40px 0;">'
        f'<h2 style="font-family:{FONT_DISPLAY};font-size:26px;line-height:1.25;'
        f'color:{NAVY};margin:0 0 14px 0;letter-spacing:-0.01em;">{heading}</h2>'
        f'<div style="font-size:17px;line-height:1.75;color:{INK};">{body}</div>'
        f'{quote_html}'
        f'</section>'
    )


def _toc_html(sections: list) -> str:
    if not sections:
        return ""
    items = "".join(
        f'<li style="margin:6px 0;"><a href="#sec-{i}" style="color:{NAVY};'
        f'text-decoration:none;border-bottom:1px dotted {MUTED};">'
        f'{escape(s.get("heading", "") or "")}</a></li>'
        for i, s in enumerate(sections)
    )
    return (
        f'<nav style="margin:24px 0;padding:18px 22px;background:{SOFT_BG};'
        f'border-radius:8px;">'
        f'<div style="font-size:11px;font-weight:700;letter-spacing:0.12em;'
        f'text-transform:uppercase;color:{MUTED};margin-bottom:8px;">In this article</div>'
        f'<ol style="margin:0;padding-left:18px;font-size:14px;color:{INK};">{items}</ol>'
        f'</nav>'
    )


def _takeaways_html(takeaways: list) -> str:
    if not takeaways:
        return ""
    items = "".join(
        f'<li style="margin:8px 0;padding-left:6px;">{escape(t)}</li>' for t in takeaways
    )
    return (
        f'<aside style="margin:28px 0;padding:24px 26px;border:1px solid {BORDER};'
        f'border-left:4px solid {ACCENT};border-radius:8px;background:#ffffff;">'
        f'<div style="font-size:11px;font-weight:700;letter-spacing:0.14em;'
        f'text-transform:uppercase;color:{ACCENT};margin-bottom:10px;">Key Takeaways</div>'
        f'<ul style="margin:0;padding-left:20px;font-size:15px;line-height:1.65;color:{INK};">'
        f'{items}</ul></aside>'
    )


def _faqs_html(faqs: list) -> str:
    if not faqs:
        return ""
    items = "".join(
        f'<div style="padding:16px 0;border-bottom:1px solid {BORDER};">'
        f'<div style="font-weight:700;color:{NAVY};font-size:16px;margin-bottom:6px;">'
        f'Q. {escape(f.get("question",""))}</div>'
        f'<div style="font-size:15px;line-height:1.65;color:{INK};">'
        f'{escape(f.get("answer",""))}</div></div>'
        for f in faqs
    )
    return (
        f'<section style="margin:40px 0;">'
        f'<h2 style="font-family:{FONT_DISPLAY};font-size:24px;color:{NAVY};'
        f'margin:0 0 8px 0;">Frequently Asked Questions</h2>{items}</section>'
    )


def _sources_html(sources: list) -> str:
    if not sources:
        return ""
    items = "".join(
        f'<li style="margin:6px 0;font-size:13px;line-height:1.55;">'
        f'<a href="{escape(s.get("url","#"))}" style="color:{NAVY};">'
        f'{escape(s.get("title",""))}</a> '
        f'<span style="color:{MUTED};">— {escape(s.get("source",""))}</span></li>'
        for s in sources
    )
    return (
        f'<section style="margin:32px 0;padding:20px 22px;background:{SOFT_BG};'
        f'border-radius:8px;">'
        f'<div style="font-size:11px;font-weight:700;letter-spacing:0.12em;'
        f'text-transform:uppercase;color:{MUTED};margin-bottom:10px;">Sources</div>'
        f'<ul style="margin:0;padding-left:18px;">{items}</ul></section>'
    )


def _tags_html(tags: list) -> str:
    if not tags:
        return ""
    pills = "".join(
        f'<span style="display:inline-block;background:{ACCENT_BG};color:{NAVY};'
        f'padding:4px 12px;border-radius:14px;font-size:12px;font-weight:600;'
        f'margin:3px;">#{escape(t)}</span>' for t in tags
    )
    return f'<div style="margin-top:8px;">{pills}</div>'


def _tldr_html(takeaways: list) -> str:
    """A speakable TL;DR shown above the body — paired with schema.org Speakable."""
    if not takeaways:
        return ""
    items = "".join(f"<li>{escape(t)}</li>" for t in takeaways[:3])
    return (
        f'<aside class="tldr" aria-label="TL;DR" itemscope '
        f'itemtype="https://schema.org/SpeakableSpecification" '
        f'style="margin:18px 0 0 0;padding:14px 18px;background:{NAVY};color:#ffffff;'
        f'border-radius:8px;">'
        f'<div style="font-size:11px;font-weight:700;letter-spacing:0.14em;'
        f'text-transform:uppercase;color:{ACCENT};margin-bottom:6px;">TL;DR</div>'
        f'<ul style="margin:0;padding-left:18px;font-size:14px;line-height:1.55;">{items}</ul>'
        f'</aside>'
    )


def build_jsonld(post: dict) -> list[dict]:
    """Return JSON-LD schema dicts for a post.

    Always includes a BlogPosting. Includes FAQPage when faqs are present.
    Reused by blog_to_html (HTML <head>) and the markdown exporter so the
    quality analyser credits schema regardless of which artifact it scores.
    """
    title       = (post.get("title") or "").strip()
    description = (post.get("meta_description") or post.get("subtitle") or "").strip()[:300]
    url         = (post.get("canonical_url") or "").strip()
    published   = (post.get("published_iso") or datetime.now(timezone.utc).date().isoformat())
    tags        = post.get("seo_tags") or []

    article_ld = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": title,
        "description": description,
        "datePublished": published,
        "dateModified": published,
        "author": [
            {
                "@type": "Person",
                "name": "Warren Editorial Team",
                "url": "https://meetwarren.co.uk/about",
            },
            {
                "@type": "Organization",
                "name": "Warren",
                "url": "https://meetwarren.co.uk",
            },
        ],
        "publisher": {
            "@type": "Organization",
            "name": "Warren",
            "url": "https://meetwarren.co.uk",
        },
        "mainEntityOfPage": url or "",
        "keywords": ", ".join(tags),
        "speakable": {
            "@type": "SpeakableSpecification",
            "cssSelector": [".tldr", "h1"],
        },
    }
    person_ld = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": "Warren Editorial Team",
        "url": "https://meetwarren.co.uk/about",
        "worksFor": {
            "@type": "Organization",
            "name": "Warren",
            "url": "https://meetwarren.co.uk",
        },
    }
    out = [article_ld, person_ld]
    if post.get("faqs"):
        out.append({
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": f.get("question", ""),
                    "acceptedAnswer": {"@type": "Answer", "text": f.get("answer", "")},
                }
                for f in post["faqs"]
            ],
        })
    return out


def _seo_head(post: dict) -> str:
    """Open Graph + Twitter Card + JSON-LD Article + canonical + meta description."""
    title       = (post.get("title") or "").strip()
    description = (post.get("meta_description") or post.get("subtitle") or "").strip()[:300]
    url         = (post.get("canonical_url") or "").strip()
    published   = (post.get("published_iso") or datetime.now(timezone.utc).date().isoformat())
    byline      = (post.get("byline") or "By the Warren Editorial Desk").strip()
    tags        = post.get("seo_tags") or []

    canonical = f'<link rel="canonical" href="{escape(url)}">' if url else ""

    og = f"""
  <meta property="og:type" content="article">
  <meta property="og:title" content="{escape(title)}">
  <meta property="og:description" content="{escape(description)}">
  <meta property="og:site_name" content="Warren · UK Personal Finance">
  {f'<meta property="og:url" content="{escape(url)}">' if url else ""}
  <meta property="article:published_time" content="{escape(published)}">
  <meta property="article:author" content="{escape(byline)}">"""
    for t in tags[:8]:
        og += f'\n  <meta property="article:tag" content="{escape(t)}">'

    twitter = f"""
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{escape(title)}">
  <meta name="twitter:description" content="{escape(description)}">"""

    ld_blocks = "".join(
        f'\n  <script type="application/ld+json">{json.dumps(d, ensure_ascii=False)}</script>'
        for d in build_jsonld(post)
    )

    return f"""
  <meta name="description" content="{escape(description)}">
  {canonical}{og}{twitter}{ld_blocks}"""


def blog_to_html(post: dict) -> str:
    title    = escape(post.get("title", "") or "")
    subtitle = escape(post.get("subtitle", "") or "")
    byline   = escape(post.get("byline", "By the Warren Editorial Desk") or "")
    rt       = int(post.get("reading_time_minutes", 6) or 6)
    published_iso   = post.get("published_iso") or datetime.now(timezone.utc).date().isoformat()
    today           = post.get("published_human") or datetime.now(timezone.utc).strftime("%d %B %Y").lstrip("0")

    # Build visual elements lookup: after_section -> list of VE dicts
    visual_elements = post.get("visual_elements") or []
    _ve_by_section: dict[int, list] = {}
    _ve_intro: list = []
    _chart_counter = [0]  # mutable counter for unique chart IDs

    for ve in visual_elements:
        ai = ve.get("after_section")
        if ai is None:
            continue
        try:
            ai = int(ai)
        except (TypeError, ValueError):
            continue
        if ai == -1:
            _ve_intro.append(ve)
        else:
            _ve_by_section.setdefault(ai, []).append(ve)

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
        return out

    intro_paras      = _paras(post.get("intro", "") or "")
    intro_ve_html    = _render_ves(_ve_intro)
    conclusion_paras = _paras(post.get("conclusion", "") or "")

    # Build sections with VEs inserted after each
    sections_html = ""
    for i, s in enumerate(post.get("sections", [])):
        sections_html += _section_html(s, i)
        sections_html += _render_ves(_ve_by_section.get(i, []))

    toc              = _toc_html(post.get("sections", []))
    tldr             = _tldr_html(post.get("key_takeaways", []))
    takeaways        = _takeaways_html(post.get("key_takeaways", []))
    faqs             = _faqs_html(post.get("faqs", []))
    sources          = _sources_html(post.get("sources_cited", []))
    tags             = _tags_html(post.get("seo_tags", []))
    seo_head         = _seo_head(post)
    chartjs_tag      = CHARTJS_CDN if has_charts(visual_elements) else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>{title}</title>{seo_head}
  {chartjs_tag}
  <style>
    @media (prefers-color-scheme: dark) {{
      body {{ background:#0f1419 !important; color:#e6e9ef !important; }}
      header, main, footer {{ background:#1a1f2a !important; border-color:#2a3140 !important; }}
      h1, h2 {{ color:#f1d785 !important; }}
      blockquote {{ background:#2a2415 !important; color:#f1d785 !important; }}
      a {{ color:#9bbcec !important; }}
      .meta-row {{ color:#8892a3 !important; border-color:#2a3140 !important; }}
    }}
    @media print {{
      header, footer, nav {{ display:none !important; }}
      main {{ max-width:100% !important; padding:0 !important; }}
      a {{ color:inherit !important; text-decoration:none !important; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background:{SOFT_BG};font-family:{FONT_BODY};color:{INK};">

  <header role="banner" style="background:#ffffff;border-bottom:1px solid {BORDER};padding:18px 24px;">
    <div style="max-width:780px;margin:0 auto;display:flex;justify-content:space-between;align-items:center;">
      <div>
        <div style="font-size:20px;font-weight:800;color:{NAVY};letter-spacing:-0.01em;">Warren</div>
        <div style="font-size:11px;color:{MUTED};letter-spacing:0.1em;text-transform:uppercase;font-weight:600;">UK Personal Finance</div>
      </div>
      <time datetime="{escape(published_iso)}" style="font-size:12px;color:{MUTED};">{escape(today)}</time>
    </div>
  </header>

  <main role="main" style="max-width:780px;margin:0 auto;padding:48px 24px 24px 24px;background:#ffffff;">
    <article itemscope itemtype="https://schema.org/BlogPosting">
      <meta itemprop="datePublished" content="{escape(published_iso)}">

      <div style="font-size:11px;font-weight:700;letter-spacing:0.16em;text-transform:uppercase;color:{ACCENT};margin-bottom:14px;">Analysis · UK Personal Finance</div>
      <h1 itemprop="headline" style="font-family:{FONT_DISPLAY};font-size:40px;line-height:1.15;color:{NAVY};margin:0 0 16px 0;letter-spacing:-0.02em;">{title}</h1>
      <p itemprop="description" style="font-family:{FONT_BODY};font-size:19px;line-height:1.5;color:{MUTED};margin:0 0 20px 0;">{subtitle}</p>
      <div class="meta-row" style="display:flex;align-items:center;gap:14px;font-size:13px;color:{MUTED};padding:14px 0;border-top:1px solid {BORDER};border-bottom:1px solid {BORDER};margin-bottom:24px;">
        <span itemprop="author" style="font-weight:600;color:{INK};">{byline}</span>
        <span aria-hidden="true">·</span>
        <span>{rt} min read</span>
      </div>

      {tldr}

      <div itemprop="articleBody" style="font-size:18px;line-height:1.8;color:{INK};margin-top:24px;">
        {intro_paras}
        {intro_ve_html}
        {takeaways}
        <nav role="doc-toc" aria-label="Table of contents">{toc}</nav>
        {sections_html}

        <section style="margin:40px 0;padding:28px 30px;background:{NAVY};border-radius:10px;color:#ffffff;">
          <div style="font-size:11px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;color:{ACCENT};margin-bottom:12px;">The Bottom Line</div>
          <div style="font-size:17px;line-height:1.7;">{conclusion_paras}</div>
        </section>

        <p class="warren-trust-footer" style="font-size:13px;color:#6b7280;margin:24px 0;font-style:italic;">
          The <a href="https://meetwarren.co.uk/about" style="color:#6b7280;">Warren Editorial Team</a>
          produces independent UK personal finance analysis.
          <a href="mailto:info@meetwarren.co.uk" style="color:#6b7280;">Contact us</a>.
        </p>

        {faqs}
        {sources}
      </div>
    </article>
  </main>

  <footer role="contentinfo" style="max-width:780px;margin:0 auto;padding:20px 24px 48px 24px;background:#ffffff;border-top:1px solid {BORDER};">
    {tags}
    <div style="margin-top:16px;font-size:12px;color:{MUTED};text-align:center;">
      Published by Warren · UK personal finance, weekly.
    </div>
  </footer>

</body>
</html>
"""


def blog_to_text(post: dict) -> str:
    out = []
    title = post.get("title", "") or ""
    out += [title, "=" * max(len(title), 3),
            post.get("subtitle", "") or "",
            f"{post.get('byline','')} · {post.get('reading_time_minutes',6)} min read",
            ""]

    if post.get("key_takeaways"):
        out += ["KEY TAKEAWAYS", "-------------"]
        out += [f"- {t}" for t in post["key_takeaways"]]
        out.append("")

    out += [post.get("intro", "") or "", ""]
    for s in post.get("sections", []):
        h = s.get("heading", "") or ""
        out += [h, "-" * max(len(h), 3), s.get("content", "") or ""]
        if s.get("pull_quote"):
            out += ["", f'"{s["pull_quote"]}"']
        out.append("")

    out += ["THE BOTTOM LINE", "---------------", post.get("conclusion", "") or "", ""]

    if post.get("faqs"):
        out += ["FAQ", "---"]
        for f in post["faqs"]:
            out += [f"Q. {f.get('question','')}", f"A. {f.get('answer','')}", ""]

    if post.get("sources_cited"):
        out += ["SOURCES", "-------"]
        for s in post["sources_cited"]:
            out += [f"- {s.get('title','')} — {s.get('source','')} ({s.get('url','')})"]
        out.append("")

    out.append("Tags: " + ", ".join(f"#{t}" for t in post.get("seo_tags", [])))
    return "\n".join(out)
