"""Export the Neo4j mention layer to RDF and validate it against SHACL shapes."""

import json
from collections import Counter
from pathlib import Path
from typing import Optional

from rdflib import RDF, Graph, Literal, Namespace
from rdflib.namespace import XSD

KG = Namespace("http://kg.local/sec#")
NODE = Namespace("http://kg.local/sec/node/")

EDGE_TO_PROPERTY = {
    "PARENT_OF": KG.directParentOf,
    "IDENTIFIED_BY": KG.identifiedBy,
    "REPORTS": KG.reports,
    "ACQUIRED": KG.acquired,
    "OFFICER_OF": KG.officerOf,
    "COMPETES_WITH": KG.competesWith,
    "FILED": KG.filed,
    "RESOLVES_TO": KG.resolvesTo,
}

ATTR_TO_PROPERTY = {
    "scheme": (KG.identifierScheme, None),
    "val": (KG.factValue, XSD.decimal),
    "fy": (KG.fiscalYear, XSD.integer),
    "unit": (KG.unit, None),
    "jurisdiction_text": (KG.jurisdictionText, None),
}


def _add_mention(graph: Graph, node) -> None:
    """Add one mention's triples, from a Neo4j node or a Parquet row alike."""
    subject = NODE[node["mention_id"]]
    graph.add((subject, RDF.type, KG[node["mention_type"]]))

    if node.get("name"):
        prop = KG.identifierValue if node["mention_type"] == "Identifier" else KG.legalName
        graph.add((subject, prop, Literal(node["name"])))

    for key, prop in (
        ("source_doc", KG.sourceDoc),
        ("extractor", KG.extractor),
        ("extractor_version", KG.extractorVersion),
        ("modality", KG.modality),
    ):
        if node.get(key) is not None:
            graph.add((subject, prop, Literal(node[key])))

    if node.get("confidence") is not None:
        graph.add(
            (subject, KG.confidence, Literal(str(node["confidence"]), datatype=XSD.decimal))
        )

    attrs = json.loads(node.get("attrs") or "{}")
    for key, (prop, dtype) in ATTR_TO_PROPERTY.items():
        if attrs.get(key) is not None:
            value = (
                Literal(str(attrs[key]), datatype=dtype)
                if dtype
                else Literal(str(attrs[key]))
            )
            graph.add((subject, prop, value))


def _add_edge(graph: Graph, src: str, edge_type: str, dst: str, exported: set) -> None:
    """Add one edge, unless it is untyped in the ontology or dangles outside the sample."""
    prop = EDGE_TO_PROPERTY.get(edge_type)
    if prop is None or src not in exported or dst not in exported:
        return
    graph.add((NODE[src], prop, NODE[dst]))


def export_rdf(driver, limit: Optional[int] = None) -> Graph:
    """Materialise the :Mention layer as RDF typed by the ontology."""
    graph = Graph()
    graph.bind("kg", KG)

    node_query = "MATCH (m:Mention) RETURN m"
    if limit:
        node_query += f" LIMIT {int(limit)}"

    exported = set()
    with driver.session() as session:
        for record in session.run(node_query):
            node = record["m"]
            exported.add(node["mention_id"])
            _add_mention(graph, node)

        edge_query = "MATCH (a:Mention)-[r]->(b:Mention) RETURN a.mention_id AS s, type(r) AS t, b.mention_id AS o"
        for record in session.run(edge_query):
            _add_edge(graph, record["s"], record["t"], record["o"], exported)

    return graph


def _read_rows(paths) -> list:
    """Parquet to dicts, with NaN flattened to None so .get() behaves like Neo4j's."""
    import pandas as pd

    if isinstance(paths, (str, Path)):
        paths = [paths]
    rows = []
    for path in paths:
        if not Path(path).exists():
            continue
        df = pd.read_parquet(path)
        rows += df.astype(object).where(pd.notna(df), None).to_dict("records")
    return rows


def export_rdf_from_parquet(
    mentions_path, edges_path, limit: Optional[int] = None
) -> Graph:
    """Build the same RDF graph from the staged Parquet, with no database."""
    graph = Graph()
    graph.bind("kg", KG)

    rows = _read_rows(mentions_path)
    if limit:
        rows = rows[:limit]

    exported = set()
    for row in rows:
        exported.add(row["mention_id"])
        _add_mention(graph, row)

    for row in _read_rows(edges_path):
        _add_edge(
            graph, row["src_mention_id"], row["edge_type"], row["dst_mention_id"], exported
        )

    return graph


def _walks_returning_to_start(adjacency: dict, start: str, max_depth: int) -> int:
    """Count walks of length 1..max_depth that return to start."""
    found = 0
    stack = [(start, 0)]
    while stack:
        node, depth = stack.pop()
        if depth >= max_depth:
            continue
        for nxt in adjacency.get(node, ()):
            if nxt == start:
                found += 1
            stack.append((nxt, depth + 1))
    return found


def parquet_only_checks(mentions_path, edges_path, max_depth: int = 6) -> dict:
    """The cypher_only_checks in pure Python, with the same output keys."""
    mentions = _read_rows(mentions_path)
    edges = _read_rows(edges_path)

    adjacency: dict = {}
    linked = set()
    for edge in edges:
        src, dst = edge["src_mention_id"], edge["dst_mention_id"]
        linked.add(src)
        linked.add(dst)
        if edge["edge_type"] == "PARENT_OF":
            adjacency.setdefault(src, []).append(dst)

    cycles = sum(
        _walks_returning_to_start(adjacency, node, max_depth) for node in adjacency
    )
    total = len(mentions)
    orphans = sum(1 for m in mentions if m["mention_id"] not in linked)
    return {
        "ownership_cycles": cycles,
        "orphan_nodes": orphans,
        "total_nodes": total,
        "orphan_rate": round(orphans / total, 4) if total else 0.0,
    }


def validate(data_graph: Graph, shapes_path: Path, ontology_path: Optional[Path] = None):
    """Run pyshacl. Returns (conforms, results_graph, report_text)."""
    from pyshacl import validate as shacl_validate

    shapes = Graph()
    shapes.parse(shapes_path, format="turtle")
    ontology = None
    if ontology_path:
        ontology = Graph()
        ontology.parse(ontology_path, format="turtle")

    return shacl_validate(
        data_graph,
        shacl_graph=shapes,
        ont_graph=ontology,
        inference="none",
        advanced=True,
        abort_on_first=False,
        meta_shacl=False,
    )


def summarise(results_graph: Graph) -> Counter:
    """Count violations by their sh:message."""
    SH = Namespace("http://www.w3.org/ns/shacl#")
    counts: Counter = Counter()
    for result in results_graph.subjects(RDF.type, SH.ValidationResult):
        message = next(results_graph.objects(result, SH.resultMessage), None)
        counts[str(message) if message else "unspecified"] += 1
    return counts


def cypher_only_checks(driver) -> dict:
    """Constraints SHACL cannot express: transitive cycles and orphan rate."""
    with driver.session() as session:
        cycles = session.run(
            "MATCH path=(m:Mention)-[:PARENT_OF*1..6]->(m) RETURN count(path) AS c"
        ).single()["c"]
        orphans = session.run(
            "MATCH (m:Mention) WHERE NOT (m)--() RETURN count(m) AS c"
        ).single()["c"]
        total = session.run("MATCH (m:Mention) RETURN count(m) AS c").single()["c"]
    return {
        "ownership_cycles": cycles,
        "orphan_nodes": orphans,
        "total_nodes": total,
        "orphan_rate": round(orphans / total, 4) if total else 0.0,
    }
