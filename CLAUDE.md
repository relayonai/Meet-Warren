# Warren Workflow — CLAUDE.md

## What This Project Is
A Plotly Dash web dashboard (`http://127.0.0.1:8050`) for generating
compliance-checked, brand-voiced UK personal finance content (blogs +
newsletters). Built on top of a CLI scraper that feeds a SQLite database.

---

## How to Run
```bash
source venv/bin/activate
python dashboard.py        # starts the Dash app on :8050
python main.py scrape      # manual scrape (no scheduler — manual only)
python main.py generate    # newsletter via CLI
```

---

## Project Structure
```
Warren Workflow/
├── dashboard.py          # Main Dash app — ALL pages wired here (~3900 lines)
├── main.py               # Click CLI: scrape / generate / compliance commands
├── requirements.txt
├── .env.example
├── assets/               # CSS (warren theme, sidebar, create page styles)
├── logs/
├── data/
│   ├── articles.db                  # SQLite — article store
│   ├── compliance_rules.json        # Cached parsed rulebook (auto-rebuilt)
│   └── answer_machine_sources/      # brand_narrative.docx, faqs.docx, meta_comments.xlsx
└── src/
    ├── answer_machine/
    │   ├── __init__.py    # load_knowledge_base(), draft_reply(), append_exemplar(), delete_exemplar()
    │   ├── agent.py       # draft_reply() — prompt assembly + Claude call with cache_control
    │   └── kb.py          # KnowledgeBase dataclass, parsers, build/load/append/delete
    ├── compliance/
    │   ├── __init__.py    # Re-exports: grade_content(), analyze_findings(), ensure_compliant(),
    │   │                  #             scan_article(), load_rulebook()
    │   ├── advisor.py     # parse_elements(), advise_document() — line-by-line doc audit
    │   │                  # temperature=0, named-principle checklist, extended hard rules
    │   ├── analyzer.py    # analyze_findings() — categorises grading output
    │   ├── enforcer.py    # revise_content() — LLM-driven compliance rewrite
    │   ├── grader.py      # grade_content() — hard pass + LLM principle pass → pass/warn/fail
    │   ├── pipeline.py    # ensure_compliant(), scan_article(), init_compliance_tables()
    │   └── rulebook.py    # HardRule, Principle, Rulebook dataclasses; load_rulebook()
    ├── archive.py             # archive_stats(), list_archive(), get_entry(), delete_entry()
    ├── blog_generator.py      # generate_blog_post(), blog_to_html(), blog_to_text()
    ├── blog_quality.py        # quick_score() — 100-pt rubric
    ├── blog_quality_revision.py  # revise_for_quality() — revision loop
    ├── brand_review.py        # review_brand_voice() — KB-grounded brand voice audit
    ├── config.py              # load_config(), build_anthropic_client()
    ├── database.py            # SQLite schema, get_connection(), init_db(), query_articles(),
    │                          # insert_article(), existing_urls(), get_source_log(), mark_source_scraped()
    ├── deduplicator.py        # filter_new()
    ├── exporters.py           # to_pdf(), to_docx(), to_markdown(), to_eml()
    ├── formatter.py           # to_html(), to_text() (newsletter formatter)
    ├── generator.py           # generate_newsletter()
    ├── internal_links.py      # load_published_corpus()
    ├── publisher.py           # local / Gmail / Notion outputs
    ├── scraper.py             # fetch_rss(), fetch_govuk_api(), fetch_http(),
    │                          # RSS_SOURCE_OVERRIDES, title_passes_prefilter()
    ├── source_verifier.py     # verify_urls(), summarise()
    ├── summariser.py          # summarise() — per-article LLM summarisation
    └── _json.py               # tolerant JSON parsing for LLM responses
```

---

## Dashboard Pages & Routes

| Route | Function | Purpose |
|-------|----------|---------|
| `/` | `_database_page()` | Article DB — Overview charts, Browser, Scrape |
| `/create` | `_create_page()` | Generate blogs/newsletters |
| `/compliance` | `_compliance_page()` | Document advisor + rulebook + run log |
| `/answer-machine` | `_answer_machine_page()` | Brand-voiced Q&A |
| `/archive` | `_archive_page()` | Browse/preview/download/delete past generations |

### Sidebar nav order
🗄 Database → ✍ Create → 🛡 Compliance → 💬 Answer Machine → 🗂 Archive

---

## Create Page Pipeline

Stages (set via `_set_stage(job_id, stage_key)`):
```
collect → draft → verify → quality_loop → brand_review → compliance → export → quality → done
```

| Stage | What happens |
|-------|-------------|
| collect | Load article context from DB |
| draft | LLM drafts content (blog or newsletter) |
| verify | `source_verifier.py` checks all URLs |
| quality_loop | `blog_quality_revision.py` revision loop (blog only) |
| brand_review | `brand_review.py` — KB-grounded voice audit, severity-graded |
| compliance | `ensure_compliant()` grading + optional revision |
| export | Renders 7 formats: HTML / PDF / DOCX / MD / EML / TXT / JSON |
| quality | Final 100-pt rubric via `blog_quick_score()` |
| done | Success — shows download bar + all result cards |

