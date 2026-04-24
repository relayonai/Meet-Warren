from __future__ import annotations

import json
import logging
import sys

import click

from src.config import build_anthropic_client, load_config
from src.database import (
    existing_urls,
    get_connection,
    get_source_log,
    init_db,
    insert_article,
    is_source_due,
    mark_source_scraped,
    query_articles,
)
from src.deduplicator import filter_new
from src.formatter import to_html, to_text
from src.generator import generate_newsletter
from src.publisher import publish
from src.scraper import (
    RSS_SOURCE_OVERRIDES,
    fetch_govuk_api,
    fetch_http,
    fetch_rss,
    title_passes_prefilter,
)
from src.summariser import summarise
from src.compliance import scan_article
from src.compliance.pipeline import init_compliance_tables

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@click.group()
def cli() -> None:
    """UK Personal Finance News Aggregator."""


@cli.command()
@click.option("--force", is_flag=True, default=False,
              help="Ignore schedule and scrape all sources regardless of last run.")
@click.option("--sources", default=None,
              help="Comma-separated source keys (URLs or GOV.UK slugs) to scrape on demand. "
                   "Implies --force for the selected sources only.")
def scrape(force: bool, sources: str | None) -> None:
    """Fetch, deduplicate, pre-filter, summarise, and store new articles."""
    cfg = load_config()
    conn = get_connection(cfg.db_path)
    init_db(conn)
    init_compliance_tables(conn)

    selected = None
    if sources:
        selected = {s.strip() for s in sources.split(",") if s.strip()}
        click.echo(f"On-demand mode: scraping {len(selected)} selected source(s), ignoring schedule.")

    def _frequency_for(source_name: str) -> str:
        sl = source_name.lower()
        for name, freq in cfg.source_schedules.items():
            if name.lower() in sl or sl in name.lower():
                return freq
        return "daily"

    def _source_name_for_rss(url: str) -> str:
        return RSS_SOURCE_OVERRIDES.get(url, url)

    # -----------------------------------------------------------------------
    # Collect articles source by source, respecting schedule
    # -----------------------------------------------------------------------
    raw = []
    skipped_sources = []

    # RSS feeds
    for url in cfg.rss_feeds:
        name = _source_name_for_rss(url)
        freq = _frequency_for(name)
        if selected is not None and url not in selected:
            continue
        if selected is None and not force and not is_source_due(conn, url, freq):
            skipped_sources.append((name, freq))
            click.echo(f"  ⏭  {name} ({freq}) — not due yet, skipping")
            continue
        click.echo(f"  ⬇  {name} ({freq})")
        articles = fetch_rss([url])
        raw.extend(articles)
        mark_source_scraped(conn, url, name, freq)
        click.echo(f"      fetched {len(articles)}")

    # GOV.UK API orgs
    GOVUK_NAMES = {
        "office-for-national-statistics": "Office For National Statistics",
        "hm-revenue-customs":             "Hm Revenue Customs",
    }
    for slug in cfg.govuk_orgs:
        name = GOVUK_NAMES.get(slug, slug.replace("-", " ").title())
        freq = _frequency_for(name)
        if selected is not None and slug not in selected:
            continue
        if selected is None and not force and not is_source_due(conn, slug, freq):
            skipped_sources.append((name, freq))
            click.echo(f"  ⏭  {name} ({freq}) — not due yet, skipping")
            continue
        click.echo(f"  ⬇  {name} ({freq})")
        articles = fetch_govuk_api([slug])
        raw.extend(articles)
        mark_source_scraped(conn, slug, name, freq)
        click.echo(f"      fetched {len(articles)}")

    # Generic HTTP sources
    for url in cfg.http_sources:
        freq = _frequency_for(url)
        if selected is not None and url not in selected:
            continue
        if selected is None and not force and not is_source_due(conn, url, freq):
            click.echo(f"  ⏭  {url} ({freq}) — not due yet, skipping")
            continue
        click.echo(f"  ⬇  {url} ({freq})")
        articles = fetch_http([url])
        raw.extend(articles)
        mark_source_scraped(conn, url, url, freq)
        click.echo(f"      fetched {len(articles)}")

    if not raw and skipped_sources:
        click.echo(f"\nAll sources are up to date. Run with --force to override.")
        return

    click.echo(f"\nFetched {len(raw)} articles total.")

    # -----------------------------------------------------------------------
    # Dedup → pre-filter → summarise → store
    # -----------------------------------------------------------------------
    new = filter_new(raw, existing_urls(conn))
    click.echo(f"{len(new)} new after URL dedup.")

    prefiltered = [a for a in new if title_passes_prefilter(a.title)]
    dropped_kw  = len(new) - len(prefiltered)
    if dropped_kw:
        click.echo(f"{dropped_kw} dropped by keyword pre-filter, {len(prefiltered)} remain.")

    if not prefiltered:
        click.echo("Nothing to summarise. Done.")
        return

    client = build_anthropic_client(cfg)
    inserted = skipped_relevance = compliance_warned = 0

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

        # Compliance scan (lightweight, hard rules only — never blocks storage)
        scan_text = (summary.get("summary") or "") + "\n" + art.title
        scan = scan_article(scan_text, article_id=art.id, title=art.title, conn=conn)
        summary["compliance_grade"] = scan["grade"]
        summary["compliance_notes"] = scan["notes"]
        if scan["grade"] != "pass":
            compliance_warned += 1
            click.echo(f"     ⚠ compliance:{scan['grade']} ({scan['failed']} flag(s))")

        freq = _frequency_for(art.source)
        if insert_article(conn, art, summary, frequency=freq):
            inserted += 1
            click.echo(f"     stored  (score {score}, cat={summary.get('category')}, freq={freq})")

    click.echo(
        f"\nDone. fetched={len(raw)} deduped={len(new)} "
        f"prefiltered={dropped_kw} low-score={skipped_relevance} "
        f"compliance-flagged={compliance_warned} inserted={inserted}."
    )
    if skipped_sources:
        click.echo(f"Skipped {len(skipped_sources)} source(s) not yet due: "
                   f"{', '.join(n for n, _ in skipped_sources)}")


