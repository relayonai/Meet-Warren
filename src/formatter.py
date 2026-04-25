from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from html import escape
from typing import Any, Dict, List, Tuple


# ---------- design tokens (kept inline so output is portable) ---------------
NAVY     = "#0b2545"
INK      = "#1a1f36"
MUTED    = "#5a6478"
BORDER   = "#e6e9ef"
ACCENT   = "#c9a227"   # warm gold
ACCENT_BG = "#fdf6e3"
SOFT_BG  = "#f6f8fb"
LINK     = "#1a4f8b"

# Dark-mode counterparts (used inside @media (prefers-color-scheme: dark))
DARK_BG       = "#0f1419"
DARK_CARD     = "#1a1f2a"
DARK_INK      = "#e6e9ef"
DARK_MUTED    = "#8892a3"
DARK_BORDER   = "#2a3140"
DARK_ACCENT_BG = "#2a2415"


# ---------------------------------------------------------------------------
# Auto-computed extras: "By the numbers" + "On the calendar"
# ---------------------------------------------------------------------------

def _flatten_articles(newsletter: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for s in newsletter.get("sections", []) or []:
        for a in s.get("articles", []) or []:
            out.append(a)
    return out


def _by_the_numbers(newsletter: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Returns a list of (label, value) tuples to render as a small stats strip."""
    arts = _flatten_articles(newsletter)
    if not arts:
        return []
    sources = Counter((a.get("source") or "Unknown") for a in arts)
    cats = Counter((a.get("category") or "other") for a in arts if a.get("category"))
    top_source, _ = sources.most_common(1)[0]
    top_cat = cats.most_common(1)[0][0] if cats else "—"
    return [
        ("Stories",       str(len(arts))),
        ("Sections",      str(len(newsletter.get("sections", []) or []))),
        ("Top outlet",    top_source),
        ("Top category",  top_cat.title()),
    ]


# Recurring + notable UK personal-finance dates. (month, day, label).
# Recurring entries repeat every year; absolute entries can be added with a year too.
_UK_MONEY_CALENDAR: List[Tuple[int, int, str]] = [
    (1,  31, "Self-Assessment online tax return deadline"),
    (4,   5, "End of UK tax year — last day to use this year's ISA / pension allowances"),
    (4,   6, "Start of new UK tax year — fresh ISA & pension allowances"),
    (7,  31, "Second payment on account due (Self-Assessment)"),
    (10, 31, "Self-Assessment paper return deadline"),
    (12, 31, "Calendar year end — review pensions / charity gift aid"),
]

# Bank of England MPC meeting dates often shift; keep the most likely upcoming windows
# as approximate "watch dates" — explicitly labelled so readers know to confirm.
_BOE_MPC_APPROX: List[Tuple[int, int]] = [
    (2, 6), (3, 20), (5, 8), (6, 19), (8, 7), (9, 18), (11, 6), (12, 18),
]


def _on_the_calendar(today: date | None = None, lookahead_days: int = 60) -> List[Tuple[str, str]]:
    """Returns up to 3 (date_str, label) tuples for upcoming UK money dates."""
    today = today or datetime.now(timezone.utc).date()
    upcoming: List[Tuple[date, str]] = []

    def _add(m: int, d: int, label: str) -> None:
        for yr in (today.year, today.year + 1):
            try:
                evt = date(yr, m, d)
            except ValueError:
                continue
            delta = (evt - today).days
            if 0 <= delta <= lookahead_days:
                upcoming.append((evt, label))
                return

    for m, d, label in _UK_MONEY_CALENDAR:
        _add(m, d, label)
    for m, d in _BOE_MPC_APPROX:
        _add(m, d, "Bank of England MPC decision (approx — confirm via BoE)")

    upcoming.sort(key=lambda x: x[0])
    out = []
    for evt, label in upcoming[:3]:
        out.append((evt.strftime("%a %d %b"), label))
    return out

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


def _by_the_numbers_block(stats: List[Tuple[str, str]]) -> str:
    if not stats:
        return ""
    cells = "".join(
        f'<td class="warren-stat" style="padding:14px 10px;text-align:center;'
        f'border-right:1px solid {BORDER};">'
        f'<div style="font-size:10px;font-weight:700;letter-spacing:0.12em;'
        f'text-transform:uppercase;color:{MUTED};margin-bottom:4px;">{escape(label)}</div>'
        f'<div style="font-size:18px;font-weight:700;color:{NAVY};line-height:1.2;">{escape(value)}</div>'
        f'</td>'
        for label, value in stats
    )
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'class="warren-stats-strip" '
        f'style="margin:24px 0 0 0;background:{SOFT_BG};border:1px solid {BORDER};'
        f'border-radius:8px;border-collapse:separate;">'
        f'<tr><td style="padding:6px 10px 0 14px;">'
        f'<div style="font-size:10px;font-weight:700;letter-spacing:0.14em;'
        f'text-transform:uppercase;color:{MUTED};">By the numbers</div>'
        f'</td></tr>'
        f'<tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0"><tr>{cells}</tr></table></td></tr></table>'
    )


def _calendar_block(items: List[Tuple[str, str]]) -> str:
    if not items:
        return ""
    rows = "".join(
        f'<tr>'
        f'<td class="warren-cal-date" style="padding:10px 14px;width:120px;color:{NAVY};'
        f'font-weight:700;font-size:13px;border-bottom:1px dashed {BORDER};white-space:nowrap;">'
        f'{escape(d)}</td>'
        f'<td class="warren-cal-label" style="padding:10px 14px;color:{INK};font-size:14px;'
        f'line-height:1.5;border-bottom:1px dashed {BORDER};">{escape(label)}</td>'
        f'</tr>'
        for d, label in items
    )
    return (
        f'<aside class="warren-cal" style="margin:28px 0 0 0;padding:18px 20px;'
        f'background:#ffffff;border:1px solid {BORDER};border-radius:10px;">'
        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">'
        f'<span style="display:inline-block;width:6px;height:14px;background:{ACCENT};'
        f'border-radius:2px;"></span>'
        f'<div style="font-size:11px;font-weight:700;letter-spacing:0.14em;'
        f'text-transform:uppercase;color:{NAVY};">On the calendar</div>'
        f'</div>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0">{rows}</table>'
        f'</aside>'
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
    stats_html    = _by_the_numbers_block(_by_the_numbers(newsletter))
    cal_html      = _calendar_block(_on_the_calendar())

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
  <meta name="color-scheme" content="light dark">
  <meta name="supported-color-schemes" content="light dark">
  <title>{subject}</title>
  <style>
    @media (prefers-color-scheme: dark) {{
      body, .warren-bg {{ background:{DARK_BG} !important; color:{DARK_INK} !important; }}
      .warren-card {{ background:{DARK_CARD} !important; box-shadow:none !important; }}
      .warren-card td, .warren-card div, .warren-card p, .warren-card span {{ color:{DARK_INK} !important; }}
      .warren-muted {{ color:{DARK_MUTED} !important; }}
      .warren-border, .warren-stats-strip, .warren-cal {{
        border-color:{DARK_BORDER} !important; background:{DARK_CARD} !important;
      }}
      .warren-stat {{ border-right-color:{DARK_BORDER} !important; }}
      .warren-soft {{ background:#222a37 !important; }}
      .warren-accent-bg {{ background:{DARK_ACCENT_BG} !important; }}
      .warren-headline, .warren-cal-date {{ color:#f1d785 !important; }}
      a {{ color:#9bbcec !important; }}
    }}
  </style>
</head>
<body class="warren-bg" style="margin:0;padding:0;background:{SOFT_BG};font-family:{FONT_STACK};color:{INK};">
  {preheader_span}
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:{SOFT_BG};padding:24px 12px;">
    <tr><td align="center">
      <table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0"
             class="warren-card"
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
          {stats_html}
          {pick_html}
          {cal_html}
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

    stats = _by_the_numbers(newsletter)
    if stats:
        out += ["BY THE NUMBERS"] + [f"  {l}: {v}" for l, v in stats] + [""]
    cal = _on_the_calendar()
    if cal:
        out += ["ON THE CALENDAR"] + [f"  {d} — {label}" for d, label in cal] + [""]

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
