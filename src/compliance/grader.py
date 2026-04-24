"""Compliance grader — implements the grader.md skill methodology.

Two passes:
1. Hard pass: deterministic regex/substring checks for banned phrases, terms, topics.
2. Principle pass: a single Claude call evaluating the content against the rulebook
   principles, returning per-principle pass/fail with cited evidence.

Output mirrors the grading.json structure described in grader.md:
{
  "expectations": [{text, passed, evidence, rule_id, severity, section}],
  "summary": {passed, failed, total, pass_rate, grade},  # 'pass'|'warn'|'fail'
  "claims": [...],
  "eval_feedback": {...}
}
"""
from __future__ import annotations

import json
import logging
import re
from html import unescape
from typing import Optional

import anthropic

from .._json import parse_json_response
from .rulebook import Rulebook, load_rulebook

log = logging.getLogger(__name__)

PASS_THRESHOLD = 0.95   # >= 95% of expectations passed → 'pass'
WARN_THRESHOLD = 0.80   # 80–95% → 'warn', below 80% → 'fail'


# ---------------------------------------------------------------------------
# Pass 1 — deterministic checks
# ---------------------------------------------------------------------------

def _strip_html(s: str) -> str:
    s = re.sub(r"<script.*?</script>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<style.*?</style>",   " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return unescape(re.sub(r"\s+", " ", s)).strip()


def _word_boundary_match(needle: str, haystack: str) -> Optional[str]:
    pattern = r"\b" + re.escape(needle) + r"\b"
    m = re.search(pattern, haystack, flags=re.I)
    if not m:
        return None
    start = max(0, m.start() - 60)
    end   = min(len(haystack), m.end() + 60)
    return f"…{haystack[start:end].strip()}…"


def _disclaimer_present(plain: str, canonical: list[str]) -> tuple[bool, str]:
    """Heuristic: at least one canonical disclaimer fragment is present."""
    plain_low = plain.lower()
    # Match on stable substrings
    fragments = [
        "not financial advice",
        "does not provide financial advice",
        "planning tool and does not provide",
        "scenarios only, not advice",
        "information only, not advice",
    ]
    for f in fragments:
        if f in plain_low:
            return True, f"Found disclaimer fragment: '{f}'"
    return False, "No 'not financial advice' style disclaimer detected."


def _hard_pass(content: str, rb: Rulebook, *, kind: str) -> list[dict]:
    """Run deterministic checks. Returns expectations[]. kind: 'article'|'newsletter'|'blog'."""
    plain = _strip_html(content)
    expectations: list[dict] = []

    for rule in rb.hard_rules:
        if rule.kind == "banned_phrase":
            ev = _word_boundary_match(rule.pattern, plain)
            passed = ev is None
            expectations.append({
                "text":     f"Content does not contain banned phrase '{rule.pattern}'",
                "passed":   passed,
                "evidence": ev or "Phrase not found.",
                "rule_id":  rule.id,
                "section":  rule.section,
                "severity": rule.severity,
                "rationale": rule.rationale,
                "suggested_replacement": rule.suggested_replacement,
            })
        elif rule.kind == "banned_term":
            ev = _word_boundary_match(rule.pattern, plain)
            passed = ev is None
            expectations.append({
                "text":     f"Content does not use banned term '{rule.pattern}'",
                "passed":   passed,
                "evidence": ev or "Term not found.",
                "rule_id":  rule.id,
                "section":  rule.section,
                "severity": rule.severity,
                "rationale": rule.rationale,
                "suggested_replacement": rule.suggested_replacement,
            })
        elif rule.kind == "banned_topic":
            ev = _word_boundary_match(rule.pattern, plain)
            # For scraped articles a passing news article about crypto is informational, not marketing.
            # For generated outputs (newsletter/blog) it is the marketing channel — strict.
            if kind == "article":
                # Downgrade: just flag, don't fail outright.
                expectations.append({
                    "text":     f"(advisory) Article references banned topic '{rule.pattern}'",
                    "passed":   ev is None,
                    "evidence": ev or "Topic not mentioned.",
                    "rule_id":  rule.id,
                    "section":  rule.section,
                    "severity": "low",   # downgrade — news coverage of crypto is informational
                    "rationale": rule.rationale + " (Downgraded for scraped news.)",
                })
            else:
                expectations.append({
                    "text":     f"Output does not reference banned topic '{rule.pattern}'",
                    "passed":   ev is None,
                    "evidence": ev or "Topic not mentioned.",
                    "rule_id":  rule.id,
                    "section":  rule.section,
                    "severity": rule.severity,
                    "rationale": rule.rationale,
                })

    if kind in ("newsletter", "blog"):
        present, ev = _disclaimer_present(plain, rb.canonical_disclaimers)
        expectations.append({
            "text":     "Customer-facing output includes the 'not financial advice' disclaimer",
            "passed":   present,
            "evidence": ev,
            "rule_id":  "HR_disclaimer_present",
            "section":  "2.5.2",
            "severity": "high",
            "rationale": "Required status disclosure per rulebook section 2.5.",
            "suggested_replacement": rb.canonical_disclaimers[0] if rb.canonical_disclaimers else "",
        })

    return expectations


# ---------------------------------------------------------------------------
# Pass 2 — principle-level evaluation via Claude
# ---------------------------------------------------------------------------

PRINCIPLE_SYSTEM = (
    "You are a UK financial-marketing compliance grader applying the Meet Warren "
    "Marketing Compliance Guidebook. You evaluate content strictly and return ONLY valid JSON."
)

PRINCIPLE_TEMPLATE = """Evaluate the CONTENT below against each PRINCIPLE.

For each principle return:
- "passed": true if content is compliant, false if it violates the principle
- "evidence": short quote (<=20 words) from the content showing the issue, OR "No violation found."
- "claims": flag specific factual / quality claims in the content that you cannot verify

Return ONLY a JSON object EXACTLY matching this schema:
{{
  "principles": [
    {{"id": "<principle id>", "passed": <bool>, "evidence": "<string>"}}
  ],
  "claims": [
    {{"claim": "<extracted statement>", "type": "factual|process|quality", "verified": <bool>, "evidence": "<string>"}}
  ],
  "eval_feedback": {{
    "suggestions": [{{"reason": "<short observation>"}}],
    "overall": "<one-sentence assessment>"
  }}
}}

PRINCIPLES:
{principles_json}

CONTENT (kind={kind}):
{content_excerpt}
"""


def _principle_pass(
    content_plain: str,
    rb: Rulebook,
    client: anthropic.Anthropic,
    model: str,
    kind: str,
) -> tuple[list[dict], list[dict], dict]:
    """Returns (principle_expectations, claims, eval_feedback)."""
    if not content_plain.strip():
        return [], [], {"suggestions": [], "overall": "No content to evaluate."}

    excerpt = content_plain[:6000]
    prompt = PRINCIPLE_TEMPLATE.format(
        principles_json=json.dumps(
            [{"id": p.id, "title": p.title, "description": p.description, "section": p.section}
             for p in rb.principles], indent=2),
        kind=kind,
        content_excerpt=excerpt,
    )
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=2500,
            system=PRINCIPLE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
        data = parse_json_response(text)
    except (anthropic.APIError, json.JSONDecodeError, IndexError, AttributeError) as exc:
        log.error("Principle grading failed: %s", exc)
        return [], [], {"suggestions": [], "overall": "Grader call failed; skipped principle pass."}

    by_id = {p.id: p for p in rb.principles}
    expectations = []
    for r in data.get("principles", []):
        pid    = r.get("id", "")
        passed = bool(r.get("passed", False))
        ev     = r.get("evidence", "") or ""
        meta   = by_id.get(pid)
        expectations.append({
            "text":     meta.title if meta else pid,
            "passed":   passed,
            "evidence": ev,
            "rule_id":  pid,
            "section":  meta.section if meta else "",
            "severity": meta.severity if meta else "high",
            "rationale": meta.description if meta else "",
        })
    claims = data.get("claims", []) or []
    feedback = data.get("eval_feedback", {}) or {"suggestions": [], "overall": ""}
    return expectations, claims, feedback


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def grade_content(
    content: str,
    *,
    kind: str,                                # 'article' | 'newsletter' | 'blog'
    client: Optional[anthropic.Anthropic] = None,
    model: str = "claude-sonnet-4-5",
    rulebook: Optional[Rulebook] = None,
    skip_principles: bool = False,
) -> dict:
    """Grade content against the compliance rulebook. Returns a grading.json dict."""
    rb = rulebook or load_rulebook()
    plain = _strip_html(content)

    expectations: list[dict] = _hard_pass(content, rb, kind=kind)
    claims: list[dict] = []
    eval_feedback: dict = {"suggestions": [], "overall": ""}

    if not skip_principles and client is not None:
        principle_exps, claims, eval_feedback = _principle_pass(plain, rb, client, model, kind)
        expectations.extend(principle_exps)

    total  = len(expectations)
    passed = sum(1 for e in expectations if e["passed"])
    failed = total - passed
    rate   = (passed / total) if total else 1.0
    grade  = "pass" if rate >= PASS_THRESHOLD else ("warn" if rate >= WARN_THRESHOLD else "fail")

    return {
        "expectations": expectations,
        "summary": {
            "passed":    passed,
            "failed":    failed,
            "total":     total,
            "pass_rate": round(rate, 3),
            "grade":     grade,
        },
        "claims":         claims,
        "eval_feedback":  eval_feedback,
        "rulebook_meta":  {
            "source": rb.source_path,
            "extracted_at": rb.extracted_at,
            "principle_count": len(rb.principles),
            "hard_rule_count": len(rb.hard_rules),
        },
    }
