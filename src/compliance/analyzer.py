"""Compliance analyzer — implements the analyzer.md skill methodology.

Given grader output + the original content, identify WHY rules failed and
generate prioritised, actionable improvement_suggestions[].

Output mirrors the analyzer.md JSON structure adapted for the compliance
context (no winner/loser pair — the 'loser' is the content under review,
the 'winner' is the rulebook).
"""
from __future__ import annotations

from typing import List


def _priority_for(severity: str) -> str:
    return {"high": "high", "medium": "medium", "low": "low"}.get(severity, "medium")


def _category_for(rule_id: str) -> str:
    if rule_id.startswith("HR_phrase_"):     return "language"
    if rule_id.startswith("HR_term_"):       return "language"
    if rule_id.startswith("HR_topic_"):      return "topic"
    if rule_id == "HR_disclaimer_present":   return "disclaimer"
    if rule_id.startswith("P"):              return "principle"
    return "other"


def analyze_findings(grading: dict, content_excerpt: str = "") -> dict:
    """Convert grader output to an analyzer.md-style structured improvement plan."""
    failures: List[dict] = [e for e in grading.get("expectations", []) if not e.get("passed")]
    suggestions: List[dict] = []

    for f in failures:
        rule_id   = f.get("rule_id", "")
        section   = f.get("section", "")
        severity  = f.get("severity", "medium")
        ev        = (f.get("evidence") or "").strip()
        rationale = (f.get("rationale") or "").strip()
        replace   = (f.get("suggested_replacement") or "").strip()
        category  = _category_for(rule_id)

        if category == "disclaimer":
            sugg = (
                "Insert the standard 'not financial advice' disclaimer near the foot of the "
                "content. Recommended text: \"" + (replace or "Warren is not financial advice. "
                "It helps you explore scenarios to support your decisions.") + "\""
            )
            impact = "Closes a high-severity gap (rulebook §2.5.2 mandates this disclosure)."
        elif category in ("language",):
            tgt = f.get("text", "").replace("Content does not contain banned phrase ", "") \
                                   .replace("Content does not use banned term ", "").strip("'")
            if replace:
                sugg = f"Replace the banned wording {tgt!r} with: '{replace}'."
            else:
                sugg = f"Remove or rephrase the banned wording {tgt!r}."
            impact = f"Removes a hard-rule violation (rulebook §{section})."
        elif category == "topic":
            sugg = (
                "Remove the cryptocurrency reference from this output (rulebook §2.4 prohibits "
                "crypto in advertising materials)."
            )
            impact = "Removes a hard-rule violation."
        else:  # principle
            sugg = f"Revise wording to satisfy '{f.get('text','')}'. Rulebook §{section}: {rationale}"
            if ev and ev not in ("No violation found.",):
                sugg += f" Specific text to address: {ev}"
            impact = "Closes a principle-level violation."

        suggestions.append({
            "priority":         _priority_for(severity),
            "category":         category,
            "rule_id":          rule_id,
            "section":          section,
            "suggestion":       sugg,
            "expected_impact":  impact,
            "evidence":         ev,
        })

    # Order: high → medium → low, then by category
    pri_order = {"high": 0, "medium": 1, "low": 2}
    suggestions.sort(key=lambda s: (pri_order.get(s["priority"], 9), s["category"]))

    summary = grading.get("summary", {})
    return {
        "comparison_summary": {
            "winner":             "Rulebook (Marketing Compliance Guidebook)",
            "loser":              "Content under review",
            "comparator_reasoning": (
                f"{summary.get('failed', 0)}/{summary.get('total', 0)} expectations failed "
                f"(grade={summary.get('grade','?')}, pass_rate={summary.get('pass_rate','?')})."
            ),
        },
        "winner_strengths": [
            "Rulebook provides explicit banned-phrase lists, canonical disclaimers, and "
            "principle-level standards that make compliance verifiable."
        ],
        "loser_weaknesses": [
            f"{f.get('text','')} — evidence: {f.get('evidence','')}"
            for f in failures
        ],
        "improvement_suggestions": suggestions,
    }