@cli.command()
def schedule() -> None:
    """Show the scrape schedule — which sources are due and which are waiting."""
    cfg = load_config()
    conn = get_connection(cfg.db_path)
    init_db(conn)

    from src.scraper import RSS_SOURCE_OVERRIDES
    from src.database import FREQ_DAYS
    from datetime import datetime, timezone

    GOVUK_NAMES = {
        "office-for-national-statistics": "Office For National Statistics",
        "hm-revenue-customs":             "Hm Revenue Customs",
    }

    def _frequency_for(name: str) -> str:
        sl = name.lower()
        for k, v in cfg.source_schedules.items():
            if k.lower() in sl or sl in k.lower():
                return v
        return "daily"

    all_sources = (
        [(url, RSS_SOURCE_OVERRIDES.get(url, url)) for url in cfg.rss_feeds] +
        [(slug, GOVUK_NAMES.get(slug, slug)) for slug in cfg.govuk_orgs]
    )

    log_rows = {r["source_key"]: r for r in get_source_log(conn)}

    click.echo(f"\n{'Source':<40} {'Freq':<8} {'Last Scraped':<22} {'Status'}")
    click.echo("-" * 85)
    for key, name in all_sources:
        freq    = _frequency_for(name)
        row     = log_rows.get(key)
        last    = row["last_scraped_at"] if row else None
        due     = is_source_due(conn, key, freq)
        status  = click.style("● DUE",  fg="green") if due else click.style("○ waiting", fg="yellow")
        last_str = last[:19].replace("T", " ") if last else "never"
        click.echo(f"{name:<40} {freq:<8} {last_str:<22} {status}")
    click.echo()


@cli.command()
@click.option("--limit",    type=int,  default=20, help="Max articles to include.")
@click.option("--category", default=None,          help="Filter by category.")
@click.option("--since",    default=None,          help="ISO date lower bound (YYYY-MM-DD).")
def generate(limit: int, category: str | None, since: str | None) -> None:
    """Generate and publish a newsletter from stored articles."""
    cfg = load_config()
    conn = get_connection(cfg.db_path)
    init_db(conn)

    rows = query_articles(conn, limit=limit, category=category, since=since)
    if not rows:
        click.echo("No matching articles. Run `scrape` first.")
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


@cli.command()
@click.argument("file_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--kind", type=click.Choice(["newsletter", "blog", "article"]),
              default="newsletter", help="Content kind for grading rules.")
@click.option("--enforce/--no-enforce", default=False,
              help="Run the auto-revision loop and write a -compliant.html alongside.")
def compliance(file_path: str, kind: str, enforce: bool) -> None:
    """Grade an HTML/text file against the compliance rulebook."""
    from src.compliance import grade_content, analyze_findings, ensure_compliant
    cfg = load_config()
    content = open(file_path, "r", encoding="utf-8").read()
    client = build_anthropic_client(cfg)

    if enforce:
        result = ensure_compliant(content, kind=kind, client=client,
                                  model=cfg.anthropic_model, max_iterations=2)
        out_path = file_path.rsplit(".", 1)[0] + "-compliant.html"
        open(out_path, "w", encoding="utf-8").write(result["final_content"])
        s = result["final_grade"]["summary"]
        click.echo(f"Iterations: {result['iterations']} · Revised: {result['revised']}")
        click.echo(f"Final grade: {s['grade']} ({s['passed']}/{s['total']} passed)")
        click.echo(f"Wrote: {out_path}")
        return

    grading = grade_content(content, kind=kind, client=client, model=cfg.anthropic_model)
    s = grading["summary"]
    click.echo(f"Grade: {s['grade']} · {s['passed']}/{s['total']} passed "
               f"({int(s['pass_rate']*100)}%)")
    click.echo("\nFailures:")
    for e in grading["expectations"]:
        if not e["passed"]:
            click.echo(f"  [{e['severity']}] §{e['section']}: {e['text']}")
            click.echo(f"      {e['evidence'][:160]}")

    analysis = analyze_findings(grading)
    click.echo(f"\n{len(analysis['improvement_suggestions'])} suggestion(s):")
    for sg in analysis["improvement_suggestions"][:10]:
        click.echo(f"  [{sg['priority']}] {sg['suggestion'][:140]}")


if __name__ == "__main__":
    cli()
