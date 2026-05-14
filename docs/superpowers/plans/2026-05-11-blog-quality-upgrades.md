# Blog Quality Upgrades — Implementation Plan
**Date:** 2026-05-11  
**Goal:** Every generated blog post scores 85+/100 (Strong band) before being saved.  
**Baseline:** avg 56.8/100 across 4 posts (Technical 33%, E-E-A-T 57%, SEO 59%)

---

## Files touched

| File | What changes |
|---|---|
| `src/blog_generator.py` | Schema type fix, hero_image_prompt field in JSON schema, prompt hardening (paragraph length, inline citations, sentence discipline) |
| `src/exporters.py` | Frontmatter enrichment (slug, og_image, keyword), trust footer appended to conclusion |
| `src/readability_pass.py` | **New file** — dedicated readability + flow revision pass |
| `src/blog_quality_revision.py` | Wire in readability pass before quality loop; raise target 78→85, max iterations 2→3 |
| `src/config.py` | Add `warren_og_image` field |
| `.env.example` | Document `WARREN_OG_IMAGE` |

---

## Task 1 — Schema type: `NewsArticle` → `BlogPosting` + add Person schema

**File:** `src/blog_generator.py` — `build_jsonld()` function (lines 555–602)

**Why it matters:** The quality analyser only credits `BlogPosting` or `Article` for the schema score.
Currently using `NewsArticle` → schema_score = 0. Fix gets us to 3/4 immediately.

**Steps:**
1. In `build_jsonld`, change `"@type": "NewsArticle"` → `"@type": "BlogPosting"`.
2. Change `"author"` from `[{"@type": "Organization", "name": "Warren Editorial Desk"}]`
   to a list with both an `Organization` entry (publisher) AND a `Person` entry:
   ```python
   "author": [
       {"@type": "Person", "name": "Warren Editorial Team",
        "url": "https://meetwarren.co.uk/about"},
       {"@type": "Organization", "name": "Warren",
        "url": "https://meetwarren.co.uk"},
   ]
   ```
3. Also update the `itemtype` attribute on the `<article>` tag in `blog_to_html()`
   (line 737): change `schema.org/NewsArticle` → `schema.org/BlogPosting`.
4. Update the docstring on `build_jsonld` to say BlogPosting.

**Verify:** Run:
```bash
cd "/Users/keremyilmaz/Warren Workflow" && source venv/bin/activate && python3 -c "
from src.blog_generator import build_jsonld
r = build_jsonld({'title':'Test','faqs':[{'question':'Q','answer':'A'}]})
assert r[0]['@type'] == 'BlogPosting', r[0]['@type']
assert any(a.get('@type')=='Person' for a in r[0]['author']), r[0]['author']
assert r[1]['@type'] == 'FAQPage'
print('PASS schema types')
"
```

**Expected score impact:** Technical schema: 1→4 (+3 pts)

---

## Task 2 — Frontmatter enrichment: slug + og_image + keyword

**Files:** `src/config.py`, `src/exporters.py` — `_md_blog()` (lines 386–503), `.env.example`

**Why it matters:** No slug → URL score 1/3. No og_image → social meta 0/2. No keyword field →
keyword placement partial credit only.

**Steps:**

### 2a — Add `warren_og_image` to Config
In `src/config.py`:
1. Add field to `Config` dataclass: `warren_og_image: str`
2. In `load_config()`, read it: `warren_og_image=os.getenv("WARREN_OG_IMAGE", "").strip()`

### 2b — Enrich markdown frontmatter
In `src/exporters.py`, `_md_blog()` — after line 410 (after `out.append("---")`), add these
fields inside the frontmatter block (between the opening and closing `---`):

```python
# slug — derived from the output filename stored in result, or slugified title
slug = result.get("_output_basename") or ""
if not slug:
    import re as _re
    slug = _re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")[:60]
if slug:
    out_fm.append(f"slug: {_yaml_str(slug)}")

# keyword — first seo_tag makes the best primary keyword
keyword = tags[0] if tags else ""
if keyword:
    out_fm.append(f"keyword: {_yaml_str(keyword)}")

# og_image / image — from env config or empty
from src.config import load_config as _lc
try:
    _cfg = _lc()
    og_img = _cfg.warren_og_image
except Exception:
    og_img = ""
if og_img:
    out_fm.append(f"image: {_yaml_str(og_img)}")
    out_fm.append(f"og_image: {_yaml_str(og_img)}")
```