Jobs run in background threads (`_jobs` dict + `threading.Lock()`).
UI polls via `dcc.Interval(id="cr-job-poll", interval=600)`.

### Content Types
- **Newsletter** — email digest with sections + editor pick
- **Blog Post** — long-form analysis with TL;DR + FAQ

### Create Page UI Structure
- Left pane: filter strip (source, category, timeframe, min score) + article DataTable
- Right pane: selection badge → content type tiles → editor's angle textarea → sticky generate bar
- Output below shell: progress card → result cards (download bar, source verification,
  quality revision, brand review, compliance, quality score)

---

## Key Modules

### brand_review.py
- `review_brand_voice(content, kb, client, model, kind) -> dict`
- Uses KB (brand_narrative + voice_principles + comment_examples) as `cache_control: ephemeral` system block
- Returns: `{grade: pass|warn|fail, issues: [{severity, field, finding, suggestion}], summary}`
- Grade logic: fail ≥2 criticals; warn = 1 critical or ≥3 warnings; pass otherwise
- JSON sidecar key: `brand_review`

### compliance/advisor.py
- `parse_elements(text) -> list[dict]` — splits plain text into typed, indexed elements
  (`heading`, `paragraph`, `list_item`, `line`)
- `advise_document(elements, rulebook, client, model) -> dict` — line-by-line audit
- Two-pass architecture:
  - **Hard pass** (deterministic): rulebook banned phrases/terms/topics + extended rules
  - **LLM pass** (temperature=0, named-principle checklist): evaluates against P1–P8 only
- Extended hard rules cover: advice-perimeter language (`you should invest`, `our advice is`…),
  performance promises (`risk-free`, `will grow`, `guaranteed return`…), UK English spellings
- Returns: `{findings, clean_count, total_count, summary, elapsed_seconds}`

### compliance/grader.py
- `grade_content(content, kind, client, model, rulebook) -> dict`
- Two passes: deterministic hard rules + LLM principle evaluation
- `kind`: `'article'` | `'newsletter'` | `'blog'` — controls disclaimer check and topic severity
- Used by Create pipeline (`ensure_compliant`) — not the document advisor

### compliance/pipeline.py
- `ensure_compliant()` — grades + optionally revises until pass or max iterations
- `scan_article()` — lightweight compliance scan for scraped articles
- `init_compliance_tables()` — sets up `compliance_log` SQLite table

### compliance/rulebook.py
- `load_rulebook()` — loads from `data/compliance_rules.json` cache; rebuilds from .docx if stale
- `Rulebook` dataclass: `hard_rules: list[HardRule]`, `principles: list[Principle]`,
  `canonical_disclaimers: list[str]`
- 24 hard rules (banned phrases/terms/topics) + 8 principles (P1–P8)
- **Never modified at runtime** — advisor extended rules are local to `advisor.py`

### source_verifier.py
URL status taxonomy:
| Status | Condition | UI treatment |
|--------|-----------|-------------|
| `ok` | 2xx/3xx | ✓ verified live |
| `blocked` | 401/403/429 from known paywalled outlet | ℹ informational |
| `broken` | 404/4xx/5xx/timeout/ssl/unreachable | ✗ red |

PAYWALLED_DOMAINS allowlist: ft.com, thetimes.co.uk, telegraph.co.uk, wsj.com,
bloomberg.com, economist.com, nytimes.com, washingtonpost.com, reuters.com,
bbc.co.uk, theguardian.com, moneyweek.com, thisismoney.co.uk, cityam.com,
dailymail.co.uk, express.co.uk, investorschronicle.co.uk, morningstar.co.uk

### blog_quality_revision.py
- `revise_for_quality()` — main entry point; `_revise_once()` — single revision pass
- **Key pattern**: defensive merge `merged = {**original, **revised}` so LLM-omitted
  fields fall back to original. Server-controlled fields always preserved verbatim.
  Reading time recomputed post-merge from actual body length.

### archive.py
- Filesystem as database — no SQLite; JSON sidecar per generation is source of truth
- `archive_stats()`, `list_archive()`, `get_entry()`, `delete_entry()`
- `delete_entry()` — basename sanitised (`[A-Za-z0-9_\-]+` only), removes all 7 format files
- Quality scores, brand_review, quality_revision persisted into sidecar post-export

### answer_machine/ (directory)
- `kb.py`: `KnowledgeBase` dataclass; parses brand_narrative.docx, faqs.docx, meta_comments.xlsx
  into a cached JSON snapshot. Rebuilds automatically when source mtime changes.
- `agent.py`: `draft_reply()` — assembles prompt with static system block + KB cache block
  (`cache_control: ephemeral`). ~1797 tokens cached.
- Public: `load_knowledge_base()`, `draft_reply()`, `append_exemplar()`, `delete_exemplar()`

---

## Compliance Page — Document Advisor

