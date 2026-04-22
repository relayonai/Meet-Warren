from __future__ import annotations

import json
import logging
from typing import Optional

import anthropic

from ._json import parse_json_response
from .scraper import Article

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a UK personal-finance editor. You return ONLY valid JSON. "
    "No markdown fences, no commentary, no prose outside the JSON object."
)

USER_TEMPLATE = """Summarise the following UK personal finance article.

Return a JSON object that exactly matches this schema:
{{
  "title": "string (rewritten, concise, accurate)",
  "summary": "string (2-3 sentence neutral summary)",
  "key_points": ["string", "string", "..."],
  "category": "one of: savings, mortgages, pensions, investing, tax, banking, benefits, scams, markets, other",
  "relevance_score": integer 1-10 (10 = highly relevant to UK personal finance readers)
}}

Article title: {title}
Source: {source}
URL: {url}

Article content:
{content}
"""


def summarise(article: Article, client: anthropic.Anthropic, model: str) -> Optional[dict]:
    prompt = USER_TEMPLATE.format(
        title=article.title,
        source=article.source,
        url=article.url,
        content=(article.raw_content or "")[:6000],
    )
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
        data = parse_json_response(text)
    except (anthropic.APIError, json.JSONDecodeError, IndexError, AttributeError) as exc:
        log.warning("Summarisation failed for %s: %s", article.url, exc)
        return None

    # Light validation: ensure required keys present
    for key in ("title", "summary", "key_points", "category", "relevance_score"):
        if key not in data:
            log.warning("Summary missing key '%s' for %s", key, article.url)
            return None
    return data
