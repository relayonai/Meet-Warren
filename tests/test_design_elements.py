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
