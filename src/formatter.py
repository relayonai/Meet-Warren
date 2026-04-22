from __future__ import annotations

from html import escape
from typing import Any, Dict


# ---------- design tokens (kept inline so output is portable) ---------------
NAVY     = "#0b2545"
INK      = "#1a1f36"
MUTED    = "#5a6478"
BORDER   = "#e6e9ef"
ACCENT   = "#c9a227"   # warm gold
ACCENT_BG = "#fdf6e3"
SOFT_BG  = "#f6f8fb"
LINK     = "#1a4f8b"

FONT_STACK = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',"
    "Arial,sans-serif"
)


def _article_block(a: Dict[str, Any]) -> str:
    title  = escape(a.get("title", "") or "")
    url    = escape(a.get("url", "#") or "#")
    source = escape(a.get("source", "") or "")
    blurb  = escape(a.get("blurb", "") or "")
    why    = escape(a.get("why_it_matters", "") or "")

    source_pill = (
        f'<span style="display:inline-block;background:{SOFT_BG};color:{MUTED};'
        f'font-size:11px;font-weight:600;letter-spacing:0.04em;text-transform:uppercase;'
        f'padding:3px 8px;border-radius:10px;margin-bottom:8px;">{source}</span>'
        if source else ""
    )
    why_block = (
        f'<div style="margin-top:10px;padding:10px 12px;background:{SOFT_BG};'
        f'border-left:3px solid {ACCENT};border-radius:4px;'
        f'font-size:13px;color:{INK};">'
        f'<strong style="color:{NAVY};">Why it matters · </strong>{why}</div>'
        if why else ""
    )

    return (
        f'<div style="padding:18px 0;border-bottom:1px solid {BORDER};">'
        f'{source_pill}'
        f'<a href="{url}" style="display:block;color:{NAVY};text-decoration:none;'
        f'font-size:17px;font-weight:700;line-height:1.35;margin-bottom:6px;">{title}</a>'
        f'<div style="color:{INK};font-size:14px;line-height:1.6;">{blurb}</div>'
        f'{why_block}'
        f'</div>'
    )


def _section_block(s: Dict[str, Any]) -> str:
    heading    = escape(s.get("heading", "") or "")
    summary    = escape(s.get("summary", "") or "")
    commentary = escape(s.get("commentary", "") or "")
    articles   = "".join(_article_block(a) for a in s.get("articles", []))

    summary_html = (
        f'<p style="color:{MUTED};font-size:14px;font-style:italic;'
        f'margin:4px 0 14px 0;">{summary}</p>' if summary else ""
    )
    commentary_html = (
        f'<div style="margin-top:16px;padding:14px 16px;background:{ACCENT_BG};'
        f'border-radius:6px;font-size:14px;color:{INK};font-style:italic;">'
        f'<strong style="font-style:normal;color:{NAVY};">Editor · </strong>'
        f'{commentary}</div>' if commentary else ""
    )

    return (
        f'<section style="margin:36px 0 0 0;">'
        f'<div style="display:flex;align-items:baseline;gap:10px;">'
        f'<span style="display:inline-block;width:6px;height:22px;background:{ACCENT};'
        f'border-radius:2px;"></span>'
        f'<h2 style="font-family:{FONT_STACK};font-size:20px;color:{NAVY};margin:0;'
        f'letter-spacing:-0.01em;">{heading}</h2>'
        f'</div>'
        f'{summary_html}'
        f'<div style="margin-top:8px;">{articles}</div>'
        f'{commentary_html}'
        f'</section>'
    )


def _editor_pick_block(pick: Dict[str, Any] | None) -> str:
    if not pick:
        return ""
    title = escape(pick.get("title", "") or "")
    url   = escape(pick.get("url", "#") or "#")
    why   = escape(pick.get("why", "") or "")
    return (
        f'<div style="margin:28px 0;padding:22px 24px;background:{NAVY};'
        f'border-radius:10px;color:#ffffff;">'
        f'<div style="font-size:11px;font-weight:700;letter-spacing:0.12em;'
        f'text-transform:uppercase;color:{ACCENT};margin-bottom:8px;">'
        f"★ Editor's Pick</div>"
        f'<a href="{url}" style="display:block;color:#ffffff;text-decoration:none;'
        f'font-size:20px;font-weight:700;line-height:1.3;margin-bottom:8px;">{title}</a>'
        f'<div style="color:#d8e0ee;font-size:14px;line-height:1.6;">{why}</div>'
        f'</div>'
    )


