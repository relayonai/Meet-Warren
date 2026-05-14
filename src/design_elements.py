"""Design helpers for blog post and newsletter visual elements.

Public API:
  is_markdown_table(chunk)            -> bool
  markdown_table_chunk_to_html(chunk) -> str   (inline md table -> styled HTML)
  render_table_html(ve)               -> str   (visual_elements table dict -> HTML)
  render_chart_js(ve, chart_id)       -> str   (Chart.js canvas + static CSS fallback)
  has_charts(visual_elements)         -> bool
  CHARTJS_CDN                         -> str   (script tag to inject in <head>)
"""
from __future__ import annotations

import json
import re
from html import escape
from typing import Optional

# Design tokens — match blog_generator.py and formatter.py
NAVY      = "#0b2545"
INK       = "#1a1f36"
MUTED     = "#5a6478"
BORDER    = "#e6e9ef"
ACCENT    = "#c9a227"
SOFT_BG   = "#f6f8fb"

CHARTJS_CDN = (
    '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/'
    'dist/chart.umd.min.js"></script>'
)

_CHART_COLORS = [
    "#0b2545", "#c9a227", "#1a4f8b", "#5a8f3c",
    "#8b3a1a", "#4a2545", "#1a6b5a", "#8b6e1a",
]

_TH_STYLE = (
    f"padding:10px 14px;text-align:left;background:{NAVY};color:#ffffff;"
    f"font-size:13px;font-weight:700;border:1px solid {BORDER};white-space:nowrap;"
)
_TD_BASE = (
    f"padding:9px 14px;font-size:13px;color:{INK};"
    f"border:1px solid {BORDER};line-height:1.45;"
)
_TD_ALT = _TD_BASE + f"background:{SOFT_BG};"


# ---------------------------------------------------------------------------
# Markdown table detection + conversion
# ---------------------------------------------------------------------------

def is_markdown_table(chunk: str) -> bool:
    """Return True if chunk looks like a markdown table block."""
    lines = [l for l in chunk.strip().split("\n") if l.strip()]
    if len(lines) < 3:
        return False
    if not all(l.strip().startswith("|") and l.strip().endswith("|") for l in lines):
        return False
    return any(re.match(r"^\|[\s\-:|]+\|$", l.strip()) for l in lines)


def _parse_md_table(chunk: str) -> tuple[list[str], list[list[str]]]:
    lines = [l.strip() for l in chunk.strip().split("\n") if l.strip()]
    headers = [c.strip() for c in lines[0].strip("|").split("|")]
    rows = [
        [c.strip() for c in l.strip("|").split("|")]
        for l in lines[2:]  # skip separator line
        if l.strip()
    ]
    return headers, rows


def _table_html(headers: list[str], rows: list[list[str]]) -> str:
    ths = "".join(f'<th style="{_TH_STYLE}">{escape(str(h))}</th>' for h in headers)
    trs = ""
    for i, row in enumerate(rows):
        style = _TD_ALT if i % 2 else _TD_BASE
        cells = "".join(f'<td style="{style}">{escape(str(c))}</td>' for c in row)
        trs += f"<tr>{cells}</tr>"
    return (
        f'<div style="overflow-x:auto;">'
        f'<table style="width:100%;border-collapse:collapse;">'
        f"<thead><tr>{ths}</tr></thead>"
        f"<tbody>{trs}</tbody>"
        f"</table></div>"
    )


def markdown_table_chunk_to_html(chunk: str) -> str:
    """Convert a markdown table chunk to a styled HTML table."""
    headers, rows = _parse_md_table(chunk)
    return (
        f'<div style="overflow-x:auto;margin:20px 0;">'
        f'<table style="width:100%;border-collapse:collapse;">'
        f'<thead><tr>'
        + "".join(f'<th style="{_TH_STYLE}">{escape(str(h))}</th>' for h in headers)
        + f'</tr></thead><tbody>'
        + "".join(
            f'<tr>'
            + "".join(
                f'<td style="{_TD_ALT if i % 2 else _TD_BASE}">{escape(str(c))}</td>'
                for c in row
            )
            + f'</tr>'
            for i, row in enumerate(rows)
        )
        + f'</tbody></table></div>'
    )


# ---------------------------------------------------------------------------
# Structured visual_elements table (from blog JSON)
# ---------------------------------------------------------------------------

