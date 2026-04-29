from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date

import dash
import dash_bootstrap_components as dbc
import pandas as pd
import plotly.express as px
from dash import Input, Output, State, ctx, dash_table, dcc, html, no_update

from src.blog_generator import generate_blog_post, blog_to_html, blog_to_text
from src.compliance import ensure_compliant, load_rulebook
from src.compliance.pipeline import init_compliance_tables
from src.config import build_anthropic_client, load_config
from src.database import get_connection, init_db, query_articles
from src.exporters import to_pdf, to_docx, to_markdown, to_eml
from src.formatter import to_html, to_text
from src.generator import generate_newsletter

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

VENV_PYTHON = os.path.join(os.path.dirname(__file__), "venv", "bin", "python")
if not os.path.exists(VENV_PYTHON):
    VENV_PYTHON = sys.executable

cfg = load_config()

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.FLATLY],
    title="Warren Workflow",
    suppress_callback_exceptions=True,
)

_scrape: dict = {"proc": None, "output_file": None}

FREQUENCIES = ["daily", "weekly", "monthly"]


# ---------------------------------------------------------------------------
# /downloads/<filename> — serves any file from the configured output_dir.
# Path traversal is blocked by abspath comparison.
# ---------------------------------------------------------------------------
from flask import abort, send_from_directory  # noqa: E402

@app.server.route("/downloads/<path:filename>")
def _serve_download(filename: str):
    safe_dir = os.path.abspath(cfg.output_dir)
    target   = os.path.abspath(os.path.join(safe_dir, filename))
    if not target.startswith(safe_dir + os.sep) or not os.path.isfile(target):
        abort(404)
    return send_from_directory(safe_dir, filename, as_attachment=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_df() -> pd.DataFrame:
    try:
        conn = get_connection(cfg.db_path)
        init_db(conn)
        rows = conn.execute(
            "SELECT id, title, url, source, published_at, created_at, "
            "category, relevance_score, scrape_frequency, summary FROM articles "
            "ORDER BY COALESCE(published_at, created_at) DESC"
        ).fetchall()
        conn.close()
        if not rows:
            return pd.DataFrame()
        records = []
        for r in rows:
            sd = {}
            if r["summary"]:
                try:
                    sd = json.loads(r["summary"])
                except Exception:
                    pass
            records.append({
                "id":               r["id"],
                "title":            r["title"],
                "url":              r["url"],
                "source":           r["source"],
                "published_at":     (r["published_at"] or r["created_at"] or "")[:10],
                "category":         r["category"] or "other",
                "relevance_score":  r["relevance_score"] or 0,
                "scrape_frequency": r["scrape_frequency"] or "daily",
                "summary_text":     sd.get("summary", ""),
                "key_points":       sd.get("key_points", []),
            })
        return pd.DataFrame(records)
    except Exception:
        return pd.DataFrame()


def _stat_card(title: str, value: str, color: str = "primary") -> html.Div:
    """Branded stat tile (replaces dbc.Card variant)."""
    return html.Div([
        html.Div(title, className="label"),
        html.Div(value, className="value"),
    ], className="warren-stat-card")


def _page_header(title: str, subtitle: str | None = None) -> html.Div:
    """Consistent page heading with the gold accent bar."""
    bits = [
        html.Div([
            html.Span(className="accent"),
            html.H3(title),
        ], className="page-title"),
    ]
    if subtitle:
        bits.append(html.P(subtitle, className="page-subtitle"))
    return html.Div(bits)


def _running_badge():
    return dbc.Badge("● Running", color="success", className="fs-6 p-2")

def _idle_badge():
    return dbc.Badge("○ Idle", color="secondary", className="fs-6 p-2")


# ---------------------------------------------------------------------------
# Sidebar + layout
# ---------------------------------------------------------------------------

SIDEBAR = html.Div([
    html.Div([
        html.Div([
            html.Span("W", className="warren-brand-logo"),
            html.Div([
                html.Div("Warren", className="warren-brand-text"),
                html.Div("Workflow", className="warren-brand-sub"),
            ]),
        ], className="warren-brand-mark"),
    ], className="warren-brand"),

    dbc.Nav([
        dbc.NavLink(
            [html.Span("🗄", className="me-2"), "Database"],
            href="/",
            active="exact",
            className="sidebar-link fw-semibold",
        ),
        dbc.NavLink(
            [html.Span("✍", className="me-2"), "Create"],
            href="/create",
            active="exact",
            className="sidebar-link fw-semibold",
        ),
        dbc.NavLink(
            [html.Span("🛡", className="me-2"), "Compliance"],
            href="/compliance",
            active="exact",
            className="sidebar-link fw-semibold",
        ),
    ], vertical=True, pills=True, className="px-3 pt-3"),

    html.Div("UK Personal Finance · v1", className="warren-sidebar-footer"),
], className="warren-sidebar", style={
    "position": "fixed",
    "top": 0,
    "left": 0,
    "bottom": 0,
    "width": "230px",
    "zIndex": 100,
    "overflowY": "auto",
})

app.layout = html.Div([
    dcc.Location(id="url", refresh=False),
    SIDEBAR,
    html.Div(
        id="page-content",
        style={
            "marginLeft": "230px",
            "padding": "28px 36px 60px",
            "minHeight": "100vh",
        },
    ),
    # Shared stores / intervals
    dcc.Store(id="scrape-state", data={"running": False}),
    dcc.Interval(id="scrape-interval", interval=1000, disabled=True),
    dcc.Store(id="selected-article-ids", data=[]),
])

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

@app.callback(Output("page-content", "children"), Input("url", "pathname"))
def route(pathname):
    if pathname == "/create":
        return _create_page()
    if pathname == "/compliance":
        return _compliance_page()
    return _database_page()


# ===========================================================================
# DATABASE PAGE
# ===========================================================================

def _database_page():
    return html.Div([
        _page_header("Database",
                     "Articles fetched from RSS, GOV.UK and HTTP sources, deduped + scored."),
        dbc.Tabs([
            dbc.Tab(label="Overview",        tab_id="db-overview"),
            dbc.Tab(label="Article Browser", tab_id="db-browser"),
            dbc.Tab(label="Scrape",          tab_id="db-scrape"),
        ], id="db-tabs", active_tab="db-overview", className="mb-4"),
        html.Div(id="db-tab-content"),
    ])


@app.callback(Output("db-tab-content", "children"), Input("db-tabs", "active_tab"))
def render_db_tab(tab):
    if tab == "db-overview":
        return _overview_layout()
    if tab == "db-browser":
        return _browser_layout()
    if tab == "db-scrape":
        return _scrape_layout()
    return html.Div()


# --- Overview ---------------------------------------------------------------

def _overview_layout():
    return html.Div([
        dcc.Interval(id="overview-refresh", interval=30_000, n_intervals=0),
        html.Div(id="overview-content"),
    ])


@app.callback(Output("overview-content", "children"), Input("overview-refresh", "n_intervals"))
def refresh_overview(_):
    df = _get_df()
    if df.empty:
        return dbc.Alert("No articles yet — run a scrape first.", color="warning")

    total       = len(df)
    today_str   = date.today().isoformat()
    today_count = int((df["published_at"] == today_str).sum())
    sources     = df["source"].nunique()
    avg_score   = f"{df['relevance_score'].mean():.1f}"

    stat_row = dbc.Row([
        dbc.Col(_stat_card("Total Articles",  str(total),       "primary"), md=3),
        dbc.Col(_stat_card("Added Today",     str(today_count), "success"), md=3),
        dbc.Col(_stat_card("Active Sources",  str(sources),     "info"),    md=3),
        dbc.Col(_stat_card("Avg Relevance",   avg_score,        "warning"), md=3),
    ], className="mb-4 g-3")

    # By source
    src_df = df.groupby("source").size().reset_index(name="count").sort_values("count")
    fig_src = px.bar(src_df, x="count", y="source", orientation="h",
                     title="Articles by Source", color="count",
                     color_continuous_scale="Blues",
                     labels={"count": "Articles", "source": ""})
    fig_src.update_layout(coloraxis_showscale=False, plot_bgcolor="white",
                           margin=dict(l=10, r=10, t=40, b=10))

    # By category
    cat_df = df.groupby("category").size().reset_index(name="count")
    fig_cat = px.pie(cat_df, names="category", values="count",
                     title="Articles by Category", hole=0.4,
                     color_discrete_sequence=px.colors.qualitative.Set3)
    fig_cat.update_layout(margin=dict(l=10, r=10, t=40, b=10))

    # By frequency
    freq_df = df.groupby("scrape_frequency").size().reset_index(name="count")
    fig_freq = px.bar(freq_df, x="scrape_frequency", y="count",
                      title="Articles by Scrape Frequency",
                      color="scrape_frequency",
                      color_discrete_map={"daily": "#4CAF50", "weekly": "#2196F3", "monthly": "#FF9800"},
                      labels={"scrape_frequency": "Frequency", "count": "Articles"})
    fig_freq.update_layout(showlegend=False, plot_bgcolor="white",
                            margin=dict(l=10, r=10, t=40, b=10))

    # Relevance distribution
    fig_score = px.histogram(df, x="relevance_score", nbins=10,
                              title="Relevance Score Distribution",
                              color_discrete_sequence=["#2196F3"],
                              labels={"relevance_score": "Score"})
    fig_score.update_layout(plot_bgcolor="white", showlegend=False,
                             margin=dict(l=10, r=10, t=40, b=10))

    charts = dbc.Row([
        dbc.Col(dcc.Graph(figure=fig_src,   config={"displayModeBar": False}), md=6),
        dbc.Col(dcc.Graph(figure=fig_cat,   config={"displayModeBar": False}), md=6),
        dbc.Col(dcc.Graph(figure=fig_freq,  config={"displayModeBar": False}), md=6),
        dbc.Col(dcc.Graph(figure=fig_score, config={"displayModeBar": False}), md=6),
    ], className="g-3")

    return html.Div([stat_row, charts])


# --- Article Browser --------------------------------------------------------

def _browser_layout():
    df = _get_df()
    sources = sorted(df["source"].unique().tolist())    if not df.empty else []
    cats    = sorted(df["category"].unique().tolist())  if not df.empty else []

    return html.Div([
        dbc.Card(dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    dbc.Label("Source"),
                    dcc.Dropdown(id="br-source", options=[{"label": s, "value": s} for s in sources],
                                 placeholder="All sources", multi=True, clearable=True),
                ], md=3),
                dbc.Col([
                    dbc.Label("Category"),
                    dcc.Dropdown(id="br-category", options=[{"label": c.title(), "value": c} for c in cats],
                                 placeholder="All categories", multi=True, clearable=True),
                ], md=3),
                dbc.Col([
                    dbc.Label("Frequency"),
                    dcc.Dropdown(id="br-frequency",
                                 options=[{"label": f.title(), "value": f} for f in FREQUENCIES],
                                 placeholder="All frequencies", multi=True, clearable=True),
                ], md=3),
                dbc.Col([
                    dbc.Label("Min Score"),
                    dcc.Slider(id="br-score", min=1, max=10, step=1, value=1,
                               marks={i: str(i) for i in range(1, 11)},
                               tooltip={"always_visible": False}),
                ], md=3),
            ], className="g-3"),
        ]), className="mb-3 shadow-sm"),

        dash_table.DataTable(
            id="br-table",
            columns=[
                {"name": "Title",     "id": "title",            "presentation": "markdown"},
                {"name": "Source",    "id": "source"},
                {"name": "Category",  "id": "category"},
                {"name": "Frequency", "id": "scrape_frequency"},
                {"name": "Score",     "id": "relevance_score"},
                {"name": "Date",      "id": "published_at"},
            ],
            data=[],
            row_selectable="single",
            selected_rows=[],
            page_size=15,
            sort_action="native",
            filter_action="native",
            style_table={"overflowX": "auto"},
            style_header={"backgroundColor": "#2196F3", "color": "white", "fontWeight": "bold"},
            style_data_conditional=[
                {"if": {"row_index": "odd"}, "backgroundColor": "#f9f9f9"},
                {"if": {"state": "selected"}, "backgroundColor": "#e3f2fd", "border": "1px solid #2196F3"},
                {"if": {"filter_query": '{scrape_frequency} = "daily"',   "column_id": "scrape_frequency"}, "color": "#2e7d32", "fontWeight": "600"},
                {"if": {"filter_query": '{scrape_frequency} = "weekly"',  "column_id": "scrape_frequency"}, "color": "#1565c0", "fontWeight": "600"},
                {"if": {"filter_query": '{scrape_frequency} = "monthly"', "column_id": "scrape_frequency"}, "color": "#e65100", "fontWeight": "600"},
            ],
            style_cell={"textAlign": "left", "padding": "10px", "fontSize": "13px"},
            style_cell_conditional=[
                {"if": {"column_id": "title"}, "maxWidth": "380px", "overflow": "hidden", "textOverflow": "ellipsis"},
                {"if": {"column_id": "relevance_score"}, "width": "60px", "textAlign": "center"},
            ],
        ),
        html.Div(id="br-detail", className="mt-3"),
    ])


