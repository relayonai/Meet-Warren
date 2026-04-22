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
import plotly.graph_objects as go
from dash import Input, Output, State, ctx, dash_table, dcc, html, no_update

from src.config import build_anthropic_client, load_config
from src.database import get_connection, init_db, query_articles
from src.formatter import to_html, to_text
from src.generator import generate_newsletter

# ---------------------------------------------------------------------------
# App setup
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

# Module-level store for the running scrape subprocess (single-user personal tool).
_scrape: dict = {"proc": None, "output_file": None}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_df() -> pd.DataFrame:
    try:
        conn = get_connection(cfg.db_path)
        init_db(conn)
        rows = conn.execute(
            "SELECT id, title, url, source, published_at, created_at, "
            "category, relevance_score, summary FROM articles "
            "ORDER BY COALESCE(published_at, created_at) DESC"
        ).fetchall()
        conn.close()
        if not rows:
            return pd.DataFrame()
        records = []
        for r in rows:
            summary_data = {}
            if r["summary"]:
                try:
                    summary_data = json.loads(r["summary"])
                except Exception:
                    pass
            records.append({
                "id": r["id"],
                "title": r["title"],
                "url": r["url"],
                "source": r["source"],
                "published_at": (r["published_at"] or r["created_at"] or "")[:10],
                "category": r["category"] or "other",
                "relevance_score": r["relevance_score"] or 0,
                "summary_text": summary_data.get("summary", ""),
                "key_points": summary_data.get("key_points", []),
            })
        return pd.DataFrame(records)
    except Exception:
        return pd.DataFrame()


def _stat_card(title: str, value: str, color: str = "primary") -> dbc.Card:
    return dbc.Card(
        dbc.CardBody([
            html.P(title, className="text-muted mb-1", style={"fontSize": "0.85rem"}),
            html.H3(value, className=f"text-{color} mb-0 fw-bold"),
        ]),
        className="shadow-sm h-100",
    )


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

NAVBAR = dbc.Navbar(
    dbc.Container([
        html.Span("🇬🇧", style={"fontSize": "1.4rem", "marginRight": "10px"}),
        dbc.NavbarBrand("Warren Workflow", className="fw-bold fs-4"),
        dbc.Badge("UK Personal Finance", color="light", text_color="primary", className="ms-2"),
    ]),
    color="primary",
    dark=True,
    className="mb-4 shadow",
)

TABS = dbc.Tabs(
    [
        dbc.Tab(label="📊  Overview",         tab_id="tab-overview"),
        dbc.Tab(label="📰  Article Browser",   tab_id="tab-browser"),
        dbc.Tab(label="🔄  Scrape",            tab_id="tab-scrape"),
        dbc.Tab(label="✉️  Newsletter",         tab_id="tab-newsletter"),
    ],
    id="tabs",
    active_tab="tab-overview",
    className="mb-4",
)

app.layout = dbc.Container(
    [
        NAVBAR,
        TABS,
        html.Div(id="tab-content"),
        dcc.Store(id="scrape-state", data={"running": False, "output": ""}),
        dcc.Interval(id="scrape-interval", interval=1000, disabled=True),
        dcc.Store(id="newsletter-store", data=None),
    ],
    fluid=True,
    style={"backgroundColor": "#f8f9fa", "minHeight": "100vh", "paddingBottom": "40px"},
)

# ---------------------------------------------------------------------------
# Tab renderer
# ---------------------------------------------------------------------------

@app.callback(Output("tab-content", "children"), Input("tabs", "active_tab"))
def render_tab(tab):
    if tab == "tab-overview":
        return _overview_layout()
    if tab == "tab-browser":
        return _browser_layout()
    if tab == "tab-scrape":
        return _scrape_layout()
    if tab == "tab-newsletter":
        return _newsletter_layout()
    return html.Div()


# ---------------------------------------------------------------------------
# Tab 1 — Overview
# ---------------------------------------------------------------------------

def _overview_layout():
    return html.Div([
        dcc.Interval(id="overview-refresh", interval=30_000, n_intervals=0),
        html.Div(id="overview-content"),
    ])


