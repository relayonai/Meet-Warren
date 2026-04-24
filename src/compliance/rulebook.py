"""Parse the Marketing Compliance Rulebook docx into a structured cache.

The rulebook contains a mix of:
- Explicit banned phrases (e.g. section 2.1.2, 2.1.3, 2.2.1, 2.2.2)
- Required disclaimer text (section 2.5.2)
- Domain rules (e.g. no crypto, no smooth growth curves)
- Categorical principles (FSMA s.21, fair clear & not misleading)

We keep two layers:
1. Hard rules — deterministic checks the grader runs without an LLM call.
2. Principles — natural-language rules the LLM grader evaluates qualitatively.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)


@dataclass
class HardRule:
    """A pattern that can be checked without an LLM."""
    id: str
    kind: str            # 'banned_phrase' | 'banned_term' | 'banned_topic' | 'required_disclaimer'
    pattern: str         # phrase / term / regex / canonical disclaimer text
    rationale: str       # why it's a problem (cites the rulebook section)
    section: str         # e.g. '2.1.2'
    severity: str = "high"  # 'high' | 'medium' | 'low'
    suggested_replacement: str = ""


@dataclass
class Principle:
    """A qualitative rule the LLM grader evaluates."""
    id: str
    title: str
    description: str
    section: str
    severity: str = "high"


@dataclass
class Rulebook:
    source_path: str
    extracted_at: str
    title: str
    raw_text: str        # full text for prompt context
    hard_rules: List[HardRule] = field(default_factory=list)
    principles: List[Principle] = field(default_factory=list)
    canonical_disclaimers: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Rulebook":
        return cls(
            source_path=d["source_path"],
            extracted_at=d["extracted_at"],
            title=d["title"],
            raw_text=d["raw_text"],
            hard_rules=[HardRule(**r) for r in d.get("hard_rules", [])],
            principles=[Principle(**p) for p in d.get("principles", [])],
            canonical_disclaimers=d.get("canonical_disclaimers", []),
        )


# ---------------------------------------------------------------------------
# Hardcoded rulebook contents (extracted from Marketing Compliance Rulebook.docx)
# These are explicit lists in the rulebook — extracting them deterministically
# avoids LLM hallucination and gives us a fast, free first-pass grader.
# ---------------------------------------------------------------------------

_BANNED_PHRASES = [
    # 2.1.2 — Speed and effort claims
    ("In under a minute",         "2.1.2", "Trivialises financial decision-making."),
    ("the time it takes to send a text", "2.1.2", "Trivialises financial decision-making."),
    # 2.2.1 — Specific instructions (advice boundary)
    ("Ask HR to",                 "2.2.1", "Specific instruction crosses the advice boundary."),
    ("Sell £10,000 shares",       "2.2.1", "Specific instruction crosses the advice boundary."),
    ("Target £9,730",             "2.2.1", "Specific instruction crosses the advice boundary."),
    ("Right-size exposure",       "2.2.1", "Portfolio-optimisation language crosses advice boundary."),
    ("Right-size crypto exposure","2.2.1", "Portfolio-optimisation language crosses advice boundary."),
    # 2.2.2 — Suitability language
    ("personal financial plan",   "2.2.2", "'Personal' implies suitability assessment."),
    ("full financial plan",       "2.2.2", "'Full' implies comprehensive suitability assessment."),
    ("tailored strategy",         "2.2.2", "Implies suitability assessment."),
    ("personal recommendation",   "2.2.2", "Implies suitability assessment."),
    ("best for you",              "2.2.2", "Implies suitability assessment."),
    ("right for you",             "2.2.2", "Implies suitability assessment."),
]

_BANNED_TERMS = [
    # 2.1.3 — Absolute and superlative claims
    ("leading",          "2.1.3", "Unsubstantiated absolute claim."),
    ("state of the art", "2.1.3", "Unsubstantiated absolute claim."),
    ("all-in-one",       "2.1.3", "Unsubstantiated absolute claim."),
    ("actually useful",  "2.1.3", "Unsubstantiated absolute claim."),
    ("best",             "2.1.3", "Unsubstantiated superlative claim."),
    ("fastest",          "2.1.3", "Unsubstantiated superlative claim."),
    ("guaranteed",       "1.2",   "Unqualified claim per FCA fair-clear-not-misleading."),
]

_BANNED_TOPICS = [
    # 2.4 — Cryptocurrency
    ("cryptocurrency", "2.4",
     "Cryptocurrency references must be removed from advertising materials."),
    ("crypto",         "2.4",
     "Cryptocurrency references must be removed from advertising materials."),
    ("bitcoin",        "2.4",
     "Cryptocurrency references must be removed from advertising materials."),
    ("ethereum",       "2.4",
     "Cryptocurrency references must be removed from advertising materials."),
]

_REPLACEMENTS = {
    "ask hr to":                 "you may wish to explore...",
    "sell £10,000 shares":       "one possible approach is to consider whether to reduce your holding (illustrative example).",
    "target £9,730":             "an illustrative target is shown for modelling purposes only.",
    "right-size exposure":       "review your exposure with a regulated adviser.",
    "personal financial plan":   "financial scenario",
    "full financial plan":       "financial scenario",
    "tailored strategy":         "modelled scenario",
    "personal recommendation":   "illustrative output",
    "best for you":              "based on the information you entered",
    "right for you":             "based on the information you entered",
    "leading":                   "established",
    "state of the art":          "modern",
    "all-in-one":                "comprehensive set of",
    "actually useful":           "designed to be useful",
    "best":                      "a strong",
    "fastest":                   "a quick",
    "guaranteed":                "designed to",
    "in under a minute":         "in approximately 10 minutes",
    "the time it takes to send a text": "in approximately 10 minutes",
}

_CANONICAL_DISCLAIMERS = [
    "Warren provides information, modelling and scenarios to support your own decisions. "
    "It is a planning tool and does not provide financial advice or recommendations.",
    "Warren helps you explore financial scenarios and understand possible outcomes. "
    "It is an educational and planning tool, not financial advice. "
    "Any decisions you make are your own, and you should consider speaking with a regulated adviser.",
    "Warren provides information, modelling and scenario analysis to assist users in making "
    "their own financial decisions. It does not provide financial advice, assess suitability, "
    "or recommend financial products.",
    "Warren is not financial advice. It helps you explore scenarios to support your decisions.",
    "Warren does not provide financial advice.",
    "Not financial advice.",
    "Information only, not advice.",
    "For planning only, not advice.",
    "Scenarios only, not advice.",
]

_PRINCIPLES = [
    Principle(
        id="P1_fair_clear_not_misleading",
        title="Fair, clear, and not misleading",
        description=(
            "Communications must be fair, clear, and not misleading per FSMA s.21 and "
            "the FCA's Conduct of Business standard. No exaggerated claims about speed or ease, "
            "no absolute or unqualified claims, no unqualified performance projections, no reliance "
            "on disclaimers to mitigate misleading headline claims."
        ),
        section="1.1, 1.2",
    ),
    Principle(
        id="P2_advice_boundary",
        title="Stay outside the regulated-advice perimeter",
        description=(
            "Must not give a personal recommendation to buy, sell, subscribe for, or exercise rights in "
            "a specific investment. No specific instructions, no portfolio-optimisation language, no action "
            "lists that are presented as suitable, optimal, or recommended."
        ),
        section="1.3, 2.2",
    ),
    Principle(
        id="P3_substantiated_claims",
        title="All claims substantiated and non-absolute",
        description=(
            "Per the CAP Code: every claim must be substantiated with evidence. Comparative claims must be "
            "evidence-based. Absolute claims must be provable. Performance claims must be representative."
        ),
        section="1.4, 2.1.3",
    ),
    Principle(
        id="P4_projections_balanced",
        title="Projections balanced with downside scenarios",
        description=(
            "Where projections, growth curves, or graphs are used, the disclaimer 'Forecasts are not a reliable "
            "indicator of future performance' must appear, both positive and negative scenarios must be shown, "
            "all assumptions disclosed, and smooth linear growth curves must not appear in isolation."
        ),
        section="2.3",
    ),
    Principle(
        id="P5_no_crypto",
        title="No cryptocurrency references in advertising materials",
        description=(
            "All references to cryptocurrency must be removed from advertising materials in all formats and channels."
        ),
        section="2.4",
    ),
    Principle(
        id="P6_disclaimer_present",
        title="Status disclosure (not financial advice) present and prominent",
        description=(
            "Customer-facing material must include the substantively-equivalent disclosure that Warren is a planning "
            "tool and does not provide financial advice or recommendations. The disclaimer must be readable, not "
            "buried in small print, and must not be contradicted by surrounding visuals or copy."
        ),
        section="2.5",
    ),
    Principle(
        id="P7_free_claims_genuine",
        title="'Free' claims must be genuine",
        description=(
            "Where a product or feature is described as 'free', the core promised output must be genuinely available "
            "at no charge. If an upgrade is required for the principal feature, that must be disclosed."
        ),
        section="2.6",
    ),
    Principle(
        id="P8_privacy_security_accurate",
        title="Privacy and security claims must accurately reflect actual practice",
        description=(
            "Any privacy or security claims must accurately reflect data-handling practice."
        ),
        section="2.7, 1.5",
    ),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _docx_to_text(docx_path: str) -> str:
    """Extract the docx contents as readable plain text."""
    try:
        from docx import Document  # python-docx
    except ImportError:
        return ""
    doc = Document(docx_path)
    parts = []
    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            continue
        style = (p.style.name or "").lower() if p.style else ""
        if "heading" in style or "title" in style:
            parts.append("\n" + text + "\n" + "-" * len(text))
        else:
            parts.append(text)
    return "\n".join(parts)


def _build_hard_rules() -> List[HardRule]:
    rules: List[HardRule] = []
    for phrase, section, rationale in _BANNED_PHRASES:
        rules.append(HardRule(
            id=f"HR_phrase_{phrase[:30].lower().replace(' ', '_')}",
            kind="banned_phrase", pattern=phrase, rationale=rationale,
            section=section, severity="high",
            suggested_replacement=_REPLACEMENTS.get(phrase.lower(), ""),
        ))
    for term, section, rationale in _BANNED_TERMS:
        rules.append(HardRule(
            id=f"HR_term_{term[:30].lower().replace(' ', '_')}",
            kind="banned_term", pattern=term, rationale=rationale,
            section=section, severity="high",
            suggested_replacement=_REPLACEMENTS.get(term.lower(), ""),
        ))
    for topic, section, rationale in _BANNED_TOPICS:
        rules.append(HardRule(
            id=f"HR_topic_{topic[:30].lower().replace(' ', '_')}",
            kind="banned_topic", pattern=topic, rationale=rationale,
            section=section, severity="high",
        ))
    return rules


def load_rulebook(
    docx_path: str = "data/Marketing Compliance Rulebook.docx",
    cache_path: str = "data/compliance_rules.json",
    force_refresh: bool = False,
) -> Rulebook:
    """Return a Rulebook, building the cache from the docx the first time."""
    docx_p  = Path(docx_path)
    cache_p = Path(cache_path)

    use_cache = (
        not force_refresh
        and cache_p.exists()
        and (not docx_p.exists() or cache_p.stat().st_mtime >= docx_p.stat().st_mtime)
    )
    if use_cache:
        try:
            return Rulebook.from_dict(json.loads(cache_p.read_text()))
        except Exception as e:
            log.warning("Compliance cache unreadable (%s) — rebuilding.", e)

    raw_text = _docx_to_text(str(docx_p)) if docx_p.exists() else ""
    title = "Marketing Compliance Guidebook"
    rb = Rulebook(
        source_path=str(docx_p),
        extracted_at=__import__("datetime").datetime.utcnow().isoformat(),
        title=title,
        raw_text=raw_text,
        hard_rules=_build_hard_rules(),
        principles=_PRINCIPLES,
        canonical_disclaimers=_CANONICAL_DISCLAIMERS,
    )
    try:
        cache_p.parent.mkdir(parents=True, exist_ok=True)
        cache_p.write_text(json.dumps(rb.to_dict(), ensure_ascii=False, indent=2))
    except Exception as e:
        log.warning("Could not write compliance cache: %s", e)
    return rb