def to_html(newsletter: Dict[str, Any]) -> str:
    subject       = escape(newsletter.get("subject_line", "") or "UK Personal Finance Digest")
    preheader     = escape(newsletter.get("preheader", "") or "")
    edition_label = escape(newsletter.get("edition_label", "") or "")
    intro         = escape(newsletter.get("intro", "") or "")
    closing       = escape(newsletter.get("closing", "") or "")
    signature     = escape(newsletter.get("signature", "The Warren Editorial Desk") or "")

    sections_html = "".join(_section_block(s) for s in newsletter.get("sections", []))
    pick_html     = _editor_pick_block(newsletter.get("editor_pick"))

    preheader_span = (
        f'<span style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">'
        f'{preheader}</span>' if preheader else ""
    )
    edition_html = (
        f'<div style="font-size:11px;font-weight:700;letter-spacing:0.16em;'
        f'text-transform:uppercase;color:{ACCENT};">{edition_label}</div>'
        if edition_label else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{subject}</title>
</head>
<body style="margin:0;padding:0;background:{SOFT_BG};font-family:{FONT_STACK};color:{INK};">
  {preheader_span}
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:{SOFT_BG};padding:24px 12px;">
    <tr><td align="center">
      <table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0"
             style="max-width:640px;width:100%;background:#ffffff;border-radius:12px;
                    box-shadow:0 1px 3px rgba(11,37,69,0.06);overflow:hidden;">

        <tr><td style="padding:28px 32px 8px 32px;border-bottom:1px solid {BORDER};">
          <table role="presentation" width="100%"><tr>
            <td style="vertical-align:middle;">
              <div style="font-family:{FONT_STACK};font-size:20px;font-weight:800;
                          color:{NAVY};letter-spacing:-0.01em;">Warren</div>
              <div style="font-size:11px;color:{MUTED};letter-spacing:0.1em;
                          text-transform:uppercase;font-weight:600;">UK Personal Finance</div>
            </td>
            <td style="vertical-align:middle;text-align:right;">{edition_html}</td>
          </tr></table>
        </td></tr>

        <tr><td style="padding:28px 32px 0 32px;">
          <h1 style="font-family:{FONT_STACK};font-size:28px;line-height:1.2;
                     color:{NAVY};margin:0 0 16px 0;letter-spacing:-0.02em;">{subject}</h1>
          <p style="font-size:16px;line-height:1.65;color:{INK};margin:0;">{intro}</p>
          {pick_html}
          {sections_html}
        </td></tr>

        <tr><td style="padding:32px;">
          <div style="margin-top:8px;padding-top:20px;border-top:1px solid {BORDER};
                      font-size:14px;line-height:1.65;color:{INK};">{closing}</div>
          <div style="margin-top:12px;font-size:13px;color:{MUTED};font-style:italic;">— {signature}</div>
        </td></tr>

        <tr><td style="padding:18px 32px 28px 32px;background:{SOFT_BG};
                       font-size:11px;color:{MUTED};text-align:center;
                       letter-spacing:0.04em;">
          You received this because you subscribed to Warren · UK personal finance, weekly.
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>
"""


def to_text(newsletter: Dict[str, Any]) -> str:
    out = []
    subj = newsletter.get("subject_line", "") or ""
    out += [subj, "=" * max(len(subj), 3), ""]
    if newsletter.get("edition_label"):
        out += [newsletter["edition_label"], ""]
    out += [newsletter.get("intro", ""), ""]

    pick = newsletter.get("editor_pick")
    if pick:
        out += ["★ EDITOR'S PICK",
                pick.get("title", ""),
                pick.get("url", ""),
                pick.get("why", ""), ""]

    for section in newsletter.get("sections", []):
        h = section.get("heading", "") or ""
        out += [h, "-" * max(len(h), 3)]
        if section.get("summary"):
            out += [section["summary"], ""]
        for a in section.get("articles", []):
            out += [f"* {a.get('title', '')}", f"  {a.get('url', '')}"]
            if a.get("source"):         out.append(f"  [{a['source']}]")
            if a.get("blurb"):          out.append(f"  {a['blurb']}")
            if a.get("why_it_matters"): out.append(f"  → Why it matters: {a['why_it_matters']}")
            out.append("")
        if section.get("commentary"):
            out += [f"Editor: {section['commentary']}", ""]

    out += ["---", newsletter.get("closing", ""),
            f"— {newsletter.get('signature', 'The Warren Editorial Desk')}"]
    return "\n".join(out)
