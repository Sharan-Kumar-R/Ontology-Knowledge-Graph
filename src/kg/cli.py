import json
from pathlib import Path

import typer

from kg.config import load_settings
from kg.ingest.cache import RawCache
from kg.ingest.edgar import (
    fetch_company_tickers,
    fetch_companyfacts,
    fetch_filing_headers,
    fetch_submissions,
    filing_doc_url,
    find_exhibit21_from_headers,
    recent_filings,
)
from kg.ingest.http import SecClient
from kg.load.neo4j_conn import check_connection, get_driver
from kg.load.neo4j_writer import apply_constraints, clear_graph, load_edges, load_mentions
from kg.parse.schema import write_edges, write_mentions
from kg.parse.semi import parse_companyfacts, parse_exhibit21
from kg.parse.structured import parse_company_tickers

app = typer.Typer(help="Enterprise KG construction pipeline")

CONSTRAINTS = Path("ontology/constraints.cypher")


def _client(settings):
    return SecClient(settings, RawCache(settings.raw_dir))


@app.command()
def check():
    """Verify config, data root, and Neo4j connectivity."""
    settings = load_settings()
    typer.echo(f"data_root: {settings.data_root}")
    driver = get_driver(settings)
    try:
        typer.echo(json.dumps(check_connection(driver), indent=2))
    finally:
        driver.close()


@app.command(name="refresh-samples")
def ingest_sec(limit: int = typer.Option(25, help="number of companies to fetch")):
    """OPTIONAL. Re-download from SEC to refresh data/samples/.

    Needs a contact email in config/settings.yaml and network access. Normal
    use does not require this - the sample data ships with the repo. After
    running it, regenerate the folder with: python scripts/export_samples.py
    """
    settings = load_settings()
    client = _client(settings)
    doc_id, records = fetch_company_tickers(client)
    (settings.staging_dir / "tickers_doc_id.txt").write_text(doc_id)
    manifest = []
    for rec in records[:limit]:
        cik = rec["cik_str"]
        entry = {"cik": cik, "title": rec["title"], "facts_doc": None, "ex21": None}
        try:
            entry["facts_doc"], _ = fetch_companyfacts(client, cik)
        except Exception as exc:
            typer.echo(f"  {rec['title']}: companyfacts failed ({exc.__class__.__name__})")
        try:
            _, submissions = fetch_submissions(client, cik)
            filings = recent_filings(submissions, "10-K", 1)
            if filings:
                _, headers = fetch_filing_headers(client, cik, filings[0]["accession"])
                filename = find_exhibit21_from_headers(headers)
                if filename:
                    url = filing_doc_url(cik, filings[0]["accession"], filename)
                    ex21_doc, _ = client.get_bytes(url)
                    entry["ex21"] = {"doc_id": ex21_doc, "url": url}
        except Exception as exc:
            typer.echo(f"  {rec['title']}: filing lookup failed ({exc.__class__.__name__})")
        manifest.append(entry)
        typer.echo(f"{rec['title']}: ex21={'yes' if entry['ex21'] else 'no'}")
    (settings.staging_dir / "sec_manifest.json").write_text(json.dumps(manifest, indent=2))
    typer.echo(f"ingested {len(manifest)} companies")


