# tests/test_seo_agent.py
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock
from src.seo_agent import generate_seo_brief

_ARTICLES = [
    {
        "title": "Bank of England holds rates at 5.25%",
        "url": "https://bbc.co.uk/news/business/boe",
        "source": "BBC News",
        "summary": "The MPC voted to hold the base rate at 5.25% in March 2026.",
        "key_points": ["Rate held at 5.25%", "MPC split 6-3", "Next meeting May"],
        "excerpt": "The Bank of England kept rates on hold...",
    }
]

_VALID_BRIEF = {
    "primary_keyword": "Bank of England base rate 2026",
    "semantic_keywords": ["mortgage rates UK", "MPC decision"],
    "target_h1": "Bank of England Holds Rate at 5.25%: What It Means for UK Mortgages",
    "faq_seeds": ["Will the Bank of England cut rates in 2026?"],
    "aeo_signals": {
        "answer_first_targets": ["What did the MPC decide?"],
        "speakable_candidates": ["The Bank of England held rates at 5.25% in March 2026."],
        "citation_stats": ["Base rate 5.25% (BoE, Mar 2026)"],
    },
    "schema_flags": ["FAQPage", "Speakable"],
    "meta_description_brief": "The Bank of England held rates at 5.25% in March 2026. Here is what it means for mortgage holders and savers.",
}


def _mock_client(response_text: str):
    msg = MagicMock()
    msg.content = [MagicMock(text=response_text)]
    client = MagicMock()
    client.messages.create.return_value = msg
    return client


def test_generate_seo_brief_returns_dict():
    client = _mock_client(json.dumps(_VALID_BRIEF))
    result = generate_seo_brief(_ARTICLES, client, "claude-sonnet-4-5")
    assert isinstance(result, dict)
    assert result["primary_keyword"] == "Bank of England base rate 2026"
    assert "semantic_keywords" in result
    assert "aeo_signals" in result
    assert "schema_flags" in result


def test_generate_seo_brief_injects_editor_angle():
    client = _mock_client(json.dumps(_VALID_BRIEF))
    generate_seo_brief(_ARTICLES, client, "claude-sonnet-4-5", editor_angle="Focus on first-time buyers")
    call_kwargs = client.messages.create.call_args
    prompt = call_kwargs[1]["messages"][0]["content"]
    assert "Focus on first-time buyers" in prompt


def test_generate_seo_brief_returns_none_on_api_error():
    import anthropic
    client = MagicMock()
    client.messages.create.side_effect = anthropic.APIError("boom", request=MagicMock(), body=None)
    result = generate_seo_brief(_ARTICLES, client, "claude-sonnet-4-5")
    assert result is None


def test_generate_seo_brief_returns_none_on_bad_json():
    client = _mock_client("not valid json {{{")
    result = generate_seo_brief(_ARTICLES, client, "claude-sonnet-4-5")
    assert result is None


def test_generate_seo_brief_returns_none_for_empty_articles():
    client = _mock_client(json.dumps(_VALID_BRIEF))
    result = generate_seo_brief([], client, "claude-sonnet-4-5")
    assert result is None