@app.callback(
    Output("br-table", "data"),
    Input("br-source",    "value"),
    Input("br-category",  "value"),
    Input("br-frequency", "value"),
    Input("br-score",     "value"),
)
def update_browser(sources, cats, freqs, min_score):
    df = _get_df()
    if df.empty:
        return []
    if sources:
        df = df[df["source"].isin(sources)]
    if cats:
        df = df[df["category"].isin(cats)]
    if freqs:
        df = df[df["scrape_frequency"].isin(freqs)]
    if min_score:
        df = df[df["relevance_score"] >= min_score]
    display = df.copy()
    display["title"] = "[" + display["title"] + "](" + display["url"] + ")"
    return display[["title", "source", "category", "scrape_frequency", "relevance_score", "published_at"]].to_dict("records")


@app.callback(
    Output("br-detail", "children"),
    Input("br-table", "selected_rows"),
    State("br-table", "data"),
)
def show_br_detail(selected_rows, data):
    if not selected_rows or not data:
        return html.Div()
    row = data[selected_rows[0]]
    raw_title = row["title"].split("](")[0].lstrip("[")
    df = _get_df()
    match = df[df["title"].str.startswith(raw_title[:40])]
    if match.empty:
        return html.Div()
    art = match.iloc[0]
    kps = art.get("key_points") or []
    freq = art["scrape_frequency"]
    freq_color = {"daily": "success", "weekly": "primary", "monthly": "warning"}.get(freq, "secondary")
    return dbc.Card(dbc.CardBody([
        html.H5(raw_title, className="fw-bold"),
        dbc.Badge(art["category"].title(), color="primary",  className="me-2"),
        dbc.Badge(f"Score: {art['relevance_score']}", color="warning", text_color="dark", className="me-2"),
        dbc.Badge(freq.title(), color=freq_color, className="me-2"),
        html.Hr(),
        html.P(art["summary_text"] or "No summary available.", className="text-muted"),
        html.Ul([html.Li(kp) for kp in kps]) if kps else None,
    ]), className="shadow-sm", style={"borderLeft": "4px solid #2196F3"})


# --- Scrape -----------------------------------------------------------------

def _all_sources() -> list[tuple[str, str]]:
    """Return [(source_key, display_name)] for every configured source."""
    from src.scraper import RSS_SOURCE_OVERRIDES
    GOVUK_NAMES = {
        "office-for-national-statistics": "Office For National Statistics",
        "hm-revenue-customs":             "Hm Revenue Customs",
    }
    return (
        [(url, RSS_SOURCE_OVERRIDES.get(url, url)) for url in cfg.rss_feeds] +
        [(slug, GOVUK_NAMES.get(slug, slug)) for slug in cfg.govuk_orgs] +
        [(url, url) for url in cfg.http_sources]
    )


