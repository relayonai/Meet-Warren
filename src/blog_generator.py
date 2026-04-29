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

SYSTEM_PROMPT = (
    "You are a senior UK personal-finance journalist with 15 years at outlets like the FT and MoneyWeek. "
    "You synthesise — never paraphrase — and your analysis carries practical implications "
    "for British readers managing mortgages, ISAs, pensions, tax, and savings. "
    "You return ONLY valid JSON. No markdown fences, no prose outside the JSON object."
)

USER_TEMPLATE = """Write a long-form UK personal-finance blog post synthesising the article records below.

Each input article carries: title, url, source, published_at, category, relevance_score,
a `summary`, `key_points` (3-5 bullets), and an `excerpt` (raw text from the original).
USE the key_points and excerpt — synthesise across them, do not paraphrase a single
summary. Attribute facts to their original source.

{diversity_note}
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

— QUALITY RUBRIC (drives the post-publish 100-pt analyser score) —
Adapted from AgriciDaniel/claude-blog references (eeat-signals, quality-scoring, geo-optimization).
Hit these targets so the analyser scores the post 80+:

CONTENT (30 pts)
- Total length 1,500–2,500 words. Sections ~120–180 words between H2s.
- Sentence length: average 15–20 words; ≤25% over 20 words; none over 40.
- Flesch reading ease: aim 60–70 (acceptable 55–75).
- Passive voice ≤10%. Avoid AI-trigger words ("delve", "leverage", "navigate the
  landscape", "in today's fast-paced world", "it's important to note").
- Originality markers: include at least one of [first-person observation,
  ORIGINAL DATA, "in our analysis", "we tested", "we found"].
- Engagement: include at least 2 rhetorical questions and 2 concrete worked
  examples or scenarios in the body.

SEO (25 pts)
- Title 40–60 chars, front-loaded keyword, no clickbait.
- meta_description 150–160 chars, includes one statistic, ends with a value prop.
- Each H2 contains the primary keyword OR a closely related phrase.
- Reference 3–8 authoritative outbound sources via inline citations like
  "(HMRC, 2026)" — every statistic must be attributed inline AND appear in
  sources_cited. Never fabricate numbers.

E-E-A-T (15 pts)
- Use first-person plural ("we") sparingly, only where it reflects analytical
  experience (e.g. "in our reading of the FCA's 2026 guidance...").
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


def generate_blog_post(
    article_summaries: List[dict],
    client: anthropic.Anthropic,
    model: str,
    *,
    existing_posts: Optional[List[dict]] = None,
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
    """
    if not article_summaries:
        return None

    today = datetime.now(timezone.utc)
    today_human = today.strftime("%a %d %B %Y")
    div = _diversity_warning_blog(article_summaries) or ""
    diversity_note = f"⚠ {div}\n" if div else ""

    related_posts_block = ""
    if existing_posts:
        # Local import keeps the optional dep out of cold start.
        from .internal_links import format_for_prompt
        related_posts_block = format_for_prompt(existing_posts)

    prompt = USER_TEMPLATE.format(
        articles_json=json.dumps(article_summaries, ensure_ascii=False, indent=2),
        today_human=today_human,
        diversity_note=diversity_note,
        related_posts_block=related_posts_block,
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
    data.setdefault("key_takeaways", [])
    data.setdefault("faqs", [])
    data.setdefault("sources_cited", [])
    data.setdefault("meta_description", (data.get("subtitle") or "")[:160])

    # --- Server-side post-processing -----------------------------------------
    data["reading_time_minutes"] = _compute_reading_time(data)
    data["published_iso"] = today.date().isoformat()
    data["published_human"] = today_human
    data["_diversity_warning"] = _diversity_warning_blog(article_summaries)
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

    Always includes a NewsArticle. Includes FAQPage when faqs are present.
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
        "@type": "NewsArticle",
        "headline": title,
        "description": description,
        "datePublished": published,
        "dateModified": published,
        "author": [{"@type": "Organization", "name": "Warren Editorial Desk"}],
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
    out = [article_ld]
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

    intro_paras      = _paras(post.get("intro", "") or "")
    conclusion_paras = _paras(post.get("conclusion", "") or "")
    sections_html    = "".join(_section_html(s, i) for i, s in enumerate(post.get("sections", [])))
    toc              = _toc_html(post.get("sections", []))
    tldr             = _tldr_html(post.get("key_takeaways", []))
    takeaways        = _takeaways_html(post.get("key_takeaways", []))
    faqs             = _faqs_html(post.get("faqs", []))
    sources          = _sources_html(post.get("sources_cited", []))
    tags             = _tags_html(post.get("seo_tags", []))
    seo_head         = _seo_head(post)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>{title}</title>{seo_head}
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
    <article itemscope itemtype="https://schema.org/NewsArticle">
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
        {takeaways}
        <nav role="doc-toc" aria-label="Table of contents">{toc}</nav>
        {sections_html}

        <section style="margin:40px 0;padding:28px 30px;background:{NAVY};border-radius:10px;color:#ffffff;">
          <div style="font-size:11px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;color:{ACCENT};margin-bottom:12px;">The Bottom Line</div>
          <div style="font-size:17px;line-height:1.7;">{conclusion_paras}</div>
        </section>

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
