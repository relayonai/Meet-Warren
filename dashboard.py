from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, date

import dash
import dash_bootstrap_components as dbc
import pandas as pd
import plotly.express as px
from dash import Input, Output, State, ctx, dash_table, dcc, html, no_update

from src.blog_generator import generate_blog_post, blog_to_html, blog_to_text
from src.config import build_anthropic_client, load_config
from src.database import get_connection, init_db, query_articles
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


def _stat_card(title: str, value: str, color: str = "primary") -> dbc.Card:
    return dbc.Card(
        dbc.CardBody([
            html.P(title, className="text-muted mb-1", style={"fontSize": "0.82rem"}),
            html.H3(value, className=f"text-{color} mb-0 fw-bold"),
        ]),
        className="shadow-sm h-100",
    )


def _running_badge():
    return dbc.Badge("● Running", color="success", className="fs-6 p-2")

def _idle_badge():
    return dbc.Badge("○ Idle", color="secondary", className="fs-6 p-2")


# ---------------------------------------------------------------------------
# Sidebar + layout
# ---------------------------------------------------------------------------

SIDEBAR = html.Div([
    html.Div([
        html.Span("🇬🇧", style={"fontSize": "2rem"}),
        html.H5("Warren Workflow", className="fw-bold mb-0 mt-1"),
        html.P("UK Personal Finance", className="text-muted small mb-0"),
    ], className="text-center py-4 border-bottom"),

    dbc.Nav([
        dbc.NavLink(
            [html.Span("🗄️", className="me-2"), "Database"],
            href="/",
            active="exact",
            className="sidebar-link fw-semibold",
        ),
        dbc.NavLink(
            [html.Span("✍️", className="me-2"), "Create New Content"],
            href="/create",
            active="exact",
            className="sidebar-link fw-semibold",
        ),
    ], vertical=True, pills=True, className="px-3 pt-3"),
], style={
    "position": "fixed",
    "top": 0,
    "left": 0,
    "bottom": 0,
    "width": "220px",
    "backgroundColor": "#ffffff",
    "borderRight": "1px solid #dee2e6",
    "zIndex": 100,
    "overflowY": "auto",
})