def _schedule_table() -> dbc.Table:
    """Build a 'last scraped' table from the DB. No schedule, manual runs only."""
    from src.database import get_source_log
    from src.scraper import RSS_SOURCE_OVERRIDES

    GOVUK_NAMES = {
        "office-for-national-statistics": "Office For National Statistics",
        "hm-revenue-customs":             "Hm Revenue Customs",
    }

    try:
        conn = get_connection(cfg.db_path)
        init_db(conn)
        log_rows = {r["source_key"]: r for r in get_source_log(conn)}
        conn.close()
    except Exception:
        log_rows = {}

    all_sources = (
        [(url, RSS_SOURCE_OVERRIDES.get(url, url)) for url in cfg.rss_feeds] +
        [(slug, GOVUK_NAMES.get(slug, slug)) for slug in cfg.govuk_orgs]
    )

    rows = []
    for key, name in all_sources:
        row    = log_rows.get(key)
        last   = row["last_scraped_at"][:19].replace("T", " ") if (row and row["last_scraped_at"]) else "Never"
        rows.append(html.Tr([
            html.Td(name,  style={"fontWeight": "500"}),
            html.Td(last,  className="text-muted", style={"fontSize": "13px"}),
        ]))

    return dbc.Table(
        [html.Thead(html.Tr([html.Th("Source"), html.Th("Last Scraped")])),
         html.Tbody(rows)],
        bordered=False, hover=True, size="sm", className="mb-0",
    )


def _scrape_layout():
    return html.Div([
        # Schedule status card
        dbc.Card(dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    html.H6("Last Scraped", className="fw-bold text-primary mb-2"),
                    _schedule_table(),
                ], md=8),
                dbc.Col([
                    html.H6("Run Scrape", className="fw-bold text-primary mb-2"),
                    dbc.Button([
                        "▶  Scrape All Sources ",
                        html.Sup("ⓘ", style={"fontSize": "0.65em", "opacity": "0.7"}),
                    ], id="scrape-btn", color="primary", size="md", className="d-block w-100 mb-2"),
                    dbc.Tooltip("Manually scrapes every configured source right now. Scraping only runs when you trigger it.",
                                target="scrape-btn", placement="left"),

                    # Hidden legacy button — kept so existing callbacks remain valid.
                    dbc.Button("Force All", id="scrape-force-btn",
                               style={"display": "none"}, disabled=True),

                    dbc.Button([
                        "⏹  Stop ",
                        html.Sup("ⓘ", style={"fontSize": "0.65em", "opacity": "0.7"}),
                    ], id="scrape-stop-btn", color="danger", size="md", className="d-block w-100", disabled=True),
                    dbc.Tooltip("Terminates the running scrape process. Articles already summarised and stored will be kept.",
                                target="scrape-stop-btn", placement="left"),

                    html.Div(id="scrape-status-badge", className="mt-3 text-center"),
                ], md=4),
            ]),
        ]), className="mb-3 shadow-sm"),

        # On-demand source picker
        dbc.Card(dbc.CardBody([
            html.H6("On-Demand Scrape", className="fw-bold text-primary mb-2"),
            html.P("Pick any sources to scrape right now.",
                   className="text-muted small mb-3"),
            dbc.Row([
                dbc.Col([
                    dcc.Dropdown(
                        id="scrape-source-picker",
                        options=[{"label": name, "value": key} for key, name in _all_sources()],
                        multi=True,
                        placeholder="Select one or more sources…",
                    ),
                ], md=8),
                dbc.Col([
                    dbc.ButtonGroup([
                        dbc.Button("All", id="scrape-pick-all",  color="light", size="sm"),
                        dbc.Button("Clear", id="scrape-pick-clear", color="light", size="sm"),
                    ], className="me-2"),
                    dbc.Button([
                        "▶  Scrape Selected ",
                        html.Sup("ⓘ", style={"fontSize": "0.65em", "opacity": "0.7"}),
                    ], id="scrape-selected-btn", color="success", size="md", disabled=True),
                    dbc.Tooltip("Runs an immediate scrape against ONLY the sources you ticked above.",
                                target="scrape-selected-btn", placement="left"),
                ], md=4, className="text-end"),
            ]),
        ]), className="mb-3 shadow-sm"),

        # Live log
        dbc.Card(dbc.CardBody([
            html.H6("Live Output", className="text-muted mb-2"),
            html.Pre(id="scrape-log", children="No scrape run yet.", style={
                "backgroundColor": "#1e1e1e", "color": "#d4d4d4",
                "padding": "16px", "borderRadius": "6px",
                "height": "400px", "overflowY": "auto",
                "fontSize": "13px", "fontFamily": "monospace", "whiteSpace": "pre-wrap",
            }),
        ]), className="shadow-sm"),
    ])


