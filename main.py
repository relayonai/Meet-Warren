from __future__ import annotations

import json
import logging
import sys

import click

from src.config import build_anthropic_client, load_config
from src.database import (
    existing_urls,
    get_connection,
    init_db,
    insert_article,
    query_articles,
)
from src.deduplicator import filter_new
from src.formatter import to_html, to_text
from src.generator import generate_newsletter
from src.publisher import publish
from src.scraper import (
    fetch_govuk_api,
    fetch_http,
    fetch_rss,
    title_passes_prefilter,
)
from src.summariser import summarise

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@click.group()
def cli() -> None:
    """UK Personal Finance News Aggregator."""


@cli.command()
def scrape() -> None:
    """Fetch, deduplicate, pre-filter, summarise, and store new articles."""
    cfg = load_config()
    conn = get_connection(cfg.db_path)
    init_db(conn)

    click.echo("Fetching from all sources...")
    raw = (
        fetch_rss(cfg.rss_feeds)
        + fetch_govuk_api(cfg.govuk_orgs)
        + fetch_http(cfg.http_sources)
    )
    click.echo(f"Fetched {len(raw)} candidate articles.")

    # Dedup against DB
    new = filter_new(raw, existing_urls(conn))
    click.echo(f"{len(new)} new after URL dedup.")

    # Pre-filter by title keywords (cheap, no API call)
    prefiltered = [a for a in new if title_passes_prefilter(a.title)]
    skipped_prefilter = len(new) - len(prefiltered)
    if skipped_prefilter:
        click.echo(f"{skipped_prefilter} dropped by keyword pre-filter, {len(prefiltered)} remain.")

    if not prefiltered:
        click.echo("Nothing to summarise. Done.")
        return

    client = build_anthropic_client(cfg)
    inserted = skipped_relevance = 0

    # Build source → frequency lookup (match by substring of source name)
    def _frequency_for(source: str) -> str:
        sl = source.lower()
        for name, freq in cfg.source_schedules.items():
            if name.lower() in sl or sl in name.lower():
                return freq
        return "daily"

    for art in prefiltered:
        click.echo(f"  -> {art.source}: {art.title[:70]}")
        summary = summarise(art, client, cfg.anthropic_model)
        if summary is None:
            continue

        score = int(summary.get("relevance_score") or 0)
        if score < cfg.min_relevance_score:
            click.echo(f"     skipped (score {score} < {cfg.min_relevance_score})")
            skipped_relevance += 1
            continue

        freq = _frequency_for(art.source)
        if insert_article(conn, art, summary, frequency=freq):
            inserted += 1
            click.echo(f"     stored  (score {score}, category={summary.get('category')}, freq={freq})")

    click.echo(
        f"Done. fetched={len(raw)} deduped={len(new)} "
        f"prefiltered={skipped_prefilter} low-score={skipped_relevance} inserted={inserted}."
    )


@cli.command()
@click.option("--limit", type=int, default=20, help="Max articles to include.")
@click.option("--category", default=None, help="Filter by category (e.g. mortgages).")
@click.option("--since", default=None, help="ISO date (YYYY-MM-DD) lower bound.")
def generate(limit: int, category: str | None, since: str | None) -> None:
    """Generate and publish a newsletter from stored articles."""
    cfg = load_config()
    conn = get_connection(cfg.db_path)
    init_db(conn)

    rows = query_articles(conn, limit=limit, category=category, since=since)
    if not rows:
        click.echo("No matching articles in the database. Run `scrape` first.")
        sys.exit(1)

    summaries = []
    for row in rows:
        try:
            data = json.loads(row["summary"]) if row["summary"] else {}
        except json.JSONDecodeError:
            data = {}
        data["url"] = row["url"]
        data.setdefault("title", row["title"])
        summaries.append(data)

    click.echo(f"Generating newsletter from {len(summaries)} article(s)...")
    client = build_anthropic_client(cfg)
    newsletter = generate_newsletter(summaries, client, cfg.anthropic_model)
    if not newsletter:
        click.echo("Newsletter generation failed.", err=True)
        sys.exit(2)

    html = to_html(newsletter)
    text = to_text(newsletter)
    subject = newsletter.get("subject_line", "UK Personal Finance Digest")

    for line in publish(html, text, subject, cfg):
        click.echo(line)


if __name__ == "__main__":
    cli()