Sections (top to bottom):
1. **Stats row** — Hard Rules count, Principles count, Articles Pass, Articles Flagged
2. **Document Compliance Advisor** — dropbox + check button (no content-type selector)
3. **Marketing Compliance Rulebook** — accordion (collapsed by default)
4. **Recent Compliance Runs / Flagged Articles** — collapsed accordion

### Document Advisor background job pattern
- Job state: `_cp_jobs` dict + `_cp_jobs_lock` (separate from Create page `_jobs`)
- Stages: `parse → hard_rules → llm → done` (`_CP_STAGES`)
- Components: `dcc.Store(id="cp-doc-job-id")`, `dcc.Store(id="cp-doc-result-store")`,
  `dcc.Interval(id="cp-doc-poll", interval=800)`
- On completion: result stored in `cp-doc-result-store` for download callback
- Download: generates `.docx` report via `_build_compliance_report_docx()`,
  saved to `output/`, served via `/downloads/` Flask route

---

## API / AI Patterns
- Model: `claude-sonnet-4-20250514` (default, via `ANTHROPIC_MODEL` env var)
- Compliance model: `cfg.compliance_model` (falls back to `cfg.anthropic_model`)
- Client: `build_anthropic_client(cfg)` from `src/config.py`
- All LLM responses constrained to JSON; markdown fences stripped before parsing (`src/_json.py`)
- Prompt caching: `cache_control: ephemeral` on large static system blocks
- Consistency: `temperature=0` on all advisor/grader calls

---

## Database (SQLite)
- Path: `./data/articles.db` (configurable via `DB_PATH` env var)
- `INSERT OR IGNORE` keyed on `url` — re-running scrape never duplicates
- Key fields: id, title, url, source, published_at, created_at, category,
  relevance_score, scrape_frequency, summary (JSON blob)
- Compliance log: `compliance_log` table (init via `init_compliance_tables()`)

---

## Output / Archive
- Output dir: `./output/` (configurable via `OUTPUT_DIR`)
- 7 formats per generation: HTML, PDF, DOCX, MD, EML, TXT, JSON sidecar
- JSON sidecar keys: `kind`, `generated_at_utc`, `result`, `compliance_summary`,
  `quality`, `quality_revision`, `verification_summary`, `brand_review`
- File download served via Flask route `/downloads/<filename>` (path traversal blocked)
- Compliance reports also saved to `output/` as `compliance-report-{stem}-{timestamp}.docx`

---

## Scraping
- Sources: RSS feeds + GOV.UK API orgs + HTTP sources (configured in `.env`)
- No scheduler — manual runs only
- Pipeline: fetch → URL dedup → keyword pre-filter → LLM summarise →
  relevance score gate → compliance scan → store

---

## Dependencies
```
anthropic>=0.40.0, feedparser, requests, beautifulsoup4, notion-client,
python-dotenv, click, dash>=2.14.0, dash-bootstrap-components (FLATLY theme),
plotly, pandas, python-docx, weasyprint (needs brew install pango), textstat,
openpyxl
```

---

## UI / Styling
- Theme: `dbc.themes.FLATLY`
- Custom CSS in `assets/`
- Key CSS classes: `warren-sidebar`, `warren-stat-card`, `create-shell`,
  `create-pane-left`, `create-pane-right`, `generate-bar`, `type-tile`,
  `type-tile selected`, `section-eyebrow`, `selection-chip`

---

## What Was Recently Completed
1. ✅ Blog quality revision (`blog_quality_revision.py`) with defensive merge fix
2. ✅ Source verifier — paywall taxonomy, PAYWALLED_DOMAINS allowlist, broken/blocked accordion
3. ✅ Archive page (`/archive`) — stats, filters, format pills, preview, delete
4. ✅ Brand review stage (`src/brand_review.py`) — severity-graded voice audit in Create pipeline;
   `_brand_review_card()` in Create output; `brand_review` block in JSON sidecar
5. ✅ Compliance page — Document Compliance Advisor: dropbox, background job + progress bar,
   per-element advisory cards, `.docx` report download
6. ✅ Advisor consistency fixes — `temperature=0`, named-principle checklist (P1–P8),
   extended deterministic hard rules (advice-perimeter, performance promises, UK English)

---

## NEXT TASK — Phase 1: SEO Audit Lite

**Goal**: Keyword + content-gap audit that runs *before* generation and
pre-fills the editor's-angle textbox with suggested focus areas.

**What it does**:
- Analyses the selected articles for keyword clusters and content gaps
- Suggests a search-optimised angle / focus that the editor can accept,
  tweak or discard before hitting Generate
- Surfaces a target H1 and 3–5 suggested SEO tags

**Phase 1 Remaining (after SEO audit)**
- **New content type templates** — Press release, Case study, Landing page

---

## Phase 2 (future pages)
- `/campaigns` — campaign planner
- `/sequences` — email sequence generator

## Phase 3
- `/intel` — competitive-brief (needs WebSearch)
- `/performance` — requires analytics connector