@app.callback(
    Output("scrape-state",         "data"),
    Output("scrape-btn",           "disabled"),
    Output("scrape-force-btn",     "disabled"),
    Output("scrape-selected-btn",  "disabled", allow_duplicate=True),
    Output("scrape-stop-btn",      "disabled"),
    Output("scrape-interval",      "disabled"),
    Output("scrape-status-badge",  "children"),
    Input("scrape-btn",            "n_clicks"),
    Input("scrape-force-btn",      "n_clicks"),
    Input("scrape-selected-btn",   "n_clicks"),
    Input("scrape-stop-btn",       "n_clicks"),
    State("scrape-source-picker",  "value"),
    State("scrape-state",          "data"),
    prevent_initial_call=True,
)
def control_scrape(start_clicks, force_clicks, selected_clicks, stop_clicks,
                   selected_sources, state):
    trigger = ctx.triggered_id
    if trigger in ("scrape-btn", "scrape-force-btn", "scrape-selected-btn"):
        if _scrape.get("proc") and _scrape["proc"].poll() is None:
            return state, True, True, True, False, False, _running_badge()
        if trigger == "scrape-selected-btn" and not selected_sources:
            return state, False, False, True, True, True, _idle_badge()
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False)
        tmp.close()
        _scrape["output_file"] = tmp.name
        cmd = [VENV_PYTHON, "main.py", "scrape"]
        if trigger == "scrape-selected-btn":
            cmd += ["--sources", ",".join(selected_sources)]
        # scrape-btn / scrape-force-btn (legacy hidden) → scrape all sources, no flag
        _scrape["proc"] = subprocess.Popen(
            cmd,
            stdout=open(tmp.name, "w"),
            stderr=subprocess.STDOUT,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        return {"running": True}, True, True, True, False, False, _running_badge()
    if trigger == "scrape-stop-btn":
        if _scrape.get("proc") and _scrape["proc"].poll() is None:
            _scrape["proc"].terminate()
        return {"running": False}, False, False, False, True, True, _idle_badge()
    return state, False, False, False, True, True, _idle_badge()


@app.callback(
    Output("scrape-log",           "children"),
    Output("scrape-state",         "data",     allow_duplicate=True),
    Output("scrape-btn",           "disabled", allow_duplicate=True),
    Output("scrape-force-btn",     "disabled", allow_duplicate=True),
    Output("scrape-selected-btn",  "disabled", allow_duplicate=True),
    Output("scrape-stop-btn",      "disabled", allow_duplicate=True),
    Output("scrape-interval",      "disabled", allow_duplicate=True),
    Output("scrape-status-badge",  "children", allow_duplicate=True),
    Input("scrape-interval",       "n_intervals"),
    State("scrape-source-picker",  "value"),
    State("scrape-state",          "data"),
    prevent_initial_call=True,
)
def poll_scrape(_, picker_value, state):
    output_file = _scrape.get("output_file")
    proc        = _scrape.get("proc")
    sel_disabled_idle = not bool(picker_value)
    if not output_file or not proc:
        return no_update, state, False, False, sel_disabled_idle, True, True, _idle_badge()
    try:
        text = open(output_file).read()
    except Exception:
        text = ""
    if proc.poll() is None:
        return text or "Starting…", {"running": True}, True, True, True, False, False, _running_badge()
    return text or "(no output)", {"running": False}, False, False, sel_disabled_idle, True, True, _idle_badge()


@app.callback(
    Output("scrape-source-picker", "value"),
    Input("scrape-pick-all",   "n_clicks"),
    Input("scrape-pick-clear", "n_clicks"),
    prevent_initial_call=True,
)
def picker_quick_actions(_all, _clear):
    if ctx.triggered_id == "scrape-pick-all":
        return [k for k, _ in _all_sources()]
    return []


@app.callback(
    Output("scrape-selected-btn", "disabled"),
    Input("scrape-source-picker", "value"),
    State("scrape-state",         "data"),
)
def toggle_selected_btn(picker_value, state):
    if state and state.get("running"):
        return True
    return not bool(picker_value)


# ===========================================================================
# CREATE NEW CONTENT PAGE
# ===========================================================================

def _create_page():
    df = _get_df()
    sources = sorted(df["source"].unique().tolist())   if not df.empty else []
    cats    = sorted(df["category"].unique().tolist()) if not df.empty else []

    # ------------ LEFT PANE: filters + article picker ------------
    left_pane = html.Div([
        html.Div([
            html.Div("Step 1 · Filter & pick articles", className="section-eyebrow"),
            dbc.Row([
                dbc.Col([
                    dbc.Label("Source"),
                    dcc.Dropdown(id="cr-source",
                                 options=[{"label": s, "value": s} for s in sources],
                                 placeholder="All sources", multi=True, clearable=True),
                ], md=4),
                dbc.Col([
                    dbc.Label("Category"),
                    dcc.Dropdown(id="cr-category",
                                 options=[{"label": c.title(), "value": c} for c in cats],
                                 placeholder="All categories", multi=True, clearable=True),
                ], md=4),
                dbc.Col([
                    dbc.Label([
                        "Time frame ",
                        html.Sup("ⓘ", id="cr-timeframe-info",
                                 style={"fontSize": "0.7em", "opacity": "0.6", "cursor": "help"}),
                    ]),
                    dcc.Dropdown(id="cr-timeframe",
                                 options=[
                                     {"label": "Last 24h",  "value": "daily"},
                                     {"label": "Last 7d",   "value": "weekly"},
                                     {"label": "Last 30d",  "value": "monthly"},
                                     {"label": "All time",  "value": "all"},
                                 ],
                                 value="weekly", clearable=False),
                    dbc.Tooltip("Restricts the candidate pool to articles published within this window.",
                                target="cr-timeframe-info", placement="top"),
                ], md=4),
            ], className="g-3"),
            dbc.Row([
                dbc.Col([
                    dbc.Label("Min relevance score"),
                    dcc.Slider(id="cr-score", min=1, max=10, step=1, value=6,
                               marks={i: str(i) for i in range(1, 11)},
                               tooltip={"always_visible": False}),
                ], md=12),
            ], className="g-3 mt-1"),
            dbc.Row([
                dbc.Col([
                    dbc.Button("Select all (filtered)", id="cr-select-all",
                               color="outline-primary", size="sm", className="me-2"),
                    dbc.Button("Clear selection", id="cr-clear",
                               color="outline-secondary", size="sm"),
                ], md=12, className="mt-3"),
            ]),
        ], className="create-filter-strip"),

        html.Div(
            dash_table.DataTable(
                id="cr-table",
                columns=[
                    {"name": "",         "id": "select_hint",    "presentation": "markdown"},
                    {"name": "Title",    "id": "title"},
                    {"name": "Source",   "id": "source"},
                    {"name": "Category", "id": "category"},
                    {"name": "Score",    "id": "relevance_score"},
                    {"name": "Date",     "id": "published_at"},
                ],
                data=[],
                row_selectable="multi",
                selected_rows=[],
                page_size=14,
                sort_action="native",
                style_as_list_view=True,
                style_table={"overflowX": "auto"},
                style_data_conditional=[
                    {"if": {"row_index": "odd"},
                     "backgroundColor": "rgba(11,37,69,0.02)"},
                    {"if": {"state": "selected"},
                     "backgroundColor": "rgba(201,162,39,0.16)",
                     "border": "1px solid rgba(11,37,69,0.18)"},
                ],
                style_cell={"textAlign": "left", "padding": "10px 12px", "fontSize": "13px",
                            "fontFamily": "var(--font-body)", "color": "var(--warren-ink)"},
                style_header={"fontFamily": "var(--font-body)"},
                style_cell_conditional=[
                    {"if": {"column_id": "title"},  "maxWidth": "380px",
                     "overflow": "hidden", "textOverflow": "ellipsis", "fontWeight": "600"},
                    {"if": {"column_id": "select_hint"}, "width": "10px"},
                ],
            ),
            className="create-table-wrap",
        ),
    ], className="create-pane-left")

    # ------------ RIGHT PANE: type + sticky generate + output ------------
    right_pane = html.Div([
        # Selection summary chip
        html.Div(html.Div(id="cr-selected-badge"), className="mb-2"),

        # Step 2 — content type tiles
        dbc.Card(dbc.CardBody([
            html.Div("Step 2 · Content type", className="section-eyebrow"),
            html.Div([
                html.Div([
                    html.Div([
                        html.Div("✉", className="icon"),
                        html.Div("Newsletter", className="fw-bold mt-1"),
                        html.Div("Email digest with sections + editor pick.",
                                 className="text-muted small mt-1"),
                    ], style={"padding": "16px 14px", "textAlign": "center"}),
                ], id="cr-type-newsletter", n_clicks=0,
                   className="type-tile"),
                html.Div(style={"height": "10px"}),
                html.Div([
                    html.Div([
                        html.Div("📝", className="icon"),
                        html.Div("Blog Post", className="fw-bold mt-1"),
                        html.Div("Long-form analysis with TL;DR + FAQ.",
                                 className="text-muted small mt-1"),
                    ], style={"padding": "16px 14px", "textAlign": "center"}),
                ], id="cr-type-blog", n_clicks=0,
                   className="type-tile"),
            ]),
            dcc.Store(id="cr-content-type", data=None),
        ]), className="mb-3"),

        # Step 3 — sticky generate bar
        html.Div([
            html.Div("Step 3 · Generate", className="section-eyebrow",
                     style={"color": "rgba(255,255,255,0.7)"}),
            dbc.Button("⚡  Generate", id="cr-generate-btn", disabled=True),
            html.Div(id="cr-generate-hint", className="hint",
                     children="Pick at least one article and a content type."),
            dbc.Tooltip("Runs the LLM draft → compliance check → multi-format export. "
                        "Takes 30–90 seconds.",
                        target="cr-generate-btn", placement="top"),
        ], className="generate-bar"),

        # Job state + polling for progress
        dcc.Store(id="cr-job-id"),
        dcc.Interval(id="cr-job-poll", interval=600, disabled=True),
    ], className="create-pane-right")

    return html.Div([
        _page_header("Create",
                     "Filter articles, pick a format, ship a polished newsletter or blog."),
        html.Div([left_pane, right_pane], className="create-shell"),
        # Output sits below the two-pane shell so previews can use the full width
        html.Div(id="cr-output", className="mt-4"),
    ])


# --- Create page callbacks --------------------------------------------------

_TIMEFRAME_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}


