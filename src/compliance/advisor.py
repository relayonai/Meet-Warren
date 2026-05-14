"""Document compliance advisor — line-by-line audit against the rulebook.

Unlike the grader (which scores a finished piece pass/warn/fail), the advisor
treats every element of an uploaded document as a separate subject and returns
a plain-English finding + solution for each violation it finds.

Consistency fixes applied:
  1. temperature=0 on the LLM call — near-deterministic output.
  2. Explicit named-principle checklist in the prompt — model evaluates against
     a fixed list only, never invents new rules.
  3. Expanded hard-rule pass — advice-perimeter language, performance promises,
     and risk/safety claims are caught deterministically before the LLM sees them.

Public API:
    parse_elements(text) -> list[dict]
    advise_document(elements, rulebook, client, model) -> dict
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

import anthropic

from .._json import parse_json_response
from .rulebook import Rulebook, load_rulebook

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Element parser
# ---------------------------------------------------------------------------

def parse_elements(text: str) -> list[dict]:
    """Split plain text into typed, indexed elements.

    Each element: {"index": int, "type": "heading"|"paragraph"|"list_item"|"line", "text": str}
    """
    elements: list[dict] = []
    idx = 0

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    blocks: list[str] = []
    current: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            current.append(stripped)
        else:
            if current:
                blocks.append("\n".join(current))
                current = []
    if current:
        blocks.append("\n".join(current))

    for block in blocks:
        for raw in block.split("\n"):
            t = raw.strip()
            if not t:
                continue
            if re.match(r"^#{1,6}\s", t):
                kind = "heading"
                t = re.sub(r"^#{1,6}\s+", "", t)
            elif re.match(r"^[-*•]\s", t):
                kind = "list_item"
                t = re.sub(r"^[-*•]\s+", "", t)
            elif re.match(r"^\d+[.)]\s", t):
                kind = "list_item"
                t = re.sub(r"^\d+[.)]\s+", "", t)
            elif len(t) < 80 and not t.endswith("."):
                kind = "heading"
            else:
                kind = "paragraph"
            elements.append({"index": idx, "type": kind, "text": t})
            idx += 1

    return elements


# ---------------------------------------------------------------------------
# Extended deterministic hard rules (Fix 3)
# These supplement the rulebook's own hard rules with patterns that are
# objective enough to check without an LLM but commonly caused run-to-run
# variance when left to the principle pass.
# ---------------------------------------------------------------------------

# Each entry: (pattern, kind, section, severity, rationale, suggested_replacement)
_EXTENDED_HARD_RULES: list[tuple] = [

    # --- Advice-perimeter language (§1.3 / §2.2) ---
    ("you should invest",     "advice_perimeter", "1.3", "high",
     "Crosses the regulated-advice perimeter — personal investment recommendation.",
     "Use 'you could consider' or 'some people choose to' instead."),
    ("you should buy",        "advice_perimeter", "1.3", "high",
     "Crosses the regulated-advice perimeter.",
     "Remove or reframe as a general information statement."),
    ("you should sell",       "advice_perimeter", "1.3", "high",
     "Crosses the regulated-advice perimeter.",
     "Remove or reframe as a general information statement."),
    ("we recommend you invest","advice_perimeter","1.3","high",
     "Personal investment recommendation — regulated activity.",
     "Replace with 'you may wish to explore' or similar non-directive phrasing."),
    ("our advice is",         "advice_perimeter", "1.3", "high",
     "Warren does not give financial advice. 'Our advice is' implies regulated advice.",
     "Replace with 'our view is' or 'one consideration is'."),
    ("my advice is",          "advice_perimeter", "1.3", "high",
     "Personal advice framing — regulated activity.",
     "Replace with 'one thing worth considering is'."),
    ("we advise you",         "advice_perimeter", "1.3", "high",
     "Regulated-advice language.",
     "Replace with 'we suggest exploring' or 'it may be worth looking at'."),
    ("I advise you",          "advice_perimeter", "1.3", "high",
     "Regulated-advice language.",
     "Replace with 'you might consider'."),

    # --- Performance / return promises (§1.2 / §2.3) ---
    ("risk-free",             "performance_promise", "1.2", "high",
     "No investment is risk-free. Absolute safety claim breaches §1.2.",
     "Replace with 'lower-risk' and add appropriate caveats."),
    ("zero risk",             "performance_promise", "1.2", "high",
     "Absolute safety claim — all investments carry some risk.",
     "Remove or replace with a balanced risk statement."),
    ("no risk",               "performance_promise", "1.2", "high",
     "Absolute safety claim breaches §1.2.",
     "Replace with 'reduced risk' and add downside caveats."),
    ("safe investment",       "performance_promise", "1.2", "high",
     "'Safe' without qualification implies capital protection — misleading.",
     "Replace with 'lower-volatility option' and add risk disclaimer."),
    ("will earn",             "performance_promise", "1.2", "high",
     "Certain future return claim breaches §1.2 and FCA guidance.",
     "Replace with 'could earn' and add 'capital at risk' caveat."),
    ("will grow",             "performance_promise", "2.3", "high",
     "Guaranteed growth claim. Projections must be balanced with downside scenarios.",
     "Replace with 'has the potential to grow' and add 'past performance' caveat."),
    ("will double",           "performance_promise", "2.3", "high",
     "Absolute projection — no investment doubles with certainty.",
     "Remove or reframe as a scenario with clear downside disclosure."),
    ("will outperform",       "performance_promise", "1.2", "high",
     "Absolute comparative claim — cannot be guaranteed.",
     "Replace with 'has historically outperformed' with appropriate caveats."),
    ("guaranteed return",     "performance_promise", "1.2", "high",
     "Guaranteed returns do not exist in investment products.",
     "Remove 'guaranteed' — use 'projected' with downside scenario."),
    ("guaranteed growth",     "performance_promise", "1.2", "high",
     "No investment guarantees growth.",
     "Replace with 'potential for growth, subject to market conditions'."),
    ("promise you",           "performance_promise", "1.2", "high",
     "Promise of outcome breaches §1.2 fair-and-not-misleading principle.",
     "Replace with 'our aim is' or 'we work to'."),

    # --- American English spellings (§ tone / UK-English requirement) ---
    ("color",      "uk_english", "tone", "medium",
     "American spelling. Warren uses UK English.",
     "Replace with 'colour'."),
    ("analyze",    "uk_english", "tone", "medium",
     "American spelling.",
     "Replace with 'analyse'."),
    ("organize",   "uk_english", "tone", "medium",
     "American spelling.",
     "Replace with 'organise'."),
    ("optimize",   "uk_english", "tone", "medium",
     "American spelling.",
     "Replace with 'optimise'."),
    ("customize",  "uk_english", "tone", "medium",
     "American spelling.",
     "Replace with 'customise'."),
    ("realize",    "uk_english", "tone", "medium",
     "American spelling.",
     "Replace with 'realise'."),
    ("recognize",  "uk_english", "tone", "medium",
     "American spelling.",
     "Replace with 'recognise'."),
    ("center",     "uk_english", "tone", "medium",
     "American spelling.",
     "Replace with 'centre'."),
    ("behavior",   "uk_english", "tone", "medium",
     "American spelling.",
     "Replace with 'behaviour'."),
    ("license",    "uk_english", "tone", "medium",
     "American spelling (noun). UK English uses 'licence' for the noun.",
     "Replace with 'licence' (noun) or keep 'license' only as a verb."),
    ("defense",    "uk_english", "tone", "medium",
     "American spelling.",
     "Replace with 'defence'."),
    ("offense",    "uk_english", "tone", "medium",
     "American spelling.",
     "Replace with 'offence'."),
    ("program",    "uk_english", "tone", "medium",
     "American spelling in non-technical context.",
     "Replace with 'programme' unless referring to a software program."),
]


def _hard_findings(elements: list[dict], rb: Rulebook) -> list[dict]:
    """Run all deterministic hard rules over every element."""
    findings: list[dict] = []

    # Rulebook-defined hard rules
    for el in elements:
        for rule in rb.hard_rules:
            pattern = r"\b" + re.escape(rule.pattern) + r"\b"
            m = re.search(pattern, el["text"], re.I)
            if not m:
                continue
            start = max(0, m.start() - 40)
            end   = min(len(el["text"]), m.end() + 40)
            snippet = f"…{el['text'][start:end].strip()}…"
            findings.append({
                "index":    el["index"],
                "severity": "critical" if rule.severity == "high" else "warning",
                "section":  rule.section,
                "rule":     rule.kind.replace("_", " ").title(),
                "finding":  f"Contains {rule.kind.replace('_',' ')} '{rule.pattern}': {snippet}",
                "solution": rule.suggested_replacement or
                            f"Remove or replace '{rule.pattern}'. §{rule.section}: {rule.rationale}",
                "_source":  "hard",
            })

    # Extended hard rules
    for el in elements:
        for (phrase, kind, section, severity, rationale, fix) in _EXTENDED_HARD_RULES:
            pattern = r"\b" + re.escape(phrase) + r"\b"
            m = re.search(pattern, el["text"], re.I)
            if not m:
                continue
            # Avoid flagging "program" inside "programming" / "programmer"
            if phrase == "program":
                after = el["text"][m.end():m.end()+4].lower()
                if after.startswith("m"):   # "programmer", "programming"
                    continue
            start = max(0, m.start() - 40)
            end   = min(len(el["text"]), m.end() + 40)
            snippet = f"…{el['text'][start:end].strip()}…"
            findings.append({
                "index":    el["index"],
                "severity": "critical" if severity == "high" else "warning",
                "section":  section,
                "rule":     kind.replace("_", " ").title(),
                "finding":  f"'{phrase}' — {rationale[:120]} {snippet}",
                "solution": fix,
                "_source":  "hard",
            })

    return findings


# ---------------------------------------------------------------------------
# LLM principle pass — Fix 2: explicit named-principle checklist
# ---------------------------------------------------------------------------

_SYSTEM = """\
You are a UK financial-marketing compliance advisor for Meet Warren. \
You review document elements against a fixed rulebook checklist and \
return ONLY valid JSON — no prose, no preamble, no extra commentary.