app.layout = html.Div([
    dcc.Location(id="url", refresh=False),
    SIDEBAR,
    html.Div(
        id="page-content",
        style={
            "marginLeft": "220px",
            "padding": "28px 32px",
            "backgroundColor": "#f8f9fa",
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
    return _database_page()


# ===========================================================================
# DATABASE PAGE
# ===========================================================================

def _database_page():
    return html.Div([
        html.H3("Database", className="fw-bold mb-4"),
        dbc.Tabs([
            dbc.Tab(label="📊  Overview",        tab_id="db-overview"),
            dbc.Tab(label="📰  Article Browser", tab_id="db-browser"),
            dbc.Tab(label="🔄  Scrape",          tab_id="db-scrape"),
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

def _schedule_table() -> dbc.Table:
    """Build a live source schedule status table from the DB."""
    from src.database import get_source_log, is_source_due, FREQ_DAYS
    from src.scraper import RSS_SOURCE_OVERRIDES

    GOVUK_NAMES = {
        "office-for-national-statistics": "Office For National Statistics",
        "hm-revenue-customs":             "Hm Revenue Customs",
    }

    def _freq_for(name: str) -> str:
        sl = name.lower()
        for k, v in cfg.source_schedules.items():
            if k.lower() in sl or sl in k.lower():
                return v
        return "daily"

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
        freq   = _freq_for(name)
        row    = log_rows.get(key)
        last   = row["last_scraped_at"][:19].replace("T", " ") if (row and row["last_scraped_at"]) else "Never"
        due    = not row or not row["last_scraped_at"] or is_source_due(
            get_connection(cfg.db_path), key, freq
        )
        badge  = dbc.Badge("● Due",     color="success", className="me-1") if due \
            else dbc.Badge("○ Waiting", color="secondary", className="me-1")
        freq_color = {"daily": "success", "weekly": "primary", "monthly": "warning"}.get(freq, "secondary")
        rows.append(html.Tr([
            html.Td(name,  style={"fontWeight": "500"}),
            html.Td(dbc.Badge(freq.title(), color=freq_color)),
            html.Td(last,  className="text-muted", style={"fontSize": "13px"}),
            html.Td(badge),
        ]))

    return dbc.Table(
        [html.Thead(html.Tr([html.Th("Source"), html.Th("Frequency"), html.Th("Last Scraped"), html.Th("Status")])),
         html.Tbody(rows)],
        bordered=False, hover=True, size="sm", className="mb-0",
    )


def _scrape_layout():
    return html.Div([
        # Schedule status card
        dbc.Card(dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    html.H6("Source Schedule", className="fw-bold text-primary mb-2"),
                    _schedule_table(),
                ], md=8),
                dbc.Col([
                    html.H6("Run Options", className="fw-bold text-primary mb-2"),
                    dbc.Button([
                        "▶  Scrape Due Sources ",
                        html.Sup("ⓘ", style={"fontSize": "0.65em", "opacity": "0.7"}),
                    ], id="scrape-btn", color="primary", size="md", className="d-block w-100 mb-2"),
                    dbc.Tooltip("Fetches only sources that are due based on their schedule (daily / weekly / monthly). Skips sources scraped recently.",
                                target="scrape-btn", placement="left"),

                    dbc.Button([
                        "⚡  Force All Sources ",
                        html.Sup("ⓘ", style={"fontSize": "0.65em", "opacity": "0.7"}),
                    ], id="scrape-force-btn", color="outline-warning", size="md", className="d-block w-100 mb-2"),
                    dbc.Tooltip("Ignores the schedule and scrapes every source immediately. Use for a full refresh or after adding new sources.",
                                target="scrape-force-btn", placement="left"),

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
    Output("scrape-state",        "data"),
    Output("scrape-btn",          "disabled"),
    Output("scrape-force-btn",    "disabled"),
    Output("scrape-stop-btn",     "disabled"),
    Output("scrape-interval",     "disabled"),
    Output("scrape-status-badge", "children"),
    Input("scrape-btn",           "n_clicks"),
    Input("scrape-force-btn",     "n_clicks"),
    Input("scrape-stop-btn",      "n_clicks"),
    State("scrape-state",         "data"),
    prevent_initial_call=True,
)
def control_scrape(start_clicks, force_clicks, stop_clicks, state):
    trigger = ctx.triggered_id
    if trigger in ("scrape-btn", "scrape-force-btn"):
        if _scrape.get("proc") and _scrape["proc"].poll() is None:
            return state, True, True, False, False, _running_badge()
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False)
        tmp.close()
        _scrape["output_file"] = tmp.name
        cmd = [VENV_PYTHON, "main.py", "scrape"]
        if trigger == "scrape-force-btn":
            cmd.append("--force")
        _scrape["proc"] = subprocess.Popen(
            cmd,
            stdout=open(tmp.name, "w"),
            stderr=subprocess.STDOUT,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        return {"running": True}, True, True, False, False, _running_badge()
    if trigger == "scrape-stop-btn":
        if _scrape.get("proc") and _scrape["proc"].poll() is None:
            _scrape["proc"].terminate()
        return {"running": False}, False, False, True, True, _idle_badge()
    return state, False, False, True, True, _idle_badge()


@app.callback(
    Output("scrape-log",          "children"),
    Output("scrape-state",        "data",     allow_duplicate=True),
    Output("scrape-btn",          "disabled", allow_duplicate=True),
    Output("scrape-force-btn",    "disabled", allow_duplicate=True),
    Output("scrape-stop-btn",     "disabled", allow_duplicate=True),
    Output("scrape-interval",     "disabled", allow_duplicate=True),
    Output("scrape-status-badge", "children", allow_duplicate=True),
    Input("scrape-interval",      "n_intervals"),
    State("scrape-state",         "data"),
    prevent_initial_call=True,
)
def poll_scrape(_, state):
    output_file = _scrape.get("output_file")
    proc        = _scrape.get("proc")
    if not output_file or not proc:
        return no_update, state, False, True, True, _idle_badge()
    try:
        text = open(output_file).read()
    except Exception:
        text = ""
    if proc.poll() is None:
        return text or "Starting…", {"running": True}, True, True, False, False, _running_badge()
    return text or "(no output)", {"running": False}, False, False, True, True, _idle_badge()


# ===========================================================================
# CREATE NEW CONTENT PAGE
# ===========================================================================

def _create_page():
    df = _get_df()
    sources = sorted(df["source"].unique().tolist())   if not df.empty else []
    cats    = sorted(df["category"].unique().tolist()) if not df.empty else []

    return html.Div([
        html.H3("Create New Content", className="fw-bold mb-1"),
        html.P("Select articles from the database, choose a content type, then generate.",
               className="text-muted mb-4"),

        # --- Step 1: Filter + select articles ---
        dbc.Card(dbc.CardBody([
            html.H6("Step 1 — Select Articles", className="fw-bold text-primary mb-3"),
            dbc.Row([
                dbc.Col([
                    dbc.Label("Source"),
                    dcc.Dropdown(id="cr-source", options=[{"label": s, "value": s} for s in sources],
                                 placeholder="All sources", multi=True, clearable=True),
                ], md=3),
                dbc.Col([
                    dbc.Label("Category"),
                    dcc.Dropdown(id="cr-category", options=[{"label": c.title(), "value": c} for c in cats],
                                 placeholder="All categories", multi=True, clearable=True),
                ], md=3),
                dbc.Col([
                    dbc.Label("Frequency"),
                    dcc.Dropdown(id="cr-frequency",
                                 options=[{"label": f.title(), "value": f} for f in FREQUENCIES],
                                 placeholder="All frequencies", multi=True, clearable=True),
                ], md=3),
                dbc.Col([
                    dbc.Label("Min Score"),
                    dcc.Slider(id="cr-score", min=1, max=10, step=1, value=6,
                               marks={i: str(i) for i in range(1, 11)},
                               tooltip={"always_visible": False}),
                ], md=3),
            ], className="g-3 mb-3"),

            dbc.Row([
                dbc.Col([
                    dbc.Button(["Select All ", html.Sup("ⓘ", style={"fontSize": "0.65em", "opacity": "0.7"})],
                               id="cr-select-all", color="outline-primary", size="sm", className="me-2"),
                    dbc.Tooltip("Selects every article currently visible after applying your filters.",
                                target="cr-select-all", placement="top"),

                    dbc.Button(["Clear ", html.Sup("ⓘ", style={"fontSize": "0.65em", "opacity": "0.7"})],
                               id="cr-clear", color="outline-secondary", size="sm"),
                    dbc.Tooltip("Deselects all articles without changing your filters.",
                                target="cr-clear", placement="top"),
                ], md=6),
                dbc.Col(html.Div(id="cr-selected-badge", className="text-end"), md=6),
            ], className="mb-2"),

            dash_table.DataTable(
                id="cr-table",
                columns=[
                    {"name": "",         "id": "select_hint",    "presentation": "markdown"},
                    {"name": "Title",    "id": "title"},
                    {"name": "Source",   "id": "source"},
                    {"name": "Category", "id": "category"},
                    {"name": "Freq",     "id": "scrape_frequency"},
                    {"name": "Score",    "id": "relevance_score"},
                    {"name": "Date",     "id": "published_at"},
                ],
                data=[],
                row_selectable="multi",
                selected_rows=[],
                page_size=12,
                sort_action="native",
                style_table={"overflowX": "auto"},
                style_header={"backgroundColor": "#2196F3", "color": "white", "fontWeight": "bold"},
                style_data_conditional=[
                    {"if": {"row_index": "odd"}, "backgroundColor": "#f9f9f9"},
                    {"if": {"state": "selected"}, "backgroundColor": "#e8f5e9", "border": "1px solid #43a047"},
                ],
                style_cell={"textAlign": "left", "padding": "8px 10px", "fontSize": "13px"},
                style_cell_conditional=[
                    {"if": {"column_id": "title"},  "maxWidth": "360px", "overflow": "hidden", "textOverflow": "ellipsis"},
                    {"if": {"column_id": "select_hint"}, "width": "10px"},
                ],
            ),
        ]), className="mb-3 shadow-sm"),

        # --- Step 2: Content type ---
        dbc.Card(dbc.CardBody([
            html.H6("Step 2 — Choose Content Type", className="fw-bold text-primary mb-3"),
            dbc.Row([
                dbc.Col([
                    dbc.Card(dbc.CardBody([
                        html.H4("✉️", className="text-center mb-1", style={"fontSize": "2rem"}),
                        html.H6(["Newsletter ", html.Sup("ⓘ", style={"fontSize": "0.65em", "opacity": "0.7"})],
                                className="text-center fw-bold"),
                        html.P("A structured digest with sections, editor commentary, and a closing.",
                               className="text-muted small text-center mb-0"),
                    ]), id="cr-type-newsletter",
                       style={"cursor": "pointer", "border": "2px solid transparent"},
                       className="h-100"),
                    dbc.Tooltip("Generates a structured email digest with themed sections, article summaries, editor commentary, and a closing note.", target="cr-type-newsletter", placement="bottom"),
                ], md=4),
                dbc.Col([
                    dbc.Card(dbc.CardBody([
                        html.H4("📝", className="text-center mb-1", style={"fontSize": "2rem"}),
                        html.H6(["Blog Post ", html.Sup("ⓘ", style={"fontSize": "0.65em", "opacity": "0.7"})],
                                className="text-center fw-bold"),
                        html.P("A long-form article with intro, analysed sections, conclusion, and SEO tags.",
                               className="text-muted small text-center mb-0"),
                    ]), id="cr-type-blog",
                       style={"cursor": "pointer", "border": "2px solid transparent"},
                       className="h-100"),
                    dbc.Tooltip("Generates a long-form UK personal finance blog post with an intro, analysed sections, conclusion, and SEO tags for publishing.", target="cr-type-blog", placement="bottom"),
                ], md=4),
            ], className="g-3"),
            dcc.Store(id="cr-content-type", data=None),
        ]), className="mb-3 shadow-sm"),

        # --- Step 3: Generate ---
        dbc.Card(dbc.CardBody([
            html.H6("Step 3 — Generate", className="fw-bold text-primary mb-3"),
            dbc.Button(["⚡  Generate Content ", html.Sup("ⓘ", style={"fontSize": "0.65em", "opacity": "0.7"})],
                       id="cr-generate-btn", color="success", size="lg", disabled=True),
            dbc.Tooltip("Sends your selected articles to Claude to generate your chosen content type. This may take 10–30 seconds depending on article count.", target="cr-generate-btn", placement="top"),
            html.P(id="cr-generate-hint", className="text-muted small mt-2 mb-0",
                   children="Select at least one article and a content type first."),
        ]), className="mb-3 shadow-sm"),

        # --- Output ---
        dbc.Spinner(html.Div(id="cr-output"), color="success", type="border"),
    ])


# --- Create page callbacks --------------------------------------------------

@app.callback(
    Output("cr-table", "data"),
    Input("cr-source",    "value"),
    Input("cr-category",  "value"),
    Input("cr-frequency", "value"),
    Input("cr-score",     "value"),
)
def update_create_table(sources, cats, freqs, min_score):
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
    color = "success" if n > 0 else "secondary"
    return dbc.Badge(f"{n} article{'s' if n != 1 else ''} selected", color=color, className="fs-6 p-2")


@app.callback(
    Output("cr-type-newsletter", "style"),
    Output("cr-type-blog",       "style"),
    Output("cr-content-type",    "data"),
    Input("cr-type-newsletter",  "n_clicks"),
    Input("cr-type-blog",        "n_clicks"),
    State("cr-content-type",     "data"),
    prevent_initial_call=True,
)
def select_content_type(nl_clicks, blog_clicks, current):
    selected_style   = {"cursor": "pointer", "border": "2px solid #43a047", "backgroundColor": "#f1f8e9"}
    unselected_style = {"cursor": "pointer", "border": "2px solid transparent"}
    trigger = ctx.triggered_id
    if trigger == "cr-type-newsletter":
        return selected_style, unselected_style, "newsletter"
    if trigger == "cr-type-blog":
        return unselected_style, selected_style, "blog"
    return unselected_style, unselected_style, None


@app.callback(
    Output("cr-generate-btn",  "disabled"),
    Output("cr-generate-hint", "children"),
    Input("cr-table",          "selected_rows"),
    Input("cr-content-type",   "data"),
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


@app.callback(
    Output("cr-output",       "children"),
    Input("cr-generate-btn",  "n_clicks"),
    State("cr-table",         "selected_rows"),
    State("cr-table",         "data"),
    State("cr-content-type",  "data"),
    prevent_initial_call=True,
)
def generate_content(_, selected_rows, table_data, content_type):
    if not selected_rows or not table_data or not content_type:
        return dbc.Alert("Nothing to generate — check your selections.", color="warning")

    # Gather titles of selected rows, look up full records from DB
    selected_titles = [table_data[i]["title"] for i in selected_rows]
    df = _get_df()
    rows_df = df[df["title"].isin(selected_titles)]

    summaries = []
    for _, row in rows_df.iterrows():
        summaries.append({
            "title":        row["title"],
            "url":          row["url"],
            "summary":      row["summary_text"],
            "category":     row["category"],
            "relevance_score": row["relevance_score"],
        })

    if not summaries:
        return dbc.Alert("Could not load article data.", color="danger")

    client = build_anthropic_client(cfg)
    os.makedirs(cfg.output_dir, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")

    if content_type == "newsletter":
        result = generate_newsletter(summaries, client, cfg.anthropic_model)
        if not result:
            return dbc.Alert("Newsletter generation failed.", color="danger")
        out_html = to_html(result)
        out_text = to_text(result)
        subject  = result.get("subject_line", "UK Personal Finance Digest")
        html_path = os.path.join(cfg.output_dir, f"newsletter-{stamp}.html")
        text_path = os.path.join(cfg.output_dir, f"newsletter-{stamp}.txt")
        with open(html_path, "w") as f: f.write(out_html)
        with open(text_path, "w") as f: f.write(out_text)
        sections_list = [
            dbc.ListGroupItem(f"📌 {s.get('heading','')} — {len(s.get('articles',[]))} article(s)")
            for s in result.get("sections", [])
        ]
        return _content_preview("✉️ Newsletter Generated", subject, html_path, out_html, sections_list)

    if content_type == "blog":
        result = generate_blog_post(summaries, client, cfg.anthropic_model)
        if not result:
            return dbc.Alert("Blog post generation failed.", color="danger")
        out_html = blog_to_html(result)
        out_text = blog_to_text(result)
        title    = result.get("title", "Blog Post")
        html_path = os.path.join(cfg.output_dir, f"blog-{stamp}.html")
        text_path = os.path.join(cfg.output_dir, f"blog-{stamp}.txt")
        with open(html_path, "w") as f: f.write(out_html)
        with open(text_path, "w") as f: f.write(out_text)
        meta_list = [
            dbc.ListGroupItem(f"📌 {s.get('heading', '')}")
            for s in result.get("sections", [])
        ] + [dbc.ListGroupItem("🏷️ " + " ".join(f"#{t}" for t in result.get("seo_tags", [])))]
        return _content_preview("📝 Blog Post Generated", title, html_path, out_html, meta_list)

    return dbc.Alert("Unknown content type.", color="danger")


def _content_preview(badge_title: str, content_title: str, saved_path: str,
                     preview_html: str, meta_items: list) -> html.Div:
    return html.Div([
        dbc.Alert([
            html.Strong(f"✅ {badge_title}: {content_title}"),
            html.Br(),
            html.Small(f"Saved to {saved_path}"),
        ], color="success", className="mb-3"),
        dbc.Row([
            dbc.Col([
                dbc.Card(dbc.CardBody([
                    html.H6("Contents", className="text-muted mb-2"),
                    dbc.ListGroup(meta_items, flush=True),
                ]), className="shadow-sm h-100"),
            ], md=3),
            dbc.Col([
                dbc.Card(dbc.CardBody([
                    html.H6("Preview", className="text-muted mb-2"),
                    html.Iframe(
                        srcDoc=preview_html,
                        style={"width": "100%", "height": "580px", "border": "none", "borderRadius": "4px"},
                    ),
                ]), className="shadow-sm"),
            ], md=9),
        ], className="g-3"),
    ])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=False, port=8050)