@app.callback(Output("overview-content", "children"), Input("overview-refresh", "n_intervals"))
def refresh_overview(_):
    df = _get_df()

    if df.empty:
        return dbc.Alert("No articles in the database yet. Run a scrape first.", color="warning")

    total       = len(df)
    today       = date.today().isoformat()
    today_count = int((df["published_at"] == today).sum())
    sources     = df["source"].nunique()
    avg_score   = f"{df['relevance_score'].mean():.1f}"

    stat_row = dbc.Row([
        dbc.Col(_stat_card("Total Articles",    str(total),       "primary"),  md=3),
        dbc.Col(_stat_card("Added Today",       str(today_count), "success"),  md=3),
        dbc.Col(_stat_card("Active Sources",    str(sources),     "info"),     md=3),
        dbc.Col(_stat_card("Avg Relevance",     avg_score,        "warning"),  md=3),
    ], className="mb-4 g-3")

    # Articles by source
    src_counts = df.groupby("source").size().reset_index(name="count").sort_values("count", ascending=True)
    fig_source = px.bar(
        src_counts, x="count", y="source", orientation="h",
        title="Articles by Source", color="count",
        color_continuous_scale="Blues",
        labels={"count": "Articles", "source": ""},
    )
    fig_source.update_layout(coloraxis_showscale=False, plot_bgcolor="white", margin=dict(l=10, r=10, t=40, b=10))

    # Articles by category
    cat_counts = df.groupby("category").size().reset_index(name="count")
    fig_cat = px.pie(
        cat_counts, names="category", values="count",
        title="Articles by Category",
        color_discrete_sequence=px.colors.qualitative.Set3,
        hole=0.4,
    )
    fig_cat.update_layout(margin=dict(l=10, r=10, t=40, b=10))

    # Relevance score histogram
    fig_score = px.histogram(
        df, x="relevance_score", nbins=10,
        title="Relevance Score Distribution",
        color_discrete_sequence=["#2196F3"],
        labels={"relevance_score": "Score", "count": "Articles"},
    )
    fig_score.update_layout(plot_bgcolor="white", showlegend=False, margin=dict(l=10, r=10, t=40, b=10))

    # Articles over time
    daily = (
        df.groupby("published_at").size().reset_index(name="count")
          .sort_values("published_at")
    )
    daily = daily[daily["published_at"] != ""]
    fig_time = px.bar(
        daily, x="published_at", y="count",
        title="Articles by Date",
        color_discrete_sequence=["#2196F3"],
        labels={"published_at": "Date", "count": "Articles"},
    )
    fig_time.update_layout(plot_bgcolor="white", margin=dict(l=10, r=10, t=40, b=10))

    charts = dbc.Row([
        dbc.Col(dcc.Graph(figure=fig_source, config={"displayModeBar": False}), md=6),
        dbc.Col(dcc.Graph(figure=fig_cat,    config={"displayModeBar": False}), md=6),
        dbc.Col(dcc.Graph(figure=fig_score,  config={"displayModeBar": False}), md=6),
        dbc.Col(dcc.Graph(figure=fig_time,   config={"displayModeBar": False}), md=6),
    ], className="g-3")

    return html.Div([stat_row, charts])


# ---------------------------------------------------------------------------
# Tab 2 — Article Browser
# ---------------------------------------------------------------------------

def _browser_layout():
    df = _get_df()
    sources    = sorted(df["source"].unique().tolist())    if not df.empty else []
    categories = sorted(df["category"].unique().tolist())  if not df.empty else []

    return html.Div([
        dbc.Card(dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    dbc.Label("Source"),
                    dcc.Dropdown(
                        id="filter-source",
                        options=[{"label": s, "value": s} for s in sources],
                        placeholder="All sources",
                        multi=True,
                        clearable=True,
                    ),
                ], md=4),
                dbc.Col([
                    dbc.Label("Category"),
                    dcc.Dropdown(
                        id="filter-category",
                        options=[{"label": c.title(), "value": c} for c in categories],
                        placeholder="All categories",
                        multi=True,
                        clearable=True,
                    ),
                ], md=4),
                dbc.Col([
                    dbc.Label("Min Relevance Score"),
                    dcc.Slider(id="filter-score", min=1, max=10, step=1, value=1,
                               marks={i: str(i) for i in range(1, 11)},
                               tooltip={"always_visible": False}),
                ], md=4),
            ], className="g-3"),
        ]), className="mb-3 shadow-sm"),

        dash_table.DataTable(
            id="article-table",
            columns=[
                {"name": "Title",     "id": "title",            "presentation": "markdown"},
                {"name": "Source",    "id": "source"},
                {"name": "Category",  "id": "category"},
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
            ],
            style_cell={"textAlign": "left", "padding": "10px", "fontSize": "13px"},
            style_cell_conditional=[
                {"if": {"column_id": "title"},   "maxWidth": "420px", "overflow": "hidden", "textOverflow": "ellipsis"},
                {"if": {"column_id": "score"},   "width": "60px", "textAlign": "center"},
            ],
        ),

        html.Div(id="article-detail", className="mt-3"),
    ])