@app.command()
def parse():
    """Read data/samples/ and write mentions.parquet and edge_mentions.parquet."""
    from kg.ingest.local import (
        doc_id_for,
        load_exhibit21,
        load_index,
        load_tickers,
        load_xbrl,
    )

    settings = load_settings()
    mentions, edges = [], []

    records = load_tickers()
    tickers_doc = doc_id_for("structured/company_tickers.json")
    m, e = parse_company_tickers(records, tickers_doc, "data/samples/structured/company_tickers.json")
    mentions += m
    edges += e
    typer.echo(f"structured/tickers:  {len(m):>6} mentions")

    index = load_index()
    facts_count = ex21_count = 0
    for entry in index:
        cik = entry["cik"]
        facts = load_xbrl(entry)
        if facts is not None:
            doc = doc_id_for(entry["xbrl"])
            m, e = parse_companyfacts(facts, doc, entry["xbrl"], cik)
            mentions += m
            edges += e
            facts_count += len(m)
        html = load_exhibit21(entry)
        if html is not None:
            doc = doc_id_for(entry["exhibit21"])
            m, e = parse_exhibit21(
                html, doc, entry["exhibit21_url"] or entry["exhibit21"], cik, entry["title"]
            )
            mentions += m
            edges += e
            ex21_count += len(e)

    typer.echo(f"semi/xbrl:           {facts_count:>6} mentions")
    typer.echo(f"semi/exhibit21:      {ex21_count:>6} ownership edges")

    m_path = write_mentions(mentions, settings.staging_dir / "mentions.parquet")
    e_path = write_edges(edges, settings.staging_dir / "edge_mentions.parquet")
    typer.echo(f"wrote {len(mentions)} mentions -> {m_path}")
    typer.echo(f"wrote {len(edges)} edges -> {e_path}")


@app.command()
def load(reset: bool = typer.Option(False, help="delete all nodes first")):
    """Apply constraints and load Parquet into Neo4j."""
    settings = load_settings()
    driver = get_driver(settings)
    try:
        if reset:
            clear_graph(driver)
        apply_constraints(driver, CONSTRAINTS)
        n = load_mentions(driver, settings.staging_dir / "mentions.parquet")
        m = load_edges(driver, settings.staging_dir / "edge_mentions.parquet")
        typer.echo(f"loaded {n} mentions, {m} edges")
    finally:
        driver.close()


@app.command()
def stats():
    """Print node and relationship counts by type."""
    settings = load_settings()
    driver = get_driver(settings)
    try:
        with driver.session() as session:
            rows = session.run(
                "MATCH (m:Mention) RETURN m.mention_type AS t, m.modality AS mo, "
                "count(*) AS c ORDER BY c DESC"
            ).data()
            rels = session.run(
                "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY c DESC"
            ).data()
        typer.echo("mentions:")
        for r in rows:
            typer.echo(f"  {r['t']:<16} {r['mo']:<14} {r['c']}")
        typer.echo("edges:")
        for r in rels:
            typer.echo(f"  {r['t']:<16} {r['c']}")
    finally:
        driver.close()


@app.command()
def validate(limit: int = typer.Option(3000, help="max nodes to export to RDF")):
    """Export the graph to RDF and validate it against the SHACL shapes."""
    from kg.evaluate.shacl_eval import (
        cypher_only_checks,
        export_rdf,
        summarise,
        validate as shacl_validate,
    )

    settings = load_settings()
    driver = get_driver(settings)
    try:
        graph = export_rdf(driver, limit=limit)
        typer.echo(f"exported {len(graph)} RDF triples")
        conforms, results, _ = shacl_validate(
            graph, Path("ontology/shapes.ttl"), Path("ontology/ontology.ttl")
        )
        counts = summarise(results)
        typer.echo(f"conforms: {conforms}")
        typer.echo(f"violations: {sum(counts.values())}")
        for message, n in counts.most_common(10):
            typer.echo(f"  {n:>6}  {message[:80]}")
        typer.echo("")
        for key, value in cypher_only_checks(driver).items():
            typer.echo(f"  {key:<18} {value}")
    finally:
        driver.close()


@app.command(name="build-ontology")
def build_ontology():
    """Regenerate ontology.owl and constraints.cypher from ontology.ttl."""
    import subprocess
    import sys

    subprocess.run([sys.executable, "ontology/build.py"], check=True)


@app.command(name="run-all")
def run_all():
    """Build the whole graph from data/samples/: parse, load, stats."""
    parse()
    load(reset=True)
    stats()


if __name__ == "__main__":
    app()
