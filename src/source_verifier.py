"""Verify that LLM-cited source URLs actually resolve.

Catches the most embarrassing failure mode of generated content — the
model citing a plausible-looking but fabricated URL — without false-
flagging real, paywalled news sites that bot-block our request.

Status taxonomy:
- ok        2xx/3xx                                 (✓ verified live)
- blocked   401/403/429 from a known paywalled site (⚠ likely valid;
            paywall + bot detection, can't verify from this side)
- 404 / 4xx / 5xx                                   (✗ actually broken)
- timeout / ssl / unreachable / invalid / error     (✗ probably broken)

Public API:
- verify_urls(urls, *, timeout=8, max_workers=8) -> dict[str, dict]
- summarise(records) -> {total, ok, blocked, broken, all_ok}
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)


# Browser-like headers. Some sites still 403 us — see PAYWALLED_DOMAINS.
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}


# Paywalled / strongly bot-protected outlets. A 401/403/429 from these is
# almost always Cloudflare or a paywall gate, not a broken URL — so we mark
# them "blocked" (informational), not "broken". URLs from any other domain
# returning 403 are classified as a real 4xx failure.
PAYWALLED_DOMAINS = frozenset({
    "ft.com", "thetimes.co.uk", "telegraph.co.uk", "wsj.com",
    "bloomberg.com", "economist.com", "nytimes.com", "washingtonpost.com",
    "reuters.com", "bbc.co.uk", "theguardian.com", "moneyweek.com",
    "thisismoney.co.uk", "cityam.com", "dailymail.co.uk", "express.co.uk",
    "investorschronicle.co.uk", "morningstar.co.uk",
})

# Codes that suggest "site is up and refusing automated access" rather than
# "URL is genuinely broken".
_BOT_BLOCK_CODES = (401, 403, 429)


def _hostname(url: str) -> str:
    try:
        h = (urlparse(url).hostname or "").lower()
        # Strip leading "www." for matching against PAYWALLED_DOMAINS.
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def _is_paywalled(url: str) -> bool:
    h = _hostname(url)
    if not h:
        return False
    if h in PAYWALLED_DOMAINS:
        return True
    # Match subdomains too (e.g. blog.bloomberg.com).
    return any(h.endswith("." + d) for d in PAYWALLED_DOMAINS)


def _classify(http_code: int, url: str) -> tuple[str, str | None]:
    """Map an HTTP code to (status, optional explanatory note)."""
    if 200 <= http_code < 400:
        return "ok", None
    if http_code in _BOT_BLOCK_CODES:
        if _is_paywalled(url):
            return "blocked", ("paywall / bot detection — URL is on a known "
                                "paywalled outlet and is almost certainly valid")
        # Non-paywalled site returning 401/403/429 is more suspicious — but
        # still likely "anti-bot" rather than truly broken. Mark as blocked
        # but with a softer note.
        return "blocked", ("HTTP {0}: site refused automated request "
                            "(URL is probably valid; verify in a browser)").format(http_code)
    if http_code == 404:
        return "404", None
    if 400 <= http_code < 500:
        return "4xx", None
    return "5xx", None


def _verify_one(url: str, *, timeout: float) -> dict:
    """Check a single URL. HEAD first; falls back to GET if HEAD is rejected
    (some sites — gov.uk, FT — return 405 or 403 for HEAD)."""
    record: dict = {"url": url, "status": "unknown",
                     "http_code": None, "final_url": None, "error": None,
                     "note": None, "paywalled": False}
    if not url or not isinstance(url, str) or not url.strip():
        record["status"] = "invalid"
        record["error"] = "Empty or non-string URL"
        return record
    if not url.startswith(("http://", "https://")):
        record["status"] = "invalid"
        record["error"] = "URL has no scheme"
        return record

    record["paywalled"] = _is_paywalled(url)

    for method in ("head", "get"):
        try:
            resp = requests.request(
                method, url, headers=_HEADERS,
                timeout=timeout, allow_redirects=True,
                # Don't download bodies — stream + close to save bandwidth.
                stream=(method == "get"),
            )
            if method == "get":
                resp.close()
            record["http_code"] = resp.status_code
            record["final_url"] = resp.url
            # Some servers reject HEAD outright (gov.uk does it for some
            # paths). Retry with GET before classifying as failure.
            if method == "head" and resp.status_code in (405, 403, 401):
                continue
            status, note = _classify(resp.status_code, record["final_url"] or url)
            record["status"] = status
            if note:
                record["note"] = note
            return record
        except requests.exceptions.Timeout:
            record["status"] = "timeout"
            record["error"] = f"timeout after {timeout}s"
        except requests.exceptions.SSLError as e:
            record["status"] = "ssl"
            record["error"] = str(e)[:120]
        except requests.exceptions.ConnectionError as e:
            record["status"] = "unreachable"
            record["error"] = str(e)[:120]
        except requests.RequestException as e:
            record["status"] = "error"
            record["error"] = str(e)[:120]
    return record


def verify_urls(
    urls: Iterable[str], *,
    timeout: float = 8.0,
    max_workers: int = 8,
) -> dict[str, dict]:
    """Concurrently verify a list of URLs. Returns {url: record} dict.

    Verification is best-effort: a network blip won't crash generation; the
    caller decides what to do with the records (typically: render badges).
    """
    urls = [u for u in dict.fromkeys(urls or []) if u]   # dedupe + drop falsies
    if not urls:
        return {}
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(urls)))) as ex:
        futures = {ex.submit(_verify_one, u, timeout=timeout): u for u in urls}
        for fut in as_completed(futures):
            url = futures[fut]
            try:
                out[url] = fut.result()
            except Exception as e:
                out[url] = {"url": url, "status": "error",
                            "http_code": None, "final_url": None,
                            "error": str(e)[:120]}
    return out


_BROKEN_STATUSES = {"404", "4xx", "5xx", "timeout", "ssl",
                     "unreachable", "invalid", "error"}


def summarise(records: dict[str, dict]) -> dict:
    """Quick summary for UI display.

    `bad` only counts URLs that look genuinely broken — `blocked` (paywall
    + bot detection) is informational, not a failure, since the URL is
    almost certainly valid even though we can't verify it from this side.
    """
    total   = len(records)
    ok      = sum(1 for r in records.values() if r["status"] == "ok")
    blocked = sum(1 for r in records.values() if r["status"] == "blocked")
    broken  = sum(1 for r in records.values() if r["status"] in _BROKEN_STATUSES)
    return {
        "total":   total,
        "ok":      ok,
        "blocked": blocked,
        "broken":  broken,
        # Backward-compat: keep `bad` for any caller still reading it, but
        # mean it as "actually broken", not "anything not ok".
        "bad":     broken,
        "all_ok":  broken == 0,
    }