@app.callback(
    Output("article-table", "data"),
    Input("filter-source",   "value"),
    Input("filter-category", "value"),
    Input("filter-score",    "value"),
)
def update_table(sources, categories, min_score):
    df = _get_df()
    if df.empty:
        return []
    if sources:
        df = df[df["source"].isin(sources)]
    if categories:
        df = df[df["category"].isin(categories)]
    if min_score:
        df = df[df["relevance_score"] >= min_score]
    df["title"] = "[" + df["title"] + "](" + df["url"] + ")"
    return df[["title", "source", "category", "relevance_score", "published_at"]].to_dict("records")


@app.callback(
    Output("article-detail", "children"),
    Input("article-table", "selected_rows"),
    State("article-table", "data"),
)
def show_article_detail(selected_rows, data):
    if not selected_rows or not data:
        return html.Div()
    row = data[selected_rows[0]]
    df = _get_df()
    # Match by title substring (URL is embedded in markdown link)
    raw_title = row["title"].split("](")[0].lstrip("[")
    match = df[df["title"].str.startswith(raw_title[:40])]
    if match.empty:
        return html.Div()
    art = match.iloc[0]
    key_points = art.get("key_points") or []
    return dbc.Card(dbc.CardBody([
        html.H5(raw_title, className="fw-bold"),
        dbc.Badge(art["category"].title(), color="primary", className="me-2"),
        dbc.Badge(f"Score: {art['relevance_score']}", color="warning", text_color="dark"),
        html.Hr(),
        html.P(art["summary_text"] or "No summary available.", className="text-muted"),
        html.Ul([html.Li(kp) for kp in key_points]) if key_points else None,
    ]), className="shadow-sm border-primary", style={"borderLeft": "4px solid #2196F3"})


# ---------------------------------------------------------------------------
# Tab 3 — Scrape
# ---------------------------------------------------------------------------

def _scrape_layout():
    return html.Div([
        dbc.Card(dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    html.H5("Run Scrape Pipeline", className="fw-bold mb-1"),
                    html.P("Fetches from all configured sources, applies keyword pre-filter and relevance gate, then stores new articles.", className="text-muted mb-3"),
                    dbc.Button("▶  Start Scrape", id="scrape-btn", color="primary", size="lg"),
                    dbc.Button("⏹  Stop", id="scrape-stop-btn", color="danger", size="lg", className="ms-2", disabled=True),
                ], md=8),
                dbc.Col([
                    html.Div(id="scrape-status-badge"),
                ], md=4, className="d-flex align-items-center justify-content-end"),
            ]),
        ]), className="mb-3 shadow-sm"),

        dbc.Card(dbc.CardBody([
            html.H6("Live Output", className="text-muted mb-2"),
            html.Pre(
                id="scrape-log",
                children="No scrape run yet.",
                style={
                    "backgroundColor": "#1e1e1e",
                    "color": "#d4d4d4",
                    "padding": "16px",
                    "borderRadius": "6px",
                    "height": "420px",
                    "overflowY": "auto",
                    "fontSize": "13px",
                    "fontFamily": "monospace",
                    "whiteSpace": "pre-wrap",
                },
            ),
        ]), className="shadow-sm"),
    ])


@app.callback(
    Output("scrape-state",       "data"),
    Output("scrape-btn",         "disabled"),
    Output("scrape-stop-btn",    "disabled"),
    Output("scrape-interval",    "disabled"),
    Output("scrape-status-badge","children"),
    Input("scrape-btn",          "n_clicks"),
    Input("scrape-stop-btn",     "n_clicks"),
    State("scrape-state",        "data"),
    prevent_initial_call=True,
)
def control_scrape(start_clicks, stop_clicks, state):
    trigger = ctx.triggered_id
    if trigger == "scrape-btn":
        if _scrape.get("proc") and _scrape["proc"].poll() is None:
            return state, True, False, False, _running_badge()
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False)
        tmp.close()
        _scrape["output_file"] = tmp.name
        _scrape["proc"] = subprocess.Popen(
            [VENV_PYTHON, "main.py", "scrape"],
            stdout=open(tmp.name, "w"),
            stderr=subprocess.STDOUT,
            cwd=os.path.dirname(__file__),
        )
        return {"running": True, "output": ""}, True, False, False, _running_badge()

    if trigger == "scrape-stop-btn":
        if _scrape.get("proc") and _scrape["proc"].poll() is None:
            _scrape["proc"].terminate()
        return {"running": False, "output": state.get("output", "")}, False, True, True, _idle_badge()

    return state, False, True, True, _idle_badge()


