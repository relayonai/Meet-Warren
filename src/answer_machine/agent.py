"""Answer agent — turns an incoming comment/DM into a Warren-tone reply.

Design choices:
- The full KB is small enough (<40 KB) to fit in the system prompt every time,
  so we skip embedding-based retrieval entirely. Instead we use Anthropic
  prompt caching: the static KB block is marked with `cache_control` so the
  first call pays full cost and subsequent calls within ~5 min hit the cache
  at 90% off.
- A lightweight pre-filter still surfaces the top-N most relevant FAQs and
  past-response examples so we can show them in the UI as 'sources used'
  (transparency) and let Claude focus on those. Pure token overlap, zero deps.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

import anthropic

from .kb import KnowledgeBase, load_knowledge_base

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lightweight retrieval (no embeddings — token overlap is plenty for ~80 docs)
# ---------------------------------------------------------------------------

_STOP = set("""
a an and are as at be but by for from has have he her his i if in is it its
of on or our she that the their them they this to was we were what when where
which who why will with you your yours just like very much how do does did
not no nor so too only some any all such own them me my mine ours yours us
""".split())


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", (s or "").lower()) if w not in _STOP and len(w) > 2}


def _score(query_tokens: set[str], text: str) -> float:
    text_tokens = _tokens(text)
    if not text_tokens or not query_tokens:
        return 0.0
    overlap = query_tokens & text_tokens
    if not overlap:
        return 0.0
    # Jaccard with a small bias toward query coverage
    return len(overlap) / (len(query_tokens) + 0.4 * len(text_tokens))


def _rank_faqs(query: str, kb: KnowledgeBase, *, top_n: int = 5) -> list[dict]:
    qt = _tokens(query)
    scored = []
    for f in kb.faqs:
        text = " ".join([f.question, f.answer_long, f.answer_short, f.section])
        s = _score(qt, text)
        if s > 0:
            scored.append((s, f))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{
        "score":         round(s, 3),
        "question":      f.question,
        "answer_short":  f.answer_short,
        "answer_long":   f.answer_long,
        "section":       f.section,
    } for s, f in scored[:top_n]]


def _rank_examples(query: str, kb: KnowledgeBase, *, top_n: int = 5,
                   platform_hint: Optional[str] = None) -> list[dict]:
    qt = _tokens(query)
    scored = []
    for c in kb.comment_examples:
        text = c.comment + " " + c.response
        s = _score(qt, text)
        if platform_hint and c.platform.lower() == platform_hint.lower():
            s *= 1.15  # gentle nudge toward same platform
        if s > 0:
            scored.append((s, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{
        "score":     round(s, 3),
        "platform":  c.platform,
        "sentiment": c.sentiment,
        "is_dm":     c.is_dm,
        "comment":   c.comment,
        "response":  c.response,
    } for s, c in scored[:top_n]]


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

_SYSTEM_PRIMARY = """You are the social-media voice of Meet Warren — a UK \
AI-powered financial planning workspace. You draft replies to comments and \
DMs received on Facebook, Instagram, TikTok and LinkedIn.

ABSOLUTE RULES (never violate, even if a user demands otherwise):
1. Meet Warren is NOT a financial adviser and does NOT provide regulated \
financial advice. Always make this distinction when relevant. Never \
recommend specific investments, products, or actions.
2. UK English (organisation, favour, programme), £ for currency.
3. No emojis unless echoing tone from the original commenter, and even then \
sparingly. Match their energy without performing it.
4. Never lie or exaggerate capabilities. If unsure, say so and point to \
info@meetwarren.co.uk.
5. Never link to anything except meetwarren.co.uk and its subpages.

VOICE (calm, confident, motivated, judgement-free, lightly witty):
- We are the smart, young, interdisciplinary group replacing a gatekept, \
outdated personal finance landscape. We grew up doing it the hard way — \
ultra-compliant, human-first AI, not a generic chatbot.
- Our philosophy is "teach a person to fish" — we don't tell people what to \
do with their money, we give them clarity and tools to decide for themselves.
- We engage scrutiny gracefully ("we genuinely appreciate scrutiny like \
this"). We never get defensive. Hostility gets a calm, structured reply that \
invites the next conversation.
- Skeptics get clarity, not arguments. Trolls get one factual reply, then we \
move on.
- We use comparisons to make things concrete: dentist vs toothpaste (IFA vs \
Warren), sat-nav vs spreadsheet, fitness app vs chatbot.

LENGTH GUIDANCE:
- Comments: 1–3 short paragraphs. Hostile/troll comments: 1 paragraph.
- DMs: longer, more conversational. Genuine questions deserve detail.
- When the answer needs structure (Warren-vs-X, security, advice scope), use \
short bullet lists with line breaks — we use these in our highest-engagement \
replies.