@app.callback(
    Output("cr-table", "data"),
    Input("cr-source",    "value"),
    Input("cr-category",  "value"),
    Input("cr-timeframe", "value"),
    Input("cr-score",     "value"),
)
def update_create_table(sources, cats, timeframe, min_score):
    df = _get_df()
    if df.empty:
        return []
    if sources:
        df = df[df["source"].isin(sources)]
    if cats:
        df = df[df["category"].isin(cats)]
    if timeframe and timeframe != "all":
        from datetime import datetime, timedelta
        cutoff = (datetime.utcnow() - timedelta(days=_TIMEFRAME_DAYS[timeframe])).strftime("%Y-%m-%d")
        df = df[df["published_at"] >= cutoff]
    if min_score:
        df = df[df["relevance_score"] >= min_score]
    display = df.copy()
    display["select_hint"] = ""
    return display[["select_hint", "title", "source", "category", "scrape_frequency",
                    "relevance_score", "published_at"]].to_dict("records")


@app.callback(
    Output("cr-table", "selected_rows", allow_duplicate=True),
    Input("cr-select-all", "n_clicks"),
    State("cr-table", "data"),
    prevent_initial_call=True,
)
def select_all(_, data):
    return list(range(len(data))) if data else []


@app.callback(
    Output("cr-table", "selected_rows", allow_duplicate=True),
    Input("cr-clear", "n_clicks"),
    prevent_initial_call=True,
)
def clear_selection(_):
    return []


@app.callback(
    Output("cr-selected-badge", "children"),
    Input("cr-table", "selected_rows"),
)
def update_selected_badge(selected):
    n = len(selected or [])
    if n == 0:
        return html.Span("No articles selected", className="text-muted small")
    return html.Span([
        html.Span("●", className="me-1"),
        f"{n} article{'s' if n != 1 else ''} selected",
    ], className="selection-chip")


@app.callback(
    Output("cr-type-newsletter", "className"),
    Output("cr-type-blog",       "className"),
    Output("cr-content-type",    "data"),
    Input("cr-type-newsletter",  "n_clicks"),
    Input("cr-type-blog",        "n_clicks"),
    State("cr-content-type",     "data"),
    prevent_initial_call=True,
)
def select_content_type(nl_clicks, blog_clicks, current):
    trigger = ctx.triggered_id
    if trigger == "cr-type-newsletter":
        return "type-tile selected", "type-tile", "newsletter"
    if trigger == "cr-type-blog":
        return "type-tile", "type-tile selected", "blog"
    return "type-tile", "type-tile", None


@app.callback(
    Output("cr-generate-btn",  "disabled", allow_duplicate=True),
    Output("cr-generate-hint", "children"),
    Input("cr-table",          "selected_rows"),
    Input("cr-content-type",   "data"),
    prevent_initial_call="initial_duplicate",
)
def update_generate_btn(selected, content_type):
    n = len(selected or [])
    if n == 0 and not content_type:
        return True, "Select at least one article and a content type first."
    if n == 0:
        return True, "Select at least one article."
    if not content_type:
        return True, "Choose a content type above."
    return False, f"Ready — {n} article{'s' if n != 1 else ''} selected as {content_type}."


# ---------------------------------------------------------------------------
# Generation jobs (background thread + polling) so the UI stays alive while
# Claude is drafting / compliance is running. Without this the whole callback
# blocks for 30–90s with no feedback.
# ---------------------------------------------------------------------------

_jobs: dict[str, dict] = {}        # job_id → {stage, label, started_at, error, result_div}
_jobs_lock = threading.Lock()

# Stage labels shown in the progress card. Must match what the worker sets.
_STAGES = [
    ("collect",    "Loading article context"),
    ("draft",      "Drafting with Claude"),
    ("compliance", "Compliance grading + revision"),
    ("export",     "Rendering PDF, DOCX, Markdown, EML"),
    ("done",       "Done"),
]


def _set_stage(job_id: str, stage_key: str, *, sub: str = "") -> None:
    with _jobs_lock:
        if job_id not in _jobs:
            return
        _jobs[job_id]["stage"] = stage_key
        _jobs[job_id]["sub"]   = sub


def _progress_card(job_id: str) -> html.Div:
    job = _jobs.get(job_id) or {}
    current_stage = job.get("stage", "collect")
    sub = job.get("sub", "")
    started = job.get("started_at") or time.time()
    elapsed = int(time.time() - started)

    # Stage list with check / spinner / dot icons.
    current_idx = next((i for i, (k, _) in enumerate(_STAGES) if k == current_stage), 0)
    rows = []
    for i, (key, label) in enumerate(_STAGES):
        if i < current_idx or key == "done" and current_stage == "done":
            icon = html.Span("✅", style={"width": "22px", "display": "inline-block"})
            cls  = "text-success fw-semibold"
        elif i == current_idx:
            icon = dbc.Spinner(size="sm", color="primary",
                               spinner_style={"width": "1rem", "height": "1rem",
                                              "marginRight": "4px"})
            cls  = "text-primary fw-bold"
        else:
            icon = html.Span("○", style={"width": "22px", "display": "inline-block",
                                          "color": "#c0c8d4"})
            cls  = "text-muted"
        sub_txt = (f"  —  {sub}" if (i == current_idx and sub) else "")
        rows.append(html.Div([icon, html.Span(label + sub_txt, className=cls)],
                              className="mb-2"))

    pct = int(((current_idx) / max(len(_STAGES) - 1, 1)) * 100)
    if current_stage == "done":
        pct = 100

    return html.Div([
        dbc.Card(dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    html.H6("Generating your content", className="fw-bold mb-1"),
                    html.Small(f"Elapsed: {elapsed}s", className="text-muted"),
                ], md=8),
                dbc.Col([
                    dbc.Badge(f"{pct}%", color="primary", className="float-end fs-6"),
                ], md=4),
            ], className="mb-3"),
            dbc.Progress(value=pct, striped=True, animated=(current_stage != "done"),
                         className="mb-3", style={"height": "8px"}),
            html.Div(rows),
        ]), className="shadow-sm mb-3", style={"borderLeft": "4px solid #1a4f8b"}),
    ])


def _build_summaries(rows_df, raw_by_id) -> list[dict]:
    summaries = []
    for _, row in rows_df.iterrows():
        excerpt = (raw_by_id.get(row["id"], "") or "").strip()
        if len(excerpt) > 1200:
            excerpt = excerpt[:1200].rsplit(" ", 1)[0] + "…"
        summaries.append({
            "title":           row["title"],
            "url":             row["url"],
            "source":          row["source"],
            "published_at":    row["published_at"],
            "summary":         row["summary_text"],
            "key_points":      row["key_points"] if "key_points" in row else [],
            "category":        row["category"],
            "relevance_score": row["relevance_score"],
            "excerpt":         excerpt,
        })
    return summaries


def _versioned_basename(prefix: str) -> str:
    """e.g. 'newsletter-2026-04-25-v3' — picks next free version under output_dir."""
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    n = 1
    while True:
        base = f"{prefix}-{today_str}-v{n}"
        if not os.path.exists(os.path.join(cfg.output_dir, f"{base}.html")):
            return base
        n += 1


def _write_all_formats(base: str, *, kind: str, result: dict, html_str: str,
                       text_str: str, subject: str, summaries_in: list,
                       compliance_dict: dict) -> dict:
    """Write html/txt/json/md/pdf/docx/eml in parallel. Returns {ext: abs_path}."""
    paths = {
        "html": os.path.join(cfg.output_dir, f"{base}.html"),
        "txt":  os.path.join(cfg.output_dir, f"{base}.txt"),
        "md":   os.path.join(cfg.output_dir, f"{base}.md"),
        "pdf":  os.path.join(cfg.output_dir, f"{base}.pdf"),
        "docx": os.path.join(cfg.output_dir, f"{base}.docx"),
        "eml":  os.path.join(cfg.output_dir, f"{base}.eml"),
        "json": os.path.join(cfg.output_dir, f"{base}.json"),
    }

    def _write_html():  open(paths["html"], "w").write(html_str)
    def _write_txt():   open(paths["txt"],  "w").write(text_str)
    def _write_md():    open(paths["md"],   "w").write(to_markdown(result, kind=kind))
    def _write_pdf():   open(paths["pdf"], "wb").write(to_pdf(html_str))
    def _write_docx():  open(paths["docx"],"wb").write(to_docx(result, kind=kind))
    def _write_eml():   open(paths["eml"], "wb").write(to_eml(html_str, text_str, subject))
    def _write_json():
        with open(paths["json"], "w") as f:
            json.dump({
                "kind": kind,
                "generated_at_utc": datetime.utcnow().isoformat(),
                "subject_or_title": subject,
                "input_articles": summaries_in,
                "result": result,
                "compliance_summary": (compliance_dict or {})
                    .get("final_grade", {}).get("summary", {}),
            }, f, indent=2, ensure_ascii=False, default=str)

    jobs = {
        "html": _write_html, "txt": _write_txt,  "md":   _write_md,
        "pdf":  _write_pdf,  "docx": _write_docx, "eml":  _write_eml,
        "json": _write_json,
    }
    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futs = {ex.submit(fn): ext for ext, fn in jobs.items()}
        for fut, ext in futs.items():
            try:
                fut.result()
            except Exception as e:
                print(f"{ext.upper()} export failed: {e}")
                paths.pop(ext, None)
    return paths


