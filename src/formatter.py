from __future__ import annotations

from html import escape
from typing import Any, Dict


def to_html(newsletter: Dict[str, Any]) -> str:
    sections_html = []
    for section in newsletter.get("sections", []):
        articles_html = "".join(
            f"""
            <li style="margin-bottom:12px;">
              <a href="{escape(a.get('url', '#'))}" style="color:#1a4f8b;text-decoration:none;font-weight:600;">
                {escape(a.get('title', ''))}
              </a>
              <div style="color:#444;font-size:14px;margin-top:4px;">{escape(a.get('blurb', ''))}</div>
            </li>
            """
            for a in section.get("articles", [])
        )
        sections_html.append(
            f"""
            <section style="margin:24px 0;">
              <h2 style="font-size:20px;color:#1a1a1a;border-bottom:2px solid #eee;padding-bottom:6px;">
                {escape(section.get('heading', ''))}
              </h2>
              <ul style="list-style:none;padding:0;">{articles_html}</ul>
              <p style="font-style:italic;color:#666;margin-top:8px;">{escape(section.get('commentary', ''))}</p>
            </section>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{escape(newsletter.get('subject_line', 'UK Personal Finance Digest'))}</title>
</head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:680px;margin:0 auto;padding:24px;color:#222;">
  <header>
    <h1 style="font-size:26px;color:#0b2545;margin-bottom:4px;">
      {escape(newsletter.get('subject_line', ''))}
    </h1>
  </header>
  <p style="font-size:16px;line-height:1.5;">{escape(newsletter.get('intro', ''))}</p>
  {''.join(sections_html)}
  <footer style="margin-top:32px;padding-top:16px;border-top:1px solid #eee;color:#555;font-size:14px;">
    {escape(newsletter.get('closing', ''))}
  </footer>
</body>
</html>
"""


def to_text(newsletter: Dict[str, Any]) -> str:
    lines = []
    lines.append(newsletter.get("subject_line", ""))
    lines.append("=" * len(newsletter.get("subject_line", "")))
    lines.append("")
    lines.append(newsletter.get("intro", ""))
    lines.append("")
    for section in newsletter.get("sections", []):
        lines.append(section.get("heading", ""))
        lines.append("-" * len(section.get("heading", "")))
        for a in section.get("articles", []):
            lines.append(f"* {a.get('title', '')}")
            lines.append(f"  {a.get('url', '')}")
            blurb = a.get("blurb", "")
            if blurb:
                lines.append(f"  {blurb}")
        commentary = section.get("commentary", "")
        if commentary:
            lines.append("")
            lines.append(commentary)
        lines.append("")
    lines.append("---")
    lines.append(newsletter.get("closing", ""))
    return "\n".join(lines)
