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
}

ATTR_TO_PROPERTY = {
    "scheme": (KG.identifierScheme, None),
    "val": (KG.factValue, XSD.decimal),
    "fy": (KG.fiscalYear, XSD.integer),
    "unit": (KG.unit, None),
    "jurisdiction_text": (KG.jurisdictionText, None),
}


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
            subject = NODE[node["mention_id"]]
            exported.add(node["mention_id"])
            graph.add((subject, RDF.type, KG[node["mention_type"]]))

            if node.get("name"):
                prop = (
                    KG.identifierValue
                    if node["mention_type"] == "Identifier"
                    else KG.legalName
                )
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
                    (
                        subject,
                        KG.confidence,
                        Literal(str(node["confidence"]), datatype=XSD.decimal),
                    )
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

        edge_query = "MATCH (a:Mention)-[r]->(b:Mention) RETURN a.mention_id AS s, type(r) AS t, b.mention_id AS o"
        for record in session.run(edge_query):
            prop = EDGE_TO_PROPERTY.get(record["t"])
            if prop is None or record["s"] not in exported or record["o"] not in exported:
                continue
            graph.add((NODE[record["s"]], prop, NODE[record["o"]]))

    return graph


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