def _run_generation_job(job_id: str, summaries: list, content_type: str) -> None:
    """Heavy work: LLM draft → compliance → multi-format export. Runs in a thread."""
    try:
        client = build_anthropic_client(cfg)
        os.makedirs(cfg.output_dir, exist_ok=True)

        _set_stage(job_id, "draft", sub=f"{content_type} from {len(summaries)} article(s)")
        if content_type == "newsletter":
            result = generate_newsletter(summaries, client, cfg.anthropic_model)
            if not result:
                raise RuntimeError("Newsletter generation returned no content.")
            out_html, out_text = to_html(result), to_text(result)
            subject = result.get("subject_line", "UK Personal Finance Digest")
            base = _versioned_basename("newsletter")
            kind = "newsletter"
        elif content_type == "blog":
            result = generate_blog_post(summaries, client, cfg.anthropic_model)
            if not result:
                raise RuntimeError("Blog post generation returned no content.")
            out_html, out_text = blog_to_html(result), blog_to_text(result)
            subject = result.get("title", "Blog Post")
            base = _versioned_basename("blog")
            kind = "blog"
        else:
            raise RuntimeError(f"Unknown content type: {content_type}")

        _set_stage(job_id, "compliance")
        conn_c = get_connection(cfg.db_path)
        compliance = ensure_compliant(
            out_html, kind=kind, client=client, model=cfg.anthropic_model,
            grader_model=cfg.compliance_model, conn=conn_c,
            content_ref=f"{base}.html",
            progress_cb=lambda s: _set_stage(job_id, "compliance", sub=s),
        )
        out_html = compliance["final_content"]
        # Re-render text from the (possibly revised) HTML? Source of truth is the
        # `result` dict, so plaintext stays in sync — only HTML is mutated.

        _set_stage(job_id, "export", sub="7 formats in parallel")
        paths = _write_all_formats(base, kind=kind, result=result,
                                   html_str=out_html, text_str=out_text,
                                   subject=subject, summaries_in=summaries,
                                   compliance_dict=compliance)

        # --- Build final preview component ----------------------------------
        if kind == "newsletter":
            sections_list = []
            if result.get("edition_label"):
                sections_list.append(dbc.ListGroupItem(f"🗓️ {result['edition_label']}"))
            pick = result.get("editor_pick") or {}
            if pick.get("title"):
                sections_list.append(dbc.ListGroupItem(f"★ Editor's Pick: {pick['title'][:60]}"))
            sections_list += [
                dbc.ListGroupItem(f"📌 {s.get('heading','')} — {len(s.get('articles',[]))} article(s)")
                for s in result.get("sections", [])
            ]
            preview = _content_preview("✉️ Newsletter Generated", subject,
                                        paths, out_html, sections_list, compliance)
        else:
            meta_list = []
            rt = result.get("reading_time_minutes")
            if rt:                          meta_list.append(dbc.ListGroupItem(f"⏱️ {rt} min read"))
            if result.get("byline"):        meta_list.append(dbc.ListGroupItem(f"✍️ {result['byline']}"))
            kt = result.get("key_takeaways") or []
            if kt:                          meta_list.append(dbc.ListGroupItem(f"💡 {len(kt)} key takeaways"))
            meta_list += [dbc.ListGroupItem(f"📌 {s.get('heading','')}")
                          for s in result.get("sections", [])]
            if result.get("faqs"):          meta_list.append(dbc.ListGroupItem(f"❓ {len(result['faqs'])} FAQs"))
            if result.get("sources_cited"): meta_list.append(dbc.ListGroupItem(f"🔗 {len(result['sources_cited'])} sources cited"))
            if result.get("seo_tags"):      meta_list.append(dbc.ListGroupItem("🏷️ " + " ".join(f"#{t}" for t in result["seo_tags"])))
            preview = _content_preview("📝 Blog Post Generated", subject,
                                        paths, out_html, meta_list, compliance)

        with _jobs_lock:
            _jobs[job_id]["stage"]      = "done"
            _jobs[job_id]["sub"]        = ""
            _jobs[job_id]["result_div"] = preview
    except Exception as e:
        traceback.print_exc()
        with _jobs_lock:
            _jobs[job_id]["stage"] = "done"
            _jobs[job_id]["error"] = f"{type(e).__name__}: {e}"


@app.callback(
    Output("cr-output",      "children", allow_duplicate=True),
    Output("cr-job-id",      "data"),
    Output("cr-job-poll",    "disabled"),
    Output("cr-generate-btn","disabled", allow_duplicate=True),
    Input("cr-generate-btn",  "n_clicks"),
    State("cr-table",         "selected_rows"),
    State("cr-table",         "data"),
    State("cr-content-type",  "data"),
    prevent_initial_call=True,
)
def kickoff_generation(_, selected_rows, table_data, content_type):
    if not selected_rows or not table_data or not content_type:
        return (dbc.Alert("Nothing to generate — check your selections.",
                          color="warning"),
                no_update, True, False)

    selected_titles = [table_data[i]["title"] for i in selected_rows]
    df = _get_df()
    rows_df = df[df["title"].isin(selected_titles)]
    if rows_df.empty:
        return (dbc.Alert("Could not load article data.", color="danger"),
                no_update, True, False)

    # Pull raw_content excerpts so the editor synthesises real source material.
    try:
        conn_raw = get_connection(cfg.db_path)
        ids = [r["id"] for _, r in rows_df.iterrows()]
        placeholders = ",".join("?" * len(ids))
        raw_rows = conn_raw.execute(
            f"SELECT id, raw_content FROM articles WHERE id IN ({placeholders})", ids
        ).fetchall()
        conn_raw.close()
        raw_by_id = {r["id"]: (r["raw_content"] or "") for r in raw_rows}
    except Exception:
        raw_by_id = {}

    summaries = _build_summaries(rows_df, raw_by_id)
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {
            "stage": "collect", "sub": "", "started_at": time.time(),
            "error": None, "result_div": None,
        }
    threading.Thread(target=_run_generation_job,
                     args=(job_id, summaries, content_type),
                     daemon=True).start()
    return _progress_card(job_id), job_id, False, True


@app.callback(
    Output("cr-output",       "children", allow_duplicate=True),
    Output("cr-job-poll",     "disabled", allow_duplicate=True),
    Output("cr-generate-btn", "disabled", allow_duplicate=True),
    Input("cr-job-poll",      "n_intervals"),
    State("cr-job-id",        "data"),
    prevent_initial_call=True,
)
def poll_generation(_n, job_id):
    if not job_id or job_id not in _jobs:
        return no_update, True, False
    job = _jobs[job_id]
    if job.get("error"):
        msg = dbc.Alert([html.Strong("Generation failed: "), job["error"]],
                        color="danger")
        # Free the slot
        _jobs.pop(job_id, None)
        return msg, True, False
    if job.get("stage") == "done" and job.get("result_div") is not None:
        result = job["result_div"]
        _jobs.pop(job_id, None)
        return result, True, False
    # Still running — refresh the progress card.
    return _progress_card(job_id), False, True



