# tests/test_visual_extractor.py
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock
from src.visual_extractor import extract_visuals, _content_to_text

_BLOG = {
    "title": "ISA Allowance 2026: What You Need to Know",
    "intro": "The ISA allowance stays at £20,000 for the 2026/27 tax year.",
    "sections": [
        {"heading": "Cash ISA vs S&S ISA", "content": "Cash ISAs offer 4.75% AER from leading providers."},
        {"heading": "What the rate hold means", "content": "With rates at 5.25%, savers benefit but borrowers pay more."},
    ],
    "conclusion": "Act before the April deadline to maximise your allowance.",
}

_NEWSLETTER = {
    "subject_line": "Rate hold, ISA deadline, spring budget fallout",
    "intro": "The Bank of England held rates at 5.25%.",
    "sections": [
        {
            "heading": "Savings",
            "summary": "Rates remain high for cash savers.",
            "articles": [
                {"blurb": "NS&I Premium Bond rate 4.4%.", "why_it_matters": "Best easy-access rate in a decade."}
            ],
            "commentary": "Consider locking in before cuts arrive.",
        }
    ],
}

_ARTICLES = [{"title": "BoE rate hold", "summary": "BoE held at 5.25%", "excerpt": ""}]

_BLOG_VISUALS = {
    "visual_elements": [
        {"type": "stat_card_row", "after_section": -1,
         "cards": [{"label": "ISA Allowance", "value": "£20,000", "note": "2026/27"}]},
        {"type": "comparison_card", "after_section": 0, "title": "ISA types",
         "columns": ["Feature", "Cash ISA", "S&S ISA"],
         "rows": [["Allowance", "£20,000", "£20,000"]]},
    ]
}

_EMAIL_VISUALS = {
    "visual_elements": [
        {"type": "email_stat_row", "after_section": -1,
         "cards": [{"label": "Base Rate", "value": "5.25%", "note": "BoE, Mar 2026"}]},
    ]
}


def _mock_client(response_json: dict):
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps(response_json))]
    client = MagicMock()
    client.messages.create.return_value = msg
    return client


def test_extract_visuals_blog_returns_list():
    client = _mock_client(_BLOG_VISUALS)
    result = extract_visuals(_BLOG, _ARTICLES, "blog", client, "claude-sonnet-4-5")
    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["type"] == "stat_card_row"


def test_extract_visuals_newsletter_returns_list():
    client = _mock_client(_EMAIL_VISUALS)
    result = extract_visuals(_NEWSLETTER, _ARTICLES, "newsletter", client, "claude-sonnet-4-5")
    assert isinstance(result, list)
    assert result[0]["type"] == "email_stat_row"


def test_extract_visuals_caps_blog_at_four():
    many = {"visual_elements": [{"type": "callout", "after_section": i, "body": f"body {i}"} for i in range(10)]}
    client = _mock_client(many)
    result = extract_visuals(_BLOG, _ARTICLES, "blog", client, "claude-sonnet-4-5")
    assert len(result) <= 4


def test_extract_visuals_caps_newsletter_at_two():
    many = {"visual_elements": [{"type": "email_stat_row", "after_section": i, "cards": []} for i in range(5)]}
    client = _mock_client(many)
    result = extract_visuals(_NEWSLETTER, _ARTICLES, "newsletter", client, "claude-sonnet-4-5")
    assert len(result) <= 2


def test_extract_visuals_returns_empty_on_api_error():
    import anthropic
    client = MagicMock()
    client.messages.create.side_effect = anthropic.APIError("boom", request=MagicMock(), body=None)
    result = extract_visuals(_BLOG, _ARTICLES, "blog", client, "claude-sonnet-4-5")
    assert result == []


def test_content_to_text_blog():
    text = _content_to_text(_BLOG, "blog")
    assert "ISA Allowance 2026" in text
    assert "Cash ISA vs S&S ISA" in text
    assert "April deadline" in text


def test_content_to_text_newsletter():
    text = _content_to_text(_NEWSLETTER, "newsletter")
    assert "Rate hold" in text
    assert "NS&I Premium Bond" in text