@app.callback(
    Output("scrape-log",         "children"),
    Output("scrape-state",       "data", allow_duplicate=True),
    Output("scrape-btn",         "disabled", allow_duplicate=True),
    Output("scrape-stop-btn",    "disabled", allow_duplicate=True),
    Output("scrape-interval",    "disabled", allow_duplicate=True),
    Output("scrape-status-badge","children", allow_duplicate=True),
    Input("scrape-interval",     "n_intervals"),
    State("scrape-state",        "data"),
    prevent_initial_call=True,
)
def poll_scrape(_, state):
    output_file = _scrape.get("output_file")
    proc        = _scrape.get("proc")
    if not output_file or not proc:
        return no_update, state, False, True, True, _idle_badge()

    try:
        with open(output_file) as f:
            text = f.read()
    except Exception:
        text = ""

    still_running = proc.poll() is None
    if still_running:
        return text or "Starting…", {"running": True, "output": text}, True, False, False, _running_badge()
    else:
        return text or "(no output)", {"running": False, "output": text}, False, True, True, _idle_badge()


def _running_badge():
    return dbc.Badge("● Running", color="success", className="fs-6 p-2")


def _idle_badge():
    return dbc.Badge("○ Idle", color="secondary", className="fs-6 p-2")


# ---------------------------------------------------------------------------
# Tab 4 — Newsletter
# ---------------------------------------------------------------------------

def _newsletter_layout():
    df = _get_df()
    categories = sorted(df["category"].unique().tolist()) if not df.empty else []

    return html.Div([
        dbc.Card(dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    dbc.Label("Max Articles"),
                    dbc.Input(id="nl-limit", type="number", value=15, min=1, max=50),
                ], md=3),
                dbc.Col([
                    dbc.Label("Category Filter"),
                    dcc.Dropdown(
                        id="nl-category",
                        options=[{"label": c.title(), "value": c} for c in categories],
                        placeholder="All categories",
                        clearable=True,
                    ),
                ], md=3),
                dbc.Col([
                    dbc.Label("Since Date"),
                    dcc.DatePickerSingle(
                        id="nl-since",
                        placeholder="No limit",
                        display_format="YYYY-MM-DD",
                        clearable=True,
                        style={"width": "100%"},
                    ),
                ], md=3),
                dbc.Col([
                    dbc.Label("\u00a0"),
                    dbc.Button("✉️  Generate Newsletter", id="nl-btn", color="primary", size="md", className="d-block w-100"),
                ], md=3),
            ], className="g-3 align-items-end"),
        ]), className="mb-3 shadow-sm"),

        dbc.Spinner(
            html.Div(id="nl-output"),
            color="primary",
            type="border",
        ),
    ])


@app.callback(
    Output("nl-output", "children"),
    Input("nl-btn", "n_clicks"),
    State("nl-limit",    "value"),
    State("nl-category", "value"),
    State("nl-since",    "date"),
    prevent_initial_call=True,
)
def generate_nl(_, limit, category, since):
    conn = get_connection(cfg.db_path)
    init_db(conn)
    rows = query_articles(conn, limit=int(limit or 15), category=category, since=since)
    conn.close()

    if not rows:
        return dbc.Alert("No articles match those filters. Adjust and try again.", color="warning")

    summaries = []
    for row in rows:
        try:
            data = json.loads(row["summary"]) if row["summary"] else {}
        except Exception:
            data = {}
        data["url"] = row["url"]
        data.setdefault("title", row["title"])
        summaries.append(data)

    client = build_anthropic_client(cfg)
    newsletter = generate_newsletter(summaries, client, cfg.anthropic_model)
    if not newsletter:
        return dbc.Alert("Newsletter generation failed — check your API key and model.", color="danger")

    nl_html = to_html(newsletter)
    nl_text = to_text(newsletter)
    subject = newsletter.get("subject_line", "UK Personal Finance Digest")

    # Save locally
    os.makedirs(cfg.output_dir, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    html_path = os.path.join(cfg.output_dir, f"newsletter-{stamp}.html")
    text_path = os.path.join(cfg.output_dir, f"newsletter-{stamp}.txt")
    with open(html_path, "w") as f:
        f.write(nl_html)
    with open(text_path, "w") as f:
        f.write(nl_text)

    sections_summary = [
        dbc.ListGroupItem(f"📌 {s.get('heading', '')} — {len(s.get('articles', []))} article(s)")
        for s in newsletter.get("sections", [])
    ]

    return html.Div([
        dbc.Alert([
            html.Strong(f"✅ Generated: {subject}"),
            html.Br(),
            html.Small(f"Saved to {html_path}"),
        ], color="success", className="mb-3"),

        dbc.Row([
            dbc.Col([
                dbc.Card(dbc.CardBody([
                    html.H6("Sections", className="text-muted"),
                    dbc.ListGroup(sections_summary, flush=True),
                ]), className="shadow-sm h-100"),
            ], md=3),
            dbc.Col([
                dbc.Card(dbc.CardBody([
                    html.H6("Preview", className="text-muted mb-2"),
                    html.Iframe(
                        srcDoc=nl_html,
                        style={"width": "100%", "height": "560px", "border": "none", "borderRadius": "4px"},
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