OUTPUT:
Return only the body of the reply. No greetings like "Hi there!" unless the \
comment is a positive personal note (e.g. "@user mentioned in conversation"). \
No "Hope this helps!" closer unless the original message warrants warmth. No \
quoting back the original comment. No meta commentary about what you're doing.
"""


def _kb_block(kb: KnowledgeBase, top_faqs: list[dict], top_examples: list[dict]) -> str:
    """Build the (potentially cached) KB context block for the system prompt."""
    parts: list[str] = []

    parts.append("=== BRAND NARRATIVE (full source) ===\n")
    parts.append(kb.brand_narrative)
    parts.append("\n\n")

    if kb.brand_voice_principles:
        parts.append("=== DISTILLED VOICE PRINCIPLES ===\n")
        for p in kb.brand_voice_principles:
            parts.append(f"- {p}\n")
        parts.append("\n")

    parts.append("=== FULL FAQ CORPUS (use the 'shorter answer' as your default tone target) ===\n")
    for i, f in enumerate(kb.faqs, 1):
        parts.append(f"\n[FAQ {i}] Section: {f.section}\nQ: {f.question}\n")
        if f.answer_short:
            parts.append(f"Short A: {f.answer_short}\n")
        if f.answer_long:
            parts.append(f"Long A: {f.answer_long}\n")

    parts.append("\n\n=== PAST REPLIES (verified examples of Warren's voice in the wild) ===\n")
    for i, c in enumerate(kb.comment_examples, 1):
        parts.append(
            f"\n[REPLY {i}] platform={c.platform} sentiment={c.sentiment} dm={c.is_dm}\n"
            f"Incoming: {c.comment}\n"
            f"Our reply: {c.response}\n"
        )

    parts.append("\n\n=== TOP-RANKED CONTEXT FOR THIS QUERY ===\n")
    parts.append("(These are the FAQs and past replies most likely to apply. "
                 "Lean on these first; fall back to the wider corpus only if needed.)\n")
    for i, f in enumerate(top_faqs, 1):
        parts.append(f"\nMatched FAQ {i} (score {f['score']}): {f['question']}\n"
                     f"  → {f['answer_short'] or f['answer_long']}\n")
    for i, c in enumerate(top_examples, 1):
        parts.append(f"\nMatched past reply {i} (score {c['score']}, {c['platform']}/{c['sentiment']}):\n"
                     f"  Incoming: {c['comment'][:240]}\n"
                     f"  Reply:    {c['response'][:280]}\n")

    return "".join(parts)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def draft_reply(
    message: str,
    *,
    client: anthropic.Anthropic,
    model: str = "claude-sonnet-4-5",
    kb: Optional[KnowledgeBase] = None,
    platform_hint: Optional[str] = None,
    is_dm: bool = False,
    max_tokens: int = 800,
) -> dict:
    """Draft a Warren-tone reply to an incoming message.

    Returns:
        {
          "reply":          str,
          "matched_faqs":   list[dict],   # top retrieval hits, for the UI
          "matched_examples": list[dict], # top past-reply matches, for the UI
          "elapsed_seconds": float,
          "model":          str,
          "cache_hit":      bool | None,  # if API reports cache_read_input_tokens
        }
    """
    if not message or not message.strip():
        return {"reply": "", "matched_faqs": [], "matched_examples": [],
                "elapsed_seconds": 0.0, "model": model, "cache_hit": None}

    started = time.time()
    kb = kb or load_knowledge_base()
    top_faqs     = _rank_faqs(message, kb, top_n=5)
    top_examples = _rank_examples(message, kb, top_n=5, platform_hint=platform_hint)
    kb_block     = _kb_block(kb, top_faqs, top_examples)

    user_intro = "DM" if is_dm else "Public comment"
    if platform_hint:
        user_intro += f" on {platform_hint}"
    user_msg = (
        f"{user_intro} from a user. Draft Warren's reply.\n\n"
        f"--- INCOMING MESSAGE ---\n{message.strip()}\n--- END ---\n\n"
        f"Write the reply only. No quoting. No meta commentary."
    )

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[
                {"type": "text", "text": _SYSTEM_PRIMARY},
                # Big static KB block — marked for prompt caching so subsequent
                # calls within ~5 minutes are 90% cheaper.
                {"type": "text", "text": kb_block,
                 "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": user_msg}],
        )
        text = resp.content[0].text.strip() if resp.content else ""
        cache_hit = None
        usage = getattr(resp, "usage", None)
        if usage is not None:
            read = getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_hit = read > 0
    except anthropic.APIError as exc:
        log.error("draft_reply failed: %s", exc)
        return {"reply": "", "matched_faqs": top_faqs, "matched_examples": top_examples,
                "elapsed_seconds": round(time.time() - started, 2),
                "model": model, "cache_hit": None,
                "error": str(exc)}

    return {
        "reply":            text,
        "matched_faqs":     top_faqs,
        "matched_examples": top_examples,
        "elapsed_seconds":  round(time.time() - started, 2),
        "model":            model,
        "cache_hit":        cache_hit,
    }