> **Note:** Refactor `_md_blog` to build frontmatter lines in a list `out_fm`, then
> `out += ["---"] + out_fm + ["---", ""]`. This avoids repeated `out.append` ordering bugs.

### 2c — Pass `_output_basename` into the result dict
In `dashboard.py`, before calling `_write_all_formats`, add:
`result["_output_basename"] = base`

### 2d — Update `.env.example`
Add: `WARREN_OG_IMAGE=` with a comment explaining it's used for og:image in markdown frontmatter.

**Verify:**
```bash
cd "/Users/keremyilmaz/Warren Workflow" && source venv/bin/activate && python3 -c "
from src.exporters import to_markdown
post = {'title': 'Test Post About ISAs', 'seo_tags': ['isa', 'savings'],
        'meta_description': 'x'*150, 'byline': 'Warren', 'published_iso': '2026-05-11',
        '_output_basename': 'blog-2026-05-11-v1', 'intro': 'Hello.', 'sections': [],
        'conclusion': 'Done.'}
md = to_markdown(post, kind='blog')
assert 'slug:' in md, 'missing slug'
assert 'keyword:' in md, 'missing keyword'
print('PASS frontmatter fields')
print(md[:500])
"
```

**Expected score impact:** SEO URL structure: 1→3 (+2), Technical social meta: 0→1 (+1 if og_image set)

---

## Task 3 — Hero image prompt field in generation schema

**File:** `src/blog_generator.py` — `USER_TEMPLATE` (lines 81–176)

**Why it matters:** Stores a Midjourney/DALL-E ready description in the JSON sidecar for future
art generation. No score impact now.

**Steps:**
1. In `USER_TEMPLATE`, add `hero_image_prompt` to the JSON schema block after `visual_elements`:
   ```
   "hero_image_prompt": "string (1-2 sentence image brief for a UK personal finance
     editorial illustration — describe scene, mood, colours. No text overlays.
     Example: 'A split-screen showing a rising interest rate graph on the left and a
     worried homeowner reviewing mortgage paperwork on the right, muted blues and greys,
     editorial photography style.')"
   ```
2. In `generate_blog_post()`, add `data.setdefault("hero_image_prompt", "")` after the other
   `setdefault` calls (around line 383).
3. No other changes needed — the field will be preserved through the revision loop and
   written into the JSON sidecar automatically.

**Verify:** Generate a post and check `output/blog-*.json` contains `hero_image_prompt` key
with a non-empty string.

---

## Task 4 — Trust footer appended at export time

**File:** `src/exporters.py` — `_md_blog()` and `blog_to_html()`

**Why it matters:** E-E-A-T Trust score is 1/4 because no About/Contact signals exist in the
post body. A fixed footer gets us to 3/4 without touching generation prompts.

**Steps:**

### 4a — Markdown exporter
In `_md_blog()`, after the conclusion is written (before the FAQ block, around line 472),
add a constant:

```python
_TRUST_FOOTER_MD = (
    "\n_This analysis was prepared by the Warren Editorial Team — "
    "an independent UK personal finance platform. "
    "[About Warren](https://meetwarren.co.uk/about) · "
    "[Contact](mailto:info@meetwarren.co.uk)_"
)
```

Append it right after `out += ["## The Bottom Line", "", result.get("conclusion", ""), ""]`:
```python
out += [_TRUST_FOOTER_MD, ""]
```

### 4b — HTML exporter
In `blog_to_html()`, locate where the conclusion is rendered (search for `"The Bottom Line"`
or conclusion rendering). Add an HTML trust footer div after the conclusion:

```python
_TRUST_FOOTER_HTML = (
    '<p class="warren-trust-footer">'
    'This analysis was prepared by the '
    '<a href="https://meetwarren.co.uk/about">Warren Editorial Team</a> '
    '— an independent UK personal finance platform. '
    '<a href="mailto:info@meetwarren.co.uk">Contact us</a>.'
    '</p>'
)
```

