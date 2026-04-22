# UK Personal Finance News Aggregator & Newsletter Generator

A Python CLI with two independent pipelines that share a local SQLite database:

1. **`scrape`** — pulls UK personal-finance articles from RSS + HTTP sources, dedupes by URL, summarises each new article via the Anthropic API, and stores structured records.
2. **`generate`** — queries stored summaries, asks Claude to compose a structured newsletter, renders HTML + plain text, and publishes to local files / Gmail / Notion.

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# then edit .env and add your ANTHROPIC_API_KEY (and any optional outputs)
```

## Environment variables

| Variable                 | Purpose                                                      | Required |
| ------------------------ | ------------------------------------------------------------ | -------- |
| `ANTHROPIC_API_KEY`      | Anthropic API key                                            | yes      |
| `ANTHROPIC_MODEL`        | Model id (default `claude-sonnet-4-20250514`)                | no       |
| `RSS_FEEDS`              | Comma-separated RSS feed URLs                                | yes\*    |
| `HTTP_SOURCES`           | Comma-separated listing pages to scrape                      | yes\*    |
| `DB_PATH`                | SQLite path (default `./data/articles.db`)                   | no       |
| `OUTPUT_DIR`             | Where local newsletter files are written (`./output`)        | no       |
| `ENABLE_LOCAL`           | Save HTML/text files locally (default `true`)                | no       |
| `ENABLE_GMAIL`           | Send the newsletter via Gmail SMTP (default `false`)         | no       |
| `ENABLE_NOTION`          | Save the newsletter as a Notion page (default `false`)       | no       |
| `GMAIL_USER`             | Gmail address sending the newsletter                         | if Gmail |
| `GMAIL_APP_PASSWORD`     | Gmail [App Password](https://myaccount.google.com/apppasswords) | if Gmail |
| `GMAIL_TO`               | Recipient address                                            | if Gmail |
| `NOTION_TOKEN`           | Notion integration token                                     | if Notion|
| `NOTION_DATABASE_ID`     | Target Notion database id                                    | if Notion|

\* At least one of `RSS_FEEDS` or `HTTP_SOURCES` must be set.

## Commands

```bash
# Daily ingestion (idempotent — safe to re-run)
python main.py scrape

# Generate the newsletter from stored articles
python main.py generate
python main.py generate --limit 10
python main.py generate --category mortgages --since 2026-04-01
```

### Cron example

Run the scraper every morning at 07:00:

```cron
0 7 * * * cd /path/to/Warren\ Workflow && /path/to/venv/bin/python main.py scrape >> scrape.log 2>&1
```

## Output channels

- **Local** (`ENABLE_LOCAL=true`): writes timestamped `newsletter-YYYYMMDD-HHMMSS.html` and `.txt` into `OUTPUT_DIR`.
- **Gmail** (`ENABLE_GMAIL=true`): sends a multipart (text + HTML) email via `smtp.gmail.com:465`. Use a Google App Password — your account password will not work.
- **Notion** (`ENABLE_NOTION=true`): creates a new page in the configured database. The integration must be added as a connection on that database in Notion.

## Project layout

```
main.py                 # Click CLI entrypoint
src/
  config.py             # .env loading
  database.py           # SQLite schema + queries
  scraper.py            # RSS + HTTP fetchers
  deduplicator.py       # URL-based dedup
  summariser.py         # Anthropic per-article summarisation
  generator.py          # Anthropic newsletter composition
  formatter.py          # HTML + plaintext rendering
  publisher.py          # local / Gmail / Notion outputs
  _json.py              # tolerant JSON parsing for LLM responses
.env.example
requirements.txt
```

## Notes

- The database uses `INSERT OR IGNORE` keyed on `url`, so re-running `scrape` never duplicates articles.
- All Anthropic responses are constrained to JSON; markdown fences are stripped before parsing.
- Network and API errors during scrape/summarisation are logged and skipped — one bad source never kills the run.