def render_table_html(ve: dict) -> str:
    """Render a visual_elements table dict as a standalone styled block."""
    title   = ve.get("title", "")
    headers = ve.get("headers") or []
    rows    = ve.get("rows") or []

    if not headers and not rows:
        return ""

    title_html = (
        f'<div style="font-size:12px;font-weight:700;letter-spacing:0.1em;'
        f'text-transform:uppercase;color:{MUTED};margin-bottom:10px;">{escape(title)}</div>'
        if title else ""
    )

    return (
        f'<div style="margin:28px 0;padding:20px 22px;background:{SOFT_BG};'
        f'border:1px solid {BORDER};border-radius:10px;">'
        f"{title_html}"
        f"{_table_html(headers, rows)}"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Chart rendering — Chart.js (interactive) + CSS fallback (PDF / no-JS)
# ---------------------------------------------------------------------------

def _chart_static_html(ve: dict) -> str:
    """CSS-only chart shown by default; hidden by JS when Chart.js initialises."""
    chart_type = ve.get("type", "chart_bar")
    labels = ve.get("labels") or []
    values = ve.get("values") or []
    unit   = ve.get("unit", "")

    if not labels or not values:
        return ""

    if chart_type == "chart_pie":
        rows = "".join(
            f'<tr>'
            f'<td style="padding:6px 10px;font-size:13px;color:{INK};">{escape(str(l))}</td>'
            f'<td style="padding:6px 10px;font-size:13px;font-weight:700;color:{NAVY};">'
            f'{escape(str(v))} {escape(unit)}</td>'
            f'</tr>'
            for l, v in zip(labels, values)
        )
        inner = f'<table style="border-collapse:collapse;width:auto;"><tbody>{rows}</tbody></table>'
    else:
        max_val = max((abs(float(v)) for v in values if isinstance(v, (int, float))), default=1) or 1
        bars = ""
        for label, value in zip(labels, values):
            try:
                pct = abs(float(value)) / max_val * 100
            except (TypeError, ValueError):
                pct = 0
            bars += (
                f'<div style="margin:5px 0;display:flex;align-items:center;gap:8px;">'
                f'<div style="width:110px;font-size:12px;color:{INK};text-align:right;'
                f'flex-shrink:0;line-height:1.3;">{escape(str(label))}</div>'
                f'<div style="flex:1;background:{BORDER};border-radius:3px;height:16px;">'
                f'<div style="width:{pct:.1f}%;background:{NAVY};height:16px;'
                f'border-radius:3px;min-width:2px;"></div></div>'
                f'<div style="font-size:12px;color:{MUTED};width:70px;flex-shrink:0;">'
                f'{escape(str(value))} {escape(unit)}</div>'
                f'</div>'
            )
        inner = bars

    return f'<div class="wc-chart-fallback">{inner}</div>'


def render_chart_js(ve: dict, chart_id: str) -> str:
    """Return a Chart.js canvas block with a CSS-only fallback for PDF / WeasyPrint.

    Strategy:
    - Static fallback div is visible by default (no display:none).
    - Canvas is display:none by default.
    - Inline script: if Chart.js is loaded, hides fallback and shows canvas.
    WeasyPrint executes no JS, so it shows the static bars; browsers show the
    interactive chart.
    """
    js_type_map = {"chart_bar": "bar", "chart_line": "line", "chart_pie": "doughnut"}
    js_type   = js_type_map.get(ve.get("type", "chart_bar"), "bar")
    title     = ve.get("title", "")
    labels    = ve.get("labels") or []
    values    = ve.get("values") or []
    unit      = ve.get("unit", "")
    is_pie    = js_type == "doughnut"

    title_html = (
        f'<div style="font-size:12px;font-weight:700;letter-spacing:0.1em;'
        f'text-transform:uppercase;color:{MUTED};margin-bottom:12px;">{escape(title)}</div>'
        if title else ""
    )
    static = _chart_static_html(ve)

    bg_colors  = json.dumps(_CHART_COLORS[:len(values)] if is_pie else _CHART_COLORS[0])
    unit_js    = json.dumps(unit)
    scales_js  = (
        "{}"
        if is_pie
        else json.dumps({
            "y": {"beginAtZero": False, "grid": {"color": "#e6e9ef"}},
            "x": {"grid": {"color": "#e6e9ef"}},
        })
    )

    script = (
        f"(function(){{"
        f"var el=document.getElementById('{chart_id}');"
        f"if(!el||typeof Chart==='undefined')return;"
        f"el.parentNode.querySelector('.wc-chart-fallback').style.display='none';"
        f"el.style.display='block';"
        f"new Chart(el.getContext('2d'),{{"
        f"type:'{js_type}',"
        f"data:{{"
        f"labels:{json.dumps(labels)},"
        f"datasets:[{{"
        f"label:{unit_js},"
        f"data:{json.dumps(values)},"
        f"backgroundColor:{bg_colors},"
        f"borderColor:{json.dumps(_CHART_COLORS[0])},"
        f"borderWidth:2,fill:false,tension:0.3"
        f"}}]}},"
        f"options:{{"
        f"responsive:true,"
        f"plugins:{{"
        f"legend:{{display:{'true' if is_pie else 'false'}}},"
        f"tooltip:{{callbacks:{{label:function(c){{return c.formattedValue+' '+{unit_js};}}}}}}"
        f"}},"
        f"scales:{scales_js}"
        f"}}}})}})()"
    )

    return (
        f'<div style="margin:28px 0;padding:20px 22px;background:{SOFT_BG};'
        f'border:1px solid {BORDER};border-radius:10px;">'
        f"{title_html}"
        f"{static}"
        f'<canvas id="{chart_id}" style="display:none;max-height:300px;"></canvas>'
        f"<script>{script}</script>"
        f"</div>"
    )


def has_charts(visual_elements: list) -> bool:
    return any(
        (ve.get("type") or "").startswith("chart_")
        for ve in (visual_elements or [])
    )