**Verify:**
```bash
cd "/Users/keremyilmaz/Warren Workflow" && source venv/bin/activate && python3 -c "
from src.blog_quality import analyze_text
# Minimal post with trust footer
content = '''---
title: Test
author: Warren Editorial Team
date: 2026-05-11
---
# Test
## Section One
Some content here.
## The Bottom Line
Conclusion.
_This analysis was prepared by the Warren Editorial Team — an independent UK personal finance platform. [About Warren](https://meetwarren.co.uk/about) · [Contact](mailto:info@meetwarren.co.uk)_
'''
r = analyze_text(content)
trust = r['score']['category_details']['eeat_signals']['breakdown']['trust']
print('Trust score:', trust, '(target: 3+)')
assert trust >= 3, f'Trust score too low: {trust}'
print('PASS trust footer')
"
```

**Expected score impact:** E-E-A-T Trust: 1→3 (+2 pts)

---

## Task 5 — Generation prompt hardening

**File:** `src/blog_generator.py` — `USER_TEMPLATE` quality rubric section (lines 131–175)

**Why it matters:** Three persistent failures in generated posts:
- Flesch reading ease ~43 (target 60-70) — sentences too dense
- Transition words ~3% (target 20-30%) — nearly zero
- Citable passages = 0 (target 5+ sections of 120-180 words)
- Inline citations consistently < 5

**Steps:**
Replace the existing `CONTENT (30 pts)` and `SEO (25 pts)` rubric blocks with strengthened
versions that include worked examples.

### 5a — Paragraph length rule (replaces vague "sections ~120-180 words" note)
Add to the CONTENT block:

```
PARAGRAPH LENGTH — MANDATORY (affects AI Citation Readiness):
Each section MUST open with a standalone "anchor paragraph" of 120-180 words.
Count your words. If the anchor paragraph is under 100 or over 200 words, rewrite it.

GOOD (131 words): "The Bank of England's decision to hold rates at 4.75% in March 2026
surprised most economists, who had forecast a cut following February's softer inflation
print. For mortgage holders on tracker rates, this means another month of elevated
payments — the average two-year tracker is costing roughly £180 more per month than it
did in 2021, according to UK Finance (2026). But the picture is more nuanced than the
headline suggests. Fixed-rate borrowers who locked in during 2023 and 2024 are
increasingly rolling off onto products that are cheaper than their expiring deals,
provided they remortgage promptly. The critical question for the next six months is
whether the MPC can thread the needle between taming residual services inflation and
avoiding a housing-market slowdown."

BAD: Three-sentence paragraph followed by a bullet list. Too short to cite.
```

### 5b — Inline citation rule (hard requirement)
Replace the existing citation note in the SEO block with:

```
INLINE CITATIONS — HARD REQUIREMENT (every statistic must be attributed):
After EVERY number, percentage, £ figure, or institutional claim, add "(Source, year)"
immediately — no exceptions. Do not group citations at the end of a paragraph.

GOOD: "Inflation fell to 2.6% in March 2026 (ONS, 2026), its lowest reading since
the post-pandemic peak, but wage growth remained at 5.6% (ONS, 2026), keeping the
MPC cautious."

BAD: "Inflation fell to 2.6% and wages grew 5.6%." — no citations, fails E-E-A-T.

Every source in sources_cited MUST appear at least once as an inline citation.
Minimum 5 inline citations total.
```

### 5c — Sentence discipline rule (targeted at readability failure)
Add to the CONTENT grammar block:

```
SENTENCE LENGTH — HARD LIMITS (Flesch target 60-70):
- Average sentence: 15-20 words. Count after writing.
- Maximum single sentence: 30 words. If a sentence exceeds 30 words, split it.
- Never use more than two subordinate clauses in one sentence.

GOOD (18 words): "Mortgage rates have fallen steadily this year, but the gap between
best buys and standard variable rates remains unusually wide."

BAD (47 words): "While the Bank of England's decision to maintain its base rate at
4.75% in March was widely anticipated by markets following the stronger-than-expected
services inflation data released in February, it nonetheless disappointed homeowners who
had been hoping for relief on their monthly payments."
→ Split into two sentences.

TRANSITION WORDS — TARGET 20-30% of sentences:
Start at least 1 in 4 sentences with a transition word or phrase.
Use: However, Therefore, As a result, Meanwhile, By contrast, Notably, That said,
In practice, For example, Crucially, Beyond this, On balance, In turn.
Do not use: Furthermore, Moreover, Leverage, Delve, Navigate the landscape.
```

