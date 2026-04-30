"""Verify that LLM-cited source URLs actually resolve.

Catches the most embarrassing failure mode of generated content:
the model citing a plausible-looking but fabricated URL that 404s.

Public API:
- verify_urls(urls, *, timeout=5, max_workers=8) -> dict[str, dict]
  Returns one record per URL: {status, http_code, final_url, error}.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable

import requests

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


def _verify_one(url: str, *, timeout: float) -> dict:
    """Check a single URL. HEAD first; falls back to GET if HEAD is rejected
    (some sites — gov.uk, FT — return 405 for HEAD)."""
    record: dict = {"url": url, "status": "unknown",
                     "http_code": None, "final_url": None, "error": None}
    if not url or not isinstance(url, str) or not url.strip():
        record["status"] = "invalid"
        record["error"] = "Empty or non-string URL"
        return record
    if not url.startswith(("http://", "https://")):
        record["status"] = "invalid"
        record["error"] = "URL has no scheme"
        return record

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
            # Treat 405 (method-not-allowed) on HEAD as "try GET".
            if method == "head" and resp.status_code in (405, 403):
                continue
            if 200 <= resp.status_code < 400:
                record["status"] = "ok"
            elif resp.status_code == 404:
                record["status"] = "404"
            elif 400 <= resp.status_code < 500:
                record["status"] = "4xx"
            else:
                record["status"] = "5xx"
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
    timeout: float = 5.0,
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


def summarise(records: dict[str, dict]) -> dict:
    """Quick summary for UI display."""
    total = len(records)
    ok    = sum(1 for r in records.values() if r["status"] == "ok")
    bad   = total - ok
    return {
        "total": total, "ok": ok, "bad": bad,
        "all_ok": bad == 0,
    }