CRITICAL INSTRUCTION: You may ONLY flag elements that clearly violate one \
of the NAMED PRINCIPLES listed in the prompt. Do NOT invent new rules, \
do NOT apply general editorial judgement, and do NOT flag style issues \
unless they appear on the checklist. If an element does not clearly violate \
a named principle, leave it out of your findings entirely.
"""

_TEMPLATE = """\
Review each ELEMENT against the CHECKLIST below. Flag only clear violations.

SEVERITY RULES — follow these exactly, every run:
- "critical"   : element clearly and directly violates a named principle \
(e.g. looks like regulated advice, makes an unsubstantiated absolute claim, \
references crypto, contains a performance guarantee)
- "warning"    : element is likely to breach a principle but requires context \
(e.g. a claim that might be substantiated elsewhere, a projection without a \
downside scenario, a borderline 'free' claim)
- "suggestion" : element is technically compliant but could be improved to \
better align with the spirit of the rules (use sparingly)

CHECKLIST — evaluate ONLY against these named principles:

P1  §1.1-1.2   Fair, clear, not misleading
    Flag: absolute superlatives without evidence, cherry-picked stats, \
    claims that could create a false impression of Warren's capabilities.

P2  §1.3, 2.2  Outside the regulated-advice perimeter
    Flag: any element that reads as a personal recommendation to buy, sell, \
    or hold a specific investment, or that tells a specific person what to do \
    with their money. General information and tools descriptions are fine.