**Verify:** After implementing, regenerate one post and check:
```bash
cd "/Users/keremyilmaz/Warren Workflow" && source venv/bin/activate && python3 -c "
from src.blog_quality import analyze_file
# After running a fresh generate, check the latest blog
import glob, os
files = sorted(glob.glob('output/blog-*.md'), key=os.path.getmtime, reverse=True)
if files:
    r = analyze_file(files[0])
    print('Flesch:', r['readability']['flesch_reading_ease'], '(target 60-70)')
    print('Transitions:', r['transition_words']['transition_pct'], '% (target 20-30)')
    print('Citable passages:', r['ai_citation_readiness']['citable_passages'], '(target 5+)')
    print('Inline citations:', r['citations']['inline_citations'], '(target 5+)')
"
```

**Expected score impact:** Content readability: 1→6 (+5), grammar/antipattern: 1→3 (+2),
AI Citation citability: 0→3 (+3)

---

## Task 6 — New file: `src/readability_pass.py`

**Why it matters:** Prompt hardening helps new posts but can't fix posts that the model generates
below-par. The readability pass is a focused Claude call that runs before the quality loop and
targets the two signals most consistently below floor: Flesch ease and transition word density.

**Create `src/readability_pass.py`** with this structure:

```python
"""Dedicated readability + flow revision pass for generated blog posts.

Runs BEFORE the quality revision loop. Its only job:
  1. Split sentences over 30 words
  2. Add transition words to hit 20-30% sentence density
  3. Ensure zero sentences exceed 40 words

Returns the revised post dict (merged with original), or the original if the
pass fails or does not improve the Flesch score.
"""
from __future__ import annotations
import json
import logging
from typing import Callable, Optional
import anthropic
from ._json import parse_json_response
from .blog_quality import quick_score
from .brand_voice import voice_block

log = logging.getLogger(__name__)

_READABILITY_PERSONA = (
    "You are a UK copy editor specialising in personal finance writing. "
    "Your only job in this pass is to improve sentence rhythm and flow — "
    "do NOT change facts, statistics, structure, or argument. "
    "You return ONLY valid JSON. No prose outside the object."
)

_GOOD_SENTENCE = (
    "GOOD (18 words): 'Mortgage rates have fallen steadily this year, "
    "but the gap between best buys and SVRs remains wide.'"
)
_BAD_SENTENCE = (
    "BAD (47 words): 'While the Bank of England's decision to maintain "
    "its base rate at 4.75% in March was widely anticipated by markets "
    "following the stronger-than-expected services inflation data released "
    "in February, it nonetheless disappointed homeowners hoping for relief.' "
    "→ Split into two sentences."
)
_GOOD_TRANSITION = (
    "GOOD: 'Inflation fell to 2.6% in March. However, wage growth remained "
    "elevated at 5.6%, keeping the MPC cautious. As a result, further rate "
    "cuts look unlikely before August.'"
)
_BAD_TRANSITION = (
    "BAD: 'Inflation fell to 2.6% in March. Wage growth remained elevated "
    "at 5.6%. Rate cuts look unlikely before August.' — no transitions, "
    "reads as a list of facts."
)

_READABILITY_TEMPLATE = """Revise this blog post to fix ONLY sentence length and flow.

RULES — do not change anything else:
1. SENTENCE LENGTH: Split any sentence over 30 words into two sentences.
   No sentence may exceed 40 words. Average target: 15-20 words.
   {good_sentence}
   {bad_sentence}

2. TRANSITION WORDS: 20-30% of sentences must begin with or contain a
   transition word/phrase. Add transitions where natural.
   Use: However, Therefore, As a result, Meanwhile, By contrast, Notably,
   That said, In practice, For example, Crucially, Beyond this, On balance,
   In turn, Importantly, Nevertheless.
   {good_transition}
   {bad_transition}

3. DO NOT change: facts, statistics, £ figures, citations, argument,
   section order, headings, key_takeaways, faqs, sources_cited, title,
   meta_description, byline, seo_tags.

Return the SAME JSON shape as the input, with only intro/sections/conclusion
text modified. Add "_readability_changes": ["short bullet per change"] key.

ORIGINAL POST (JSON):
{post_json}
"""


def run_readability_pass(
    post: dict,
    *,
    client: anthropic.Anthropic,
    model: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """Run the readability + flow pass. Returns improved post or original on failure.

    Return shape:
        {
          "final_post":  dict,   # revised post (or original if no improvement)
          "improved":    bool,
          "before_flesch": float,
          "after_flesch":  float,
          "changes":     list[str],
        }
    """
    from .exporters import to_markdown

    def _md(p): return to_markdown(p, kind="blog")

    if progress_cb: progress_cb("readability pass: scoring original")
    before_score = quick_score(_md(post), suffix=".md")
    before_flesch = (before_score.get("raw", {})
                     .get("readability", {})
                     .get("flesch_reading_ease", 0))
    before_transitions = (before_score.get("raw", {})
                          .get("transition_words", {})
                          .get("transition_pct", 0))

    # Skip if already meets both targets
    if before_flesch >= 60 and before_transitions >= 20:
        log.info("Readability pass skipped — Flesch %.1f, transitions %.1f%% both OK",
                 before_flesch, before_transitions)
        return {"final_post": post, "improved": False,
                "before_flesch": before_flesch, "after_flesch": before_flesch,
                "changes": []}

    if progress_cb: progress_cb("readability pass: revising sentences + flow")
    prompt = _READABILITY_TEMPLATE.format(
        good_sentence=_GOOD_SENTENCE,
        bad_sentence=_BAD_SENTENCE,
        good_transition=_GOOD_TRANSITION,
        bad_transition=_BAD_TRANSITION,
        post_json=json.dumps(post, ensure_ascii=False, indent=2),
    )

    try:
        max_tok = min(16000, max(6000, len(json.dumps(post)) // 3 + 2000))
        resp = client.messages.create(
            model=model,
            max_tokens=max_tok,
            system=[
                {"type": "text",
                 "text": voice_block(include_past_replies=False, max_replies=0),
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": _READABILITY_PERSONA},
            ],
            messages=[{"role": "user", "content": prompt}],
        )
        revised = parse_json_response(resp.content[0].text)
    except Exception as exc:
        log.warning("Readability pass failed: %s", exc)
        return {"final_post": post, "improved": False,
                "before_flesch": before_flesch, "after_flesch": before_flesch,
                "changes": []}

    if not isinstance(revised, dict) or "intro" not in revised:
        log.warning("Readability pass returned invalid structure — discarding.")
        return {"final_post": post, "improved": False,
                "before_flesch": before_flesch, "after_flesch": before_flesch,
                "changes": []}

    changes = revised.pop("_readability_changes", [])
    # Merge: revised text fields override, everything else keeps original
    merged = {**post, **{k: v for k, v in revised.items()
                         if k in ("intro", "sections", "conclusion")}}
    # Server-controlled fields stay original
    for k in ("published_iso", "published_human", "reading_time_minutes",
              "_diversity_warning", "_outline", "title", "meta_description",
              "byline", "seo_tags", "key_takeaways", "faqs", "sources_cited",
              "hero_image_prompt"):
        if k in post:
            merged[k] = post[k]

    if progress_cb: progress_cb("readability pass: re-scoring")
    after_score = quick_score(_md(merged), suffix=".md")
    after_flesch = (after_score.get("raw", {})
                    .get("readability", {})
                    .get("flesch_reading_ease", 0))
    after_transitions = (after_score.get("raw", {})
                         .get("transition_words", {})
                         .get("transition_pct", 0))

    improved = after_flesch > before_flesch or after_transitions > before_transitions
    if not improved:
        log.info("Readability pass did not improve scores — keeping original.")
        return {"final_post": post, "improved": False,
                "before_flesch": before_flesch, "after_flesch": after_flesch,
                "changes": changes}

    log.info("Readability pass: Flesch %.1f→%.1f, transitions %.1f%%→%.1f%%",
             before_flesch, after_flesch, before_transitions, after_transitions)
    return {"final_post": merged, "improved": True,
            "before_flesch": before_flesch, "after_flesch": after_flesch,
            "changes": changes}
```

