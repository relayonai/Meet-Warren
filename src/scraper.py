from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, List, Optional
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (compatible; UKFinanceNewsBot/1.0; +https://example.com/bot)"
)
HTTP_TIMEOUT = 15

GOVUK_SEARCH_API = "https://www.gov.uk/api/search.json"

# Feed URLs whose self-reported titles are too generic — override with a proper name.
RSS_SOURCE_OVERRIDES: dict[str, str] = {
    "https://www.bankofengland.co.uk/rss/news": "Bank of England",
    "https://www.fca.org.uk/rss.xml": "Financial Conduct Authority (FCA)",
    "http://feeds.bbci.co.uk/news/business/your_money/rss.xml": "BBC Your Money",
}

# Title-level keyword pre-filter — any match passes through to summarisation.
# Generous on purpose: the relevance_score gate handles the final cut.
UK_FINANCE_KEYWORDS = {
    "mortgage", "pension", "savings", "isa", "lisa", "invest", "tax", "inflation",
    "interest rate", "energy bill", "energy price", "salary", "wage", "debt",
    "insurance", "credit", "budget", "premium bond", "national insurance", "ni ",
    "stamp duty", "capital gains", "income tax", "vat", "dividend", "retirement",
    "annuity", "benefit", "universal credit", "cost of living", "housing", "rent",
    "property", "loan", "overdraft", "bank", "finance", "financial", "money",
    "wealth", "fund", "shares", "stock", "gilts", "cash", "tariff", "employment",
    "redundancy", "payroll", "hmrc", "fca", "bank of england", "boe", "ons",
    "gdp", "cpi", "rpi", "base rate", "interest", "economy", "economic",
    "fraud", "scam", "price cap", "ofgem", "gas", "electricity", "fuel",
    "car benefit", "venture capital", "regulation", "consumer", "household",
}


@dataclass
class Article:
    id: str
    title: str
    url: str
    source: str
    published_at: Optional[str]
    raw_content: str


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _iso(dt_struct) -> Optional[str]:
    if not dt_struct:
        return None
    try:
        return datetime(*dt_struct[:6], tzinfo=timezone.utc).isoformat()
    except Exception:
        return None


def title_passes_prefilter(title: str) -> bool:
    """Return True if the title contains at least one UK-finance keyword."""
    lower = title.lower()
    return any(kw in lower for kw in UK_FINANCE_KEYWORDS)


def fetch_rss(feed_urls: Iterable[str]) -> List[Article]:
    articles: List[Article] = []
    for url in feed_urls:
        try:
            parsed = feedparser.parse(url)
            source = RSS_SOURCE_OVERRIDES.get(url) or parsed.feed.get("title") or urlparse(url).netloc
            for entry in parsed.entries:
                link = entry.get("link")
                title = (entry.get("title") or "").strip()
                if not link or not title:
                    continue
                content = (
                    entry.get("summary")
                    or entry.get("description")
                    or (entry.get("content", [{}])[0].get("value") if entry.get("content") else "")
                    or ""
                )
                content_text = BeautifulSoup(content, "html.parser").get_text(" ", strip=True)
                articles.append(
                    Article(
                        id=_hash_url(link),
                        title=title,
                        url=link,
                        source=source,
                        published_at=_iso(entry.get("published_parsed") or entry.get("updated_parsed")),
                        raw_content=content_text[:8000],
                    )
                )
        except Exception as exc:
            log.warning("Failed to parse RSS feed %s: %s", url, exc)
    return articles


def fetch_govuk_api(org_slugs: Iterable[str], count: int = 20) -> List[Article]:
    """Fetch recent publications from the GOV.UK search API for given org slugs.
    Covers ONS (office-for-national-statistics) and HMRC (hm-revenue-customs).
    """
    articles: List[Article] = []
    for slug in org_slugs:
        try:
            params = {
                "filter_organisations[]": slug,
                "count": count,
                "fields[]": ["title", "link", "description", "public_timestamp", "organisations"],
                "order": "-public_timestamp",
            }
            r = requests.get(
                GOVUK_SEARCH_API,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=HTTP_TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
            source_name = slug.replace("-", " ").title()
            for item in data.get("results", []):
                title = (item.get("title") or "").strip()
                path = item.get("link", "")
                if not title or not path:
                    continue
                url = f"https://www.gov.uk{path}" if path.startswith("/") else path
                description = (item.get("description") or "").strip()
                published = item.get("public_timestamp")
                articles.append(
                    Article(
                        id=_hash_url(url),
                        title=title,
                        url=url,
                        source=source_name,
                        published_at=published,
                        raw_content=description[:8000],
                    )
                )
        except Exception as exc:
            log.warning("GOV.UK API fetch failed for org %s: %s", slug, exc)
    return articles


def _get(url: str) -> Optional[str]:
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return r.text
    except requests.RequestException as exc:
        log.warning("HTTP fetch failed for %s: %s", url, exc)
        return None


def _extract_article(html: str, url: str, source: str) -> Optional[Article]:
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    if not title:
        return None

    body_root = soup.find("article") or soup.find("main") or soup.body
    if body_root is None:
        return None
    paragraphs = [p.get_text(" ", strip=True) for p in body_root.find_all("p")]
    text = " ".join(p for p in paragraphs if p)
    if len(text) < 200:
        return None

    published = None
    time_tag = soup.find("time")
    if time_tag and time_tag.get("datetime"):
        published = time_tag["datetime"]

    return Article(
        id=_hash_url(url),
        title=title,
        url=url,
        source=source,
        published_at=published,
        raw_content=text[:8000],
    )


def fetch_ukfinance_news(max_articles: int = 15) -> List[Article]:
    """Scrape UK Finance news listing page for recent articles."""
    listing_url = "https://www.ukfinance.org.uk/news-and-insight/news"
    source = "UK Finance"
    html = _get(listing_url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    article_links: List[str] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = urljoin(listing_url, href)
        parsed = urlparse(full)
        if parsed.netloc not in ("www.ukfinance.org.uk", "ukfinance.org.uk"):
            continue
        if "/news-and-insight/" not in parsed.path and "/news/" not in parsed.path:
            continue
        if full in seen or full.rstrip("/") == listing_url.rstrip("/"):
            continue
        seen.add(full)
        article_links.append(full)
        if len(article_links) >= max_articles:
            break

    articles: List[Article] = []
    for link in article_links:
        article_html = _get(link)
        if not article_html:
            continue
        article = _extract_article(article_html, link, source)
        if article:
            articles.append(article)
    return articles


def fetch_http(source_urls: Iterable[str], max_articles_per_source: int = 10) -> List[Article]:
    """Generic listing-page scraper for any HTTP source."""
    articles: List[Article] = []
    for listing_url in source_urls:
        html = _get(listing_url)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        source = urlparse(listing_url).netloc
        seen_links: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            full = urljoin(listing_url, href)
            parsed = urlparse(full)
            if parsed.scheme not in ("http", "https"):
                continue
            if parsed.netloc != urlparse(listing_url).netloc:
                continue
            if full in seen_links or full.rstrip("/") == listing_url.rstrip("/"):
                continue
            if len(parsed.path) < 15:
                continue
            seen_links.add(full)
            if len(seen_links) > max_articles_per_source:
                break

        for link in list(seen_links)[:max_articles_per_source]:
            article_html = _get(link)
            if not article_html:
                continue
            article = _extract_article(article_html, link, source)
            if article:
                articles.append(article)
    return articles
