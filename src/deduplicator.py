from __future__ import annotations

from typing import Iterable, List, Set

from .scraper import Article


def filter_new(articles: Iterable[Article], existing_urls: Set[str]) -> List[Article]:
    seen: Set[str] = set(existing_urls)
    new: List[Article] = []
    for art in articles:
        if art.url in seen:
            continue
        seen.add(art.url)
        new.append(art)
    return new
