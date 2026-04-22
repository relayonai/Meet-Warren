from __future__ import annotations

import json
import logging
from datetime import datetime
from html import escape
from typing import List, Optional

import anthropic

from ._json import parse_json_response

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a senior UK personal-finance journalist with 15 years at outlets like the FT and MoneyWeek. "
    "You synthesise — never paraphrase — and your analysis carries practical implications "
    "for British readers managing mortgages, ISAs, pensions, tax, and savings. "
    "You return ONLY valid JSON. No markdown fences, no prose outside the JSON object."
)

USER_TEMPLATE = """Write a long-form UK personal-finance blog post synthesising the article summaries below.

Return a JSON object that EXACTLY matches this schema (do not add or omit keys):
{{
  "title":    "string (compelling SEO-friendly headline, <= 90 chars)",
  "subtitle": "string (supporting deck / dek, <= 160 chars)",
  "byline":   "string (e.g. 'By the Warren Editorial Desk')",
  "reading_time_minutes": integer (estimate based on body length, 4-12),
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
  "seo_tags": ["string", "..."]
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

Article summaries (JSON):
{articles_json}
"""


def generate_blog_post(
    article_summaries: List[dict],
    client: anthropic.Anthropic,
    model: str,
) -> Optional[dict]:
    if not article_summaries:
        return None

    prompt = USER_TEMPLATE.format(
        articles_json=json.dumps(article_summaries, ensure_ascii=False, indent=2)
    )
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=8000,
            system=SYSTEM_PROMPT,
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
    data.setdefault("reading_time_minutes", max(4, min(12, len(data.get("sections", [])) * 2 + 3)))
    data.setdefault("key_takeaways", [])
    data.setdefault("faqs", [])
    data.setdefault("sources_cited", [])
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
    return "".join(
        f'<p style="margin:0 0 16px 0;">{escape(p.strip())}</p>'
        for p in (text or "").split("\n\n") if p.strip()
    )


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


def blog_to_html(post: dict) -> str:
    title    = escape(post.get("title", "") or "")
    subtitle = escape(post.get("subtitle", "") or "")
    byline   = escape(post.get("byline", "By the Warren Editorial Desk") or "")
    rt       = int(post.get("reading_time_minutes", 6) or 6)
    today    = datetime.utcnow().strftime("%-d %B %Y") if hasattr(datetime, "strftime") else ""

    intro_paras      = _paras(post.get("intro", "") or "")
    conclusion_paras = _paras(post.get("conclusion", "") or "")
    sections_html    = "".join(_section_html(s, i) for i, s in enumerate(post.get("sections", [])))
    toc              = _toc_html(post.get("sections", []))
    takeaways        = _takeaways_html(post.get("key_takeaways", []))
    faqs             = _faqs_html(post.get("faqs", []))
    sources          = _sources_html(post.get("sources_cited", []))
    tags             = _tags_html(post.get("seo_tags", []))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title}</title>
</head>
<body style="margin:0;padding:0;background:{SOFT_BG};font-family:{FONT_BODY};color:{INK};">

  <header style="background:#ffffff;border-bottom:1px solid {BORDER};padding:18px 24px;">
    <div style="max-width:780px;margin:0 auto;display:flex;justify-content:space-between;align-items:center;">
      <div>
        <div style="font-size:20px;font-weight:800;color:{NAVY};letter-spacing:-0.01em;">Warren</div>
        <div style="font-size:11px;color:{MUTED};letter-spacing:0.1em;text-transform:uppercase;font-weight:600;">UK Personal Finance</div>
      </div>
      <div style="font-size:12px;color:{MUTED};">{today}</div>
    </div>
  </header>

  <main style="max-width:780px;margin:0 auto;padding:48px 24px 24px 24px;background:#ffffff;">

    <div style="font-size:11px;font-weight:700;letter-spacing:0.16em;text-transform:uppercase;color:{ACCENT};margin-bottom:14px;">Analysis · UK Personal Finance</div>
    <h1 style="font-family:{FONT_DISPLAY};font-size:40px;line-height:1.15;color:{NAVY};margin:0 0 16px 0;letter-spacing:-0.02em;">{title}</h1>
    <p style="font-family:{FONT_BODY};font-size:19px;line-height:1.5;color:{MUTED};margin:0 0 20px 0;">{subtitle}</p>
    <div style="display:flex;align-items:center;gap:14px;font-size:13px;color:{MUTED};padding:14px 0;border-top:1px solid {BORDER};border-bottom:1px solid {BORDER};margin-bottom:32px;">
      <span style="font-weight:600;color:{INK};">{byline}</span>
      <span>·</span>
      <span>{rt} min read</span>
    </div>

    <div style="font-size:18px;line-height:1.8;color:{INK};">{intro_paras}</div>

    {takeaways}
    {toc}
    {sections_html}

    <section style="margin:40px 0;padding:28px 30px;background:{NAVY};border-radius:10px;color:#ffffff;">
      <div style="font-size:11px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;color:{ACCENT};margin-bottom:12px;">The Bottom Line</div>
      <div style="font-size:17px;line-height:1.7;">{conclusion_paras}</div>
    </section>

    {faqs}
    {sources}

  </main>

  <footer style="max-width:780px;margin:0 auto;padding:20px 24px 48px 24px;background:#ffffff;border-top:1px solid {BORDER};">
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