def _compliance_card(compliance: dict) -> dbc.Card:
    """Render a card summarising the compliance result for the generated piece."""
    g       = (compliance or {}).get("final_grade", {}).get("summary", {})
    grade   = g.get("grade", "?")
    rate    = g.get("pass_rate", 0)
    failed  = g.get("failed", 0)
    total   = g.get("total", 0)
    iters   = compliance.get("iterations", 0)
    revised = compliance.get("revised", False)
    color   = {"pass": "success", "warn": "warning", "fail": "danger"}.get(grade, "secondary")
    icon    = {"pass": "✅", "warn": "⚠️", "fail": "❌"}.get(grade, "•")

    # Per-change rendering with §section pill extracted from the change string.
    import re as _re
    _SECTION_RE = _re.compile(r"§\s*([0-9]+(?:\.[0-9]+)*)")

    def _render_change(c: str) -> html.Span:
        m = _SECTION_RE.search(c or "")
        if m:
            sec = m.group(1)
            txt = _SECTION_RE.sub("", c).replace("()", "").strip(" .")
            return html.Span([
                dbc.Badge(f"§{sec}", color="warning", className="me-2",
                          style={"fontSize": "0.7rem"}),
                txt,
            ])
        return html.Span(c)

    grade_color = {"pass": "success", "warn": "warning", "fail": "danger"}
    audit_items = []
    for entry in compliance.get("audit", []) or []:
        i = entry.get("iteration", 0)
        g_i = entry.get("grade", "?")
        ch = entry.get("changes", []) or []
        if i == 0:
            audit_items.append(html.Li([
                html.Span("Initial grade: ", className="text-muted"),
                dbc.Badge(g_i.upper(), color=grade_color.get(g_i, "secondary"),
                          className="ms-1"),
            ], style={"marginBottom": "8px"}))
        else:
            audit_items.append(html.Li([
                html.Div([
                    html.Strong(f"Iteration {i} → "),
                    dbc.Badge(g_i.upper(), color=grade_color.get(g_i, "secondary"),
                              className="ms-1"),
                    html.Span(f"  ·  {len(ch)} change(s) applied",
                              className="text-muted ms-2", style={"fontSize": "0.85rem"}),
                ], className="mb-2"),
                html.Ul(
                    [html.Li(_render_change(c), style={"fontSize": "13px",
                                                       "marginBottom": "4px"})
                     for c in ch[:12]],
                    style={"marginTop": "4px", "marginBottom": "4px"},
                ),
            ], style={"marginBottom": "10px"}))

    failures = [e for e in compliance.get("final_grade", {}).get("expectations", [])
                if not e.get("passed")]
    fail_items = [
        html.Li([
            dbc.Badge(f"§{e.get('section','?')}", color="warning",
                      className="me-2", style={"fontSize": "0.7rem"}),
            html.Strong(e.get("text", "")),
            html.Br(),
            html.Small(e.get("evidence", "")[:160], className="text-muted"),
        ], style={"marginBottom": "10px"})
        for e in failures[:8]
    ]

    return dbc.Card(dbc.CardBody([
        dbc.Row([
            dbc.Col([
                html.Span(icon, style={"fontSize": "1.5rem"}),
                html.Span(" Compliance: ", className="ms-2"),
                dbc.Badge(grade.upper(), color=color, className="ms-1"),
                html.Span(f"  {int(rate*100)}% ({total - failed}/{total} checks passed)",
                          className="text-muted ms-2", style={"fontSize": "0.9rem"}),
            ], md=8),
            dbc.Col([
                dbc.Badge(f"Auto-revised x{iters}" if revised else "No revision needed",
                          color="info" if revised else "light", className="float-end"),
            ], md=4),
        ], className="mb-2"),
        dbc.Accordion([
            dbc.AccordionItem([
                html.H6("Audit trail", className="text-muted"),
                html.Ol(audit_items, style={"fontSize": "13px"}),
                html.H6("Outstanding issues", className="text-muted mt-3") if fail_items else None,
                html.Ul(fail_items) if fail_items else html.Em("None — all checks passed.",
                                                                className="text-success"),
            ], title="View compliance details"),
        ], start_collapsed=True, flush=True),
    ]), className="shadow-sm mb-3", style={"borderLeft": f"4px solid var(--bs-{color})"})


_FORMAT_META = {
    "html": ("🌐 HTML",     "primary",  "Open / send as web page"),
    "pdf":  ("📄 PDF",      "danger",   "Print / share as PDF"),
    "docx": ("📝 Word",     "info",     "Edit in Microsoft Word"),
    "md":   ("⌨ Markdown", "dark",     "Paste into a CMS / Substack"),
    "eml":  ("✉ Email",    "warning",  "Open in Mail / Outlook to send"),
    "txt":  ("📃 Plain",    "secondary","Plain text fallback"),
    "json": ("{ } JSON",    "light",    "Raw structured data for replay"),
}
# Order shown in the UI
_FORMAT_ORDER = ["html", "pdf", "docx", "md", "eml", "txt", "json"]


def _download_bar(paths: dict) -> html.Div:
    buttons = []
    for ext in _FORMAT_ORDER:
        p = paths.get(ext)
        if not p:
            continue
        label, color, tip = _FORMAT_META[ext]
        fname = os.path.basename(p)
        buttons.append(dbc.Button(
            label, href=f"/downloads/{fname}", external_link=True,
            download=fname, color=color, size="sm",
            className="me-2 mb-2", title=tip,
        ))
    if not buttons:
        return html.Div()
    return html.Div([
        html.Div("Download as:", className="text-muted small mb-2"),
        html.Div(buttons),
    ], className="mb-3")


def _content_preview(badge_title: str, content_title: str,
                     paths: dict, preview_html: str, meta_items: list,
                     compliance: dict | None = None) -> html.Div:
    primary = paths.get("html") or next(iter(paths.values()), "")
    return html.Div([
        dbc.Alert([
            html.Strong(f"✅ {badge_title}: {content_title}"),
            html.Br(),
            html.Small([
                "Saved to ",
                html.Code(os.path.dirname(primary), style={"fontSize": "0.78rem"}),
                f"  ({len(paths)} format(s))",
            ]),
        ], color="success", className="mb-3"),
        _download_bar(paths),
        _compliance_card(compliance) if compliance else html.Div(),
        dbc.Row([
            dbc.Col([
                dbc.Card(dbc.CardBody([
                    html.H6("Contents", className="text-muted mb-2"),
                    dbc.ListGroup(meta_items, flush=True),
                ]), className="shadow-sm h-100"),
            ], md=3),
            dbc.Col([
                dbc.Card(dbc.CardBody([
                    dbc.Row([
                        dbc.Col(html.H6("Preview", className="text-muted mb-0 mt-1"), md=6),
                        dbc.Col([
                            dbc.ButtonGroup([
                                dbc.Button("📱 Mobile",  id="preview-mobile",  color="light",
                                           size="sm", outline=True, n_clicks=0),
                                dbc.Button("💻 Tablet",  id="preview-tablet",  color="light",
                                           size="sm", outline=True, n_clicks=0),
                                dbc.Button("🖥 Desktop", id="preview-desktop", color="primary",
                                           size="sm", n_clicks=0),
                            ], size="sm"),
                        ], md=6, className="text-end"),
                    ], className="mb-2"),
                    html.Div(
                        html.Iframe(
                            id="preview-frame",
                            srcDoc=preview_html,
                            style={"width": "100%", "height": "780px",
                                   "border": "1px solid #e6e9ef", "borderRadius": "6px",
                                   "background": "#ffffff"},
                        ),
                        id="preview-frame-wrap",
                        style={"width": "100%", "transition": "width 0.2s ease",
                               "margin": "0 auto"},
                    ),
                ]), className="shadow-sm"),
            ], md=9),
        ], className="g-3"),
    ])