P3  §1.4, 2.1.3  All claims substantiated and non-absolute
    Flag: comparative claims ("better than X", "the only app that…"), \
    unverifiable quality claims, or absolute statements of fact that are \
    not evidenced in the document.

P4  §2.3  Projections balanced with downside scenarios
    Flag: any forecast, growth projection, or graph description that \
    does not include a "past performance is not a guide" or equivalent caveat.

P5  §2.4  No cryptocurrency references
    Flag: any mention of crypto assets, Bitcoin, Ethereum, NFTs, or \
    similar digital assets in a promotional context.

P6  §2.5  Status disclosure (not financial advice) present and prominent
    Flag: customer-facing sections that lack any 'not financial advice' \
    style disclaimer, or where the disclaimer is buried or unclear.

P7  §2.6  'Free' claims must be genuine
    Flag: elements describing a feature as 'free' where the actual output \
    (personalised plan, advice letter, etc.) is not genuinely provided \
    without cost or registration friction.

P8  §2.7, 1.5  Privacy and security claims accurate
    Flag: claims about data security, encryption, or privacy practices \
    that go beyond what is typically verifiable or that make absolute \
    guarantees ("your data is 100% secure", "we never share data").

Return ONLY a JSON object:
{{
  "findings": [
    {{
      "index":    <int — element index>,
      "severity": "critical" | "warning" | "suggestion",
      "section":  "<e.g. 1.3>",
      "rule":     "<principle name from checklist, e.g. 'Outside advice perimeter'>",
      "finding":  "<what is wrong — quote ≤15 words from the element>",
      "solution": "<concrete fix ≤40 words>"
    }}
  ],
  "clean_count": <int>,
  "summary": "<one sentence>"
}}