**Verify:**
```bash
cd "/Users/keremyilmaz/Warren Workflow" && source venv/bin/activate && python3 -c "
from src.readability_pass import run_readability_pass
print('Import OK')
"
```

---

## Task 7 — Wire readability pass + raise quality bar

**File:** `src/blog_quality_revision.py` (lines 195–277) and `dashboard.py` (lines 1206–1223)

**Steps:**

### 7a — Raise target and iterations in `blog_quality_revision.py`
Change line 29: `DEFAULT_TARGET_SCORE = 78` → `DEFAULT_TARGET_SCORE = 85`
Change line 30: `DEFAULT_CATEGORY_FLOOR_PCT = 0.55` → `DEFAULT_CATEGORY_FLOOR_PCT = 0.50`
Change the default in `revise_for_quality` signature: `max_iterations: int = 2` → `max_iterations: int = 3`

### 7b — Wire readability pass into the dashboard generation pipeline
In `dashboard.py`, in the quality section (around line 1206), insert the readability pass
BEFORE the existing quality revision loop:

```python
# --- Readability + flow pass (blog only) ----------------------------
if kind == "blog":
    _set_stage(job_id, "quality_loop", sub="readability + flow pass")
    try:
        from src.readability_pass import run_readability_pass
        read_result = run_readability_pass(
            result, client=client, model=cfg.anthropic_model,
            progress_cb=lambda s: _set_stage(job_id, "quality_loop", sub=s),
        )
        if read_result.get("improved"):
            result = read_result["final_post"]
            out_html = blog_to_html(result)
            out_text = blog_to_text(result)
    except Exception as e:
        print(f"Readability pass failed (non-fatal): {e}")
```