@app.callback(
    Output("preview-frame-wrap", "style"),
    Output("preview-mobile",  "color"),
    Output("preview-mobile",  "outline"),
    Output("preview-tablet",  "color"),
    Output("preview-tablet",  "outline"),
    Output("preview-desktop", "color"),
    Output("preview-desktop", "outline"),
    Input("preview-mobile",  "n_clicks"),
    Input("preview-tablet",  "n_clicks"),
    Input("preview-desktop", "n_clicks"),
    prevent_initial_call=True,
)
def _toggle_preview_width(_m, _t, _d):
    trig = ctx.triggered_id
    base = {"transition": "width 0.2s ease", "margin": "0 auto"}
    if trig == "preview-mobile":
        return ({**base, "width": "390px"},
                "primary", False, "light", True, "light", True)
    if trig == "preview-tablet":
        return ({**base, "width": "768px"},
                "light", True, "primary", False, "light", True)
    return ({**base, "width": "100%"},
            "light", True, "light", True, "primary", False)


# ===========================================================================
# COMPLIANCE PAGE
# ===========================================================================

def _compliance_page():
    rb = load_rulebook()

    # ---- Recent compliance log -----------------------------------------------
    log_rows = []
    try:
        conn = get_connection(cfg.db_path)
        init_compliance_tables(conn)
        rows = conn.execute(
            "SELECT created_at, content_type, content_ref, grade, pass_rate, "
            "failed_count, iterations, revised "
            "FROM compliance_log ORDER BY id DESC LIMIT 50"
        ).fetchall()
        conn.close()
        log_rows = [dict(r) for r in rows]
    except Exception:
        pass

    # ---- Article compliance summary ------------------------------------------
    article_stats = {"pass": 0, "warn": 0, "fail": 0, "ungraded": 0}
    flagged_articles: list[dict] = []
    try:
        conn = get_connection(cfg.db_path)
        init_db(conn)
        rows = conn.execute(
            "SELECT title, source, compliance_grade, compliance_notes, published_at, created_at "
            "FROM articles ORDER BY COALESCE(published_at, created_at) DESC"
        ).fetchall()
        conn.close()
        for r in rows:
            g = r["compliance_grade"] or "ungraded"
            article_stats[g] = article_stats.get(g, 0) + 1
            if g in ("warn", "fail"):
                try:
                    notes = json.loads(r["compliance_notes"] or "[]")
                except Exception:
                    notes = []
                flagged_articles.append({
                    "title":  r["title"],
                    "source": r["source"],
                    "grade":  g,
                    "notes":  notes,
                    "date":   (r["published_at"] or r["created_at"] or "")[:10],
                })
    except Exception:
        pass

    # ---- UI ------------------------------------------------------------------
    summary_cards = dbc.Row([
        dbc.Col(_stat_card("Hard Rules",     str(len(rb.hard_rules)),         "primary"),  md=3),
        dbc.Col(_stat_card("Principles",     str(len(rb.principles)),          "info"),     md=3),
        dbc.Col(_stat_card("Articles Pass",  str(article_stats.get("pass", 0)),"success"), md=3),
        dbc.Col(_stat_card("Articles Flagged",
                           str(article_stats.get("warn", 0) + article_stats.get("fail", 0)),
                           "warning"), md=3),
    ], className="g-3 mb-4")

    log_table = dbc.Table([
        html.Thead(html.Tr([html.Th(h) for h in
            ["When", "Type", "Reference", "Grade", "Pass Rate", "Failed", "Iters", "Revised"]])),
        html.Tbody([
            html.Tr([
                html.Td(r["created_at"][:19].replace("T", " ")),
                html.Td(dbc.Badge(r["content_type"], color="secondary")),
                html.Td(html.Code(r["content_ref"] or "—", style={"fontSize": "0.78rem"})),
                html.Td(dbc.Badge(r["grade"].upper(),
                                  color={"pass": "success", "warn": "warning",
                                         "fail": "danger"}.get(r["grade"], "secondary"))),
                html.Td(f"{int((r['pass_rate'] or 0)*100)}%"),
                html.Td(str(r["failed_count"] or 0)),
                html.Td(str(r["iterations"] or 0)),
                html.Td("Yes" if r["revised"] else "No"),
            ]) for r in log_rows
        ]) if log_rows else html.Tbody(html.Tr(html.Td("No compliance runs yet.",
                                       colSpan=8, className="text-muted text-center p-3")))
    ], hover=True, size="sm", className="mb-3")

    flagged_table = dbc.Table([
        html.Thead(html.Tr([html.Th(h) for h in ["Article", "Source", "Date", "Grade", "Issues"]])),
        html.Tbody([
            html.Tr([
                html.Td(a["title"][:80]),
                html.Td(a["source"], className="text-muted small"),
                html.Td(a["date"], className="text-muted small"),
                html.Td(dbc.Badge(a["grade"].upper(),
                                  color={"warn": "warning", "fail": "danger"}.get(a["grade"], "secondary"))),
                html.Td(html.Ul([html.Li(n, style={"fontSize": "12px"}) for n in (a["notes"] or [])[:3]],
                                style={"marginBottom": 0, "paddingLeft": "16px"})),
            ]) for a in flagged_articles[:30]
        ]) if flagged_articles else html.Tbody(html.Tr(html.Td("No flagged articles.",
                                       colSpan=5, className="text-muted text-center p-3")))
    ], hover=True, size="sm")

    rules_html = dbc.Accordion([
        dbc.AccordionItem([
            html.H6("Banned phrases", className="text-muted mt-2"),
            html.Ul([html.Li([html.Code(r.pattern), f"  — §{r.section}: {r.rationale}"])
                     for r in rb.hard_rules if r.kind == "banned_phrase"]),
            html.H6("Banned terms", className="text-muted mt-3"),
            html.Ul([html.Li([html.Code(r.pattern), f"  — §{r.section}: {r.rationale}"])
                     for r in rb.hard_rules if r.kind == "banned_term"]),
            html.H6("Banned topics", className="text-muted mt-3"),
            html.Ul([html.Li([html.Code(r.pattern), f"  — §{r.section}: {r.rationale}"])
                     for r in rb.hard_rules if r.kind == "banned_topic"]),
        ], title=f"Hard Rules ({len(rb.hard_rules)})"),
        dbc.AccordionItem([
            html.Ul([html.Li([html.Strong(p.title), f"  (§{p.section})", html.Br(),
                              html.Small(p.description, className="text-muted")])
                     for p in rb.principles]),
        ], title=f"Principles ({len(rb.principles)})"),
        dbc.AccordionItem([
            html.Ul([html.Li(html.Code(d), style={"marginBottom": "8px"})
                     for d in rb.canonical_disclaimers]),
        ], title=f"Canonical Disclaimers ({len(rb.canonical_disclaimers)})"),
    ], start_collapsed=True, flush=False, className="mb-4")

    return html.Div([
        html.H3("Compliance", className="fw-bold mb-1"),
        html.P([
            "Loaded from ", html.Code(rb.source_path),
            " · cached at ", html.Code("data/compliance_rules.json"),
        ], className="text-muted mb-4"),
        summary_cards,
        dbc.Card(dbc.CardBody([
            html.H5("Marketing Compliance Rulebook", className="fw-bold text-primary mb-3"),
            rules_html,
        ]), className="shadow-sm mb-4"),
        dbc.Card(dbc.CardBody([
            html.H5("Recent Compliance Runs", className="fw-bold text-primary mb-3"),
            log_table,
        ]), className="shadow-sm mb-4"),
        dbc.Card(dbc.CardBody([
            html.H5("Flagged Articles", className="fw-bold text-primary mb-3"),
            flagged_table,
        ]), className="shadow-sm mb-4"),
    ])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=False, port=8050)
