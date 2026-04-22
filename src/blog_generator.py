from __future__ import annotations

import json
import logging
from typing import List, Optional

import anthropic

from ._json import parse_json_response

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a senior UK personal-finance journalist writing for an educated, engaged audience. "
    "You return ONLY valid JSON. No markdown fences, no prose outside the JSON object."
)

USER_TEMPLATE = """Write a long-form UK personal-finance blog post synthesising the article summaries below.

Return a JSON object that exactly matches this schema:
{{
  "title": "string (compelling SEO-friendly headline, <= 90 chars)",
  "subtitle": "string (supporting deck, <= 140 chars)",
  "intro": "string (2-3 paragraphs setting context — no bullet points)",
  "sections": [
    {{
      "heading": "string (clear H2-level subheading)",
      "content": "string (2-4 paragraphs of analysis and practical advice)"
    }}
  ],
  "conclusion": "string (1-2 paragraphs with a clear takeaway for UK readers)",
  "seo_tags": ["string", "..."]
}}

Requirements:
- Write in a confident, authoritative but accessible UK tone (avoid Americanisms)
- Use £ for currency, reference UK institutions (HMRC, FCA, Bank of England) where relevant
- Include 2–4 sections
- seo_tags: 5–8 lowercase tags relevant to UK personal finance
- Do NOT copy sentences verbatim from the summaries — synthesise and analyse

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
            max_tokens=4096,
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
    return data


def blog_to_html(post: dict) -> str:
    from html import escape

    def _paras(text: str) -> str:
        return "".join(f"<p>{escape(p.strip())}</p>" for p in text.split("\n\n") if p.strip())

    def _section_html(s: dict) -> str:
        heading = escape(s.get("heading", ""))
        body    = _paras(s.get("content", ""))
        return (
            f'<section style="margin:28px 0;">'
            f'<h2 style="font-size:22px;color:#0b2545;border-bottom:2px solid #eee;padding-bottom:6px;">{heading}</h2>'
            f'<div style="font-size:16px;line-height:1.7;color:#333;">{body}</div>'
            f'</section>'
        )

    sections_html = "".join(_section_html(s) for s in post.get("sections", []))

    def _tag_span(t: str) -> str:
        return (
            f'<span style="background:#e3f2fd;color:#1a4f8b;padding:3px 10px;'
            f'border-radius:12px;font-size:13px;margin:2px;">#{escape(t)}</span>'
        )

    tags_html        = " ".join(_tag_span(t) for t in post.get("seo_tags", []))
    intro_paras      = _paras(post.get("intro", ""))
    conclusion_paras = _paras(post.get("conclusion", ""))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{escape(post.get('title', ''))}</title>
</head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:740px;margin:0 auto;padding:32px 24px;color:#222;">
  <header style="margin-bottom:28px;">
    <h1 style="font-size:32px;color:#0b2545;line-height:1.2;margin-bottom:8px;">{escape(post.get('title', ''))}</h1>
    <p style="font-size:18px;color:#555;margin:0;">{escape(post.get('subtitle', ''))}</p>
  </header>
  <div style="font-size:16px;line-height:1.7;color:#333;">{intro_paras}</div>
  {sections_html}
  <section style="margin:28px 0;padding:20px;background:#f0f7ff;border-radius:8px;">
    <h2 style="font-size:20px;color:#0b2545;margin-top:0;">Key Takeaways</h2>
    <div style="font-size:16px;line-height:1.7;">{conclusion_paras}</div>
  </section>
  <footer style="margin-top:32px;padding-top:16px;border-top:1px solid #eee;">
    <div>{tags_html}</div>
  </footer>
</body>
</html>
"""


def blog_to_text(post: dict) -> str:
    lines = [
        post.get("title", ""),
        "=" * len(post.get("title", "")),
        post.get("subtitle", ""),
        "",
        post.get("intro", ""),
        "",
    ]
    for s in post.get("sections", []):
        lines += [s.get("heading", ""), "-" * len(s.get("heading", "")), s.get("content", ""), ""]
    lines += ["Key Takeaways", "-------------", post.get("conclusion", ""), ""]
    lines.append("Tags: " + ", ".join(f"#{t}" for t in post.get("seo_tags", [])))
    return "\n".join(lines)