ELEMENTS:
{elements_json}
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def advise_document(
    elements: list[dict],
    *,
    rulebook: Optional[Rulebook] = None,
    client: anthropic.Anthropic,
    model: str = "claude-sonnet-4-5",
    max_tokens: int = 4000,
) -> dict:
    """Run a full compliance advisory pass over document elements.

    Returns:
        {"findings", "clean_count", "total_count", "summary", "elapsed_seconds"}
    """
    started = time.time()
    rb = rulebook or load_rulebook()

    if not elements:
        return {"findings": [], "clean_count": 0, "total_count": 0,
                "summary": "No content to review.", "elapsed_seconds": 0.0}

    # Pass 1 — deterministic (free, instant, never varies)
    hard_hits = _hard_findings(elements, rb)
    hard_flagged: set[tuple] = {(f["index"], f["rule"]) for f in hard_hits}

    # Pass 2 — LLM principle checklist (Fix 1: temperature=0, Fix 2: bounded prompt)
    elements_for_llm = elements[:120]
    elements_json = json.dumps(
        [{"index": e["index"], "type": e["type"], "text": e["text"]}
         for e in elements_for_llm],
        ensure_ascii=False, indent=2,
    )

    prompt = _TEMPLATE.format(elements_json=elements_json)

    llm_findings: list[dict] = []
    llm_summary = ""
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0,          # Fix 1 — deterministic sampling
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw  = resp.content[0].text if resp.content else "{}"
        data = parse_json_response(raw)
        llm_findings = data.get("findings") or []
        llm_summary  = data.get("summary") or ""
    except (anthropic.APIError, json.JSONDecodeError, IndexError, AttributeError) as exc:
        log.error("advise_document LLM call failed: %s", exc)
        llm_summary = f"LLM advisor call failed ({exc}); hard-rule pass only."

    # Merge — hard findings always win; LLM adds principle violations only
    merged: list[dict] = list(hard_hits)
    for f in llm_findings:
        key = (f.get("index"), f.get("rule", ""))
        if key in hard_flagged:
            continue
        f.pop("_source", None)
        merged.append(f)

    merged.sort(key=lambda x: x.get("index", 0))
    for f in merged:
        f.pop("_source", None)

    total           = len(elements)
    flagged_indices = {f["index"] for f in merged}
    clean           = total - len(flagged_indices)
    summary         = llm_summary or (
        f"{len(merged)} issue(s) found across {len(flagged_indices)} element(s)."
        if merged else "All elements are compliant."
    )

    return {
        "findings":        merged,
        "clean_count":     clean,
        "total_count":     total,
        "summary":         summary,
        "elapsed_seconds": round(time.time() - started, 2),
    }