Then update the existing `revise_for_quality` call to use new defaults (no need to pass
`target_score` or `max_iterations` explicitly since defaults now read 85/3):

```python
quality_revision = revise_for_quality(
    result, client=client, model=cfg.anthropic_model, kind="blog",
    progress_cb=lambda s: _set_stage(job_id, "quality_loop", sub=s),
)
```

### 7c — Store readability pass result in the job output
In the same block, capture the readability result in the job's output alongside
`quality_revision` so the dashboard can display it:
```python
# Store for audit display
quality_revision = quality_revision or {}
quality_revision["readability_pass"] = {
    "improved": read_result.get("improved", False),
    "before_flesch": read_result.get("before_flesch"),
    "after_flesch": read_result.get("after_flesch"),
    "changes": read_result.get("changes", []),
}
```

**Verify (integration):**
After all tasks are complete, regenerate one post from the dashboard and confirm:
1. The stage progress shows "readability + flow pass" before "quality_loop"
2. The final `.md` file's schema contains `BlogPosting`
3. The frontmatter contains `slug:` and `keyword:`
4. The trust footer appears before the FAQ section
5. Run the quality analyser on the output:

```bash
cd "/Users/keremyilmaz/Warren Workflow" && source venv/bin/activate && python3 -c "
import glob, os
from src.blog_quality import analyze_file
files = sorted(glob.glob('output/blog-*.md'), key=os.path.getmtime, reverse=True)
r = analyze_file(files[0])
s = r['score']
print(f'Total: {s[\"total\"]}/100  {s[\"rating\"]}')
for k, v in s['categories'].items():
    print(f'  {k}: {v}')
print('Target: 85+ (Strong)')
"
```

---

## Execution order

Run tasks in this order — each is independent but Task 7 depends on Task 6:

1. Task 1 (schema fix) — isolated, 5 min
2. Task 2 (frontmatter) — isolated, 15 min
3. Task 3 (hero image prompt) — isolated, 10 min
4. Task 4 (trust footer) — isolated, 10 min
5. Task 5 (prompt hardening) — isolated, 20 min
6. Task 6 (new readability_pass.py) — isolated, 30 min
7. Task 7 (wire + raise bar) — depends on Task 6, 15 min

**After all 7 tasks:** run the integration verify from Task 7 and confirm score ≥ 85.

---

## Rollback

All changes are confined to Python source files. If a task degrades quality:
- Tasks 1–5: revert the specific lines changed
- Task 6: delete `src/readability_pass.py`
- Task 7: revert `DEFAULT_TARGET_SCORE` to 78, `max_iterations` default to 2,
  remove the readability pass block in `dashboard.py`

The existing posts in `output/` are never modified by these changes.
