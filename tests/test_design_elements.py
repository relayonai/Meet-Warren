# tests/test_design_elements.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.design_elements import (
    render_stat_card_row,
    render_comparison_card,
    render_callout,
    render_email_stat_row,
    render_email_table,
    render_email_divider_callout,
    render_email_visual,
)


# ── stat_card_row ────────────────────────────────────────────────────────────

def test_stat_card_row_renders_cards():
    ve = {
        "type": "stat_card_row",
        "after_section": 0,
        "cards": [
            {"label": "Base Rate", "value": "5.25%", "note": "BoE, Mar 2026"},
            {"label": "ISA Allowance", "value": "£20,000", "note": "2025/26"},
        ],
    }
    html = render_stat_card_row(ve)
    assert "5.25%" in html
    assert "Base Rate" in html
    assert "£20,000" in html
    assert "ISA Allowance" in html
    assert "BoE, Mar 2026" in html


def test_stat_card_row_empty_returns_empty():
    assert render_stat_card_row({"type": "stat_card_row", "cards": []}) == ""
    assert render_stat_card_row({}) == ""


# ── comparison_card ──────────────────────────────────────────────────────────

def test_comparison_card_renders_table():
    ve = {
        "type": "comparison_card",
        "after_section": 1,
        "title": "Cash ISA vs S&S ISA",
        "columns": ["Feature", "Cash ISA", "S&S ISA"],
        "rows": [
            ["Annual allowance", "£20,000", "£20,000"],
            ["Risk", "Low", "Medium–High"],
        ],
    }
    html = render_comparison_card(ve)
    assert "Cash ISA vs S&amp;S ISA" in html
    assert "Feature" in html
    assert "Annual allowance" in html
    assert "Medium" in html


def test_comparison_card_empty_returns_empty():
    assert render_comparison_card({"columns": [], "rows": []}) == ""


# ── callout ──────────────────────────────────────────────────────────────────

def test_callout_renders_body():
    ve = {
        "type": "callout",
        "after_section": 2,
        "icon": "⚠",
        "heading": "Regulatory note",
        "body": "This is not financial advice.",
    }
    html = render_callout(ve)
    assert "Regulatory note" in html
    assert "This is not financial advice." in html
    assert "⚠" in html


def test_callout_empty_body_returns_empty():
    assert render_callout({"icon": "⚠", "heading": "Note", "body": ""}) == ""


# ── email_stat_row ───────────────────────────────────────────────────────────

def test_email_stat_row_renders():
    ve = {
        "type": "email_stat_row",
        "after_section": -1,
        "cards": [
            {"label": "Inflation", "value": "2.6%", "note": "ONS, Mar 2026"},
            {"label": "Base Rate",  "value": "5.25%", "note": "BoE"},
        ],
    }
    html = render_email_stat_row(ve)
    assert "2.6%" in html
    assert "Inflation" in html
    assert "5.25%" in html
    assert "<script" not in html
    assert "<canvas" not in html


def test_email_stat_row_empty_returns_empty():
    assert render_email_stat_row({"cards": []}) == ""


# ── email_table ──────────────────────────────────────────────────────────────

def test_email_table_renders():
    ve = {
        "type": "email_table",
        "after_section": 0,
        "title": "Best-buy savings",
        "headers": ["Provider", "Rate"],
        "rows": [["Nationwide", "4.75%"], ["Barclays", "4.50%"]],
    }
    html = render_email_table(ve)
    assert "Nationwide" in html
    assert "4.75%" in html
    assert "<script" not in html
    assert 'style="' in html  # all styles inline


def test_email_table_empty_returns_empty():
    assert render_email_table({"headers": [], "rows": []}) == ""


# ── email_divider_callout ────────────────────────────────────────────────────

def test_email_divider_callout_renders():
    ve = {
        "type": "email_divider_callout",
        "after_section": 1,
        "heading": "Key takeaway",
        "body": "Rates are falling but mortgage costs remain high.",
    }
    html = render_email_divider_callout(ve)
    assert "Key takeaway" in html
    assert "Rates are falling" in html
    assert "<script" not in html


def test_email_divider_callout_empty_body_returns_empty():
    assert render_email_divider_callout({"heading": "Note", "body": ""}) == ""


# ── render_email_visual dispatcher ──────────────────────────────────────────

def test_render_email_visual_dispatches_correctly():
    assert render_email_visual({"type": "email_stat_row", "cards": []}) == ""
    assert render_email_visual({"type": "unknown_type"}) == ""
    ve = {"type": "email_divider_callout", "heading": "H", "body": "Body text here."}
    assert "Body text here." in render_email_visual(ve)


# ── _render_ves dispatcher integration (via blog_to_html) ────────────────────

from src.blog_generator import blog_to_html

_MINIMAL_BLOG = {
    "title": "Test Post",
    "subtitle": "",
    "byline": "By the Warren Editorial Desk",
    "reading_time_minutes": 1,
    "seo_tags": ["test"],
    "intro": "Intro paragraph.",
    "sections": [{"heading": "Section One", "content": "Content here.", "pull_quote": ""}],
    "conclusion": "Conclusion.",
    "key_takeaways": [],
    "faqs": [],
    "sources_cited": [],
    "meta_description": "",
    "visual_elements": [],
    "hero_image_prompt": "",
}


def test_blog_to_html_renders_stat_card_row():
    post = {**_MINIMAL_BLOG, "visual_elements": [
        {"type": "stat_card_row", "after_section": -1,
         "cards": [{"label": "Base Rate", "value": "5.25%", "note": "BoE"}]},
    ]}
    html = blog_to_html(post)
    assert "5.25%" in html
    assert "Base Rate" in html


def test_blog_to_html_renders_comparison_card():
    post = {**_MINIMAL_BLOG, "visual_elements": [
        {"type": "comparison_card", "after_section": 0,
         "title": "ISA types",
         "columns": ["Feature", "Cash ISA"],
         "rows": [["Allowance", "£20,000"]]},
    ]}
    html = blog_to_html(post)
    assert "ISA types" in html
    assert "£20,000" in html


def test_blog_to_html_renders_callout():
    post = {**_MINIMAL_BLOG, "visual_elements": [
        {"type": "callout", "after_section": 0,
         "icon": "⚠", "heading": "Note", "body": "Important regulatory note here."},
    ]}
    html = blog_to_html(post)
    assert "Important regulatory note here." in html


# ── formatter.to_html visual injection integration ───────────────────────────

from src.formatter import to_html as nl_to_html

_MINIMAL_NL = {
    "subject_line": "Test Newsletter",
    "intro": "Welcome.",
    "sections": [
        {"heading": "Savings", "summary": "Rates high.", "articles": [], "commentary": ""}
    ],
    "editor_pick": None,
    "visual_elements": [],
}


def test_newsletter_to_html_injects_email_stat_row():
    nl = {**_MINIMAL_NL, "visual_elements": [
        {"type": "email_stat_row", "after_section": -1,
         "cards": [{"label": "Base Rate", "value": "5.25%", "note": "BoE"}]},
    ]}
    html = nl_to_html(nl)
    assert "5.25%" in html
    assert "<script" not in html


def test_newsletter_to_html_no_visuals_unchanged():
    html_without = nl_to_html(_MINIMAL_NL)
    nl_with_empty = {**_MINIMAL_NL, "visual_elements": []}
    html_with_empty = nl_to_html(nl_with_empty)
    assert html_without == html_with_empty
