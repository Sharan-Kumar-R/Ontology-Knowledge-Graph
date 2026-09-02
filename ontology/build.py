"""Build ontology artifacts from ontology.ttl: RDF/XML for Protege, Cypher for Neo4j."""

import argparse
from pathlib import Path

from rdflib import OWL, RDF, Graph, URIRef

HERE = Path(__file__).parent
KG = "http://kg.local/sec#"

HEADER = """// GENERATED from ontology.ttl by ontology/build.py -- do not edit by hand.
//
// Neo4j Community Edition supports uniqueness constraints and indexes only.
// Property existence, disjointness, cardinality and transitivity are expressed
// in ontology.ttl and shapes.ttl, and checked in kg.evaluate.
"""


def to_rdfxml(ttl: Path, out: Path) -> Path:
    graph = Graph()
    graph.parse(ttl, format="turtle")
    graph.serialize(destination=out, format="xml")
    return out


def _local(uri: URIRef) -> str:
    return str(uri).split("#")[-1]


def to_cypher(ttl: Path, out: Path) -> Path:
    graph = Graph()
    graph.parse(ttl, format="turtle")

    lines = [HEADER]
    lines.append("CREATE CONSTRAINT mention_id_unique IF NOT EXISTS")
    lines.append("FOR (m:Mention) REQUIRE m.mention_id IS UNIQUE;\n")

    for prop in sorted(graph.subjects(RDF.type, OWL.InverseFunctionalProperty)):
        name = _local(prop)
        lines.append(f"// {name} is inverse-functional in the ontology:")
        lines.append("// one identifier value denotes at most one entity.")
        lines.append("CREATE CONSTRAINT identifier_value_unique IF NOT EXISTS")
        lines.append(
            "FOR (m:Mention) REQUIRE (m.mention_type, m.name) IS NODE KEY;\n"
        )
        break

    classes = sorted(_local(c) for c in graph.subjects(RDF.type, OWL.Class))
    lines.append(f"// Indexes for the {len(classes)} declared classes")
    lines.append("CREATE INDEX mention_type_idx IF NOT EXISTS")
    lines.append("FOR (m:Mention) ON (m.mention_type);\n")
    lines.append("CREATE INDEX mention_name_idx IF NOT EXISTS")
    lines.append("FOR (m:Mention) ON (m.name);\n")
    lines.append("CREATE INDEX mention_modality_idx IF NOT EXISTS")
    lines.append("FOR (m:Mention) ON (m.modality);\n")
    lines.append("CREATE INDEX mention_source_idx IF NOT EXISTS")
    lines.append("FOR (m:Mention) ON (m.source_doc);\n")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def summary(ttl: Path) -> dict:
    graph = Graph()
    graph.parse(ttl, format="turtle")
    return {
        "classes": sorted(_local(c) for c in graph.subjects(RDF.type, OWL.Class)),
        "object_properties": sorted(
            _local(p) for p in graph.subjects(RDF.type, OWL.ObjectProperty)
        ),
        "data_properties": sorted(
            _local(p) for p in graph.subjects(RDF.type, OWL.DatatypeProperty)
        ),
        "transitive": sorted(
            _local(p) for p in graph.subjects(RDF.type, OWL.TransitiveProperty)
        ),
        "functional": sorted(
            _local(p) for p in graph.subjects(RDF.type, OWL.FunctionalProperty)
        ),
        "inverse_functional": sorted(
            _local(p) for p in graph.subjects(RDF.type, OWL.InverseFunctionalProperty)
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ttl", type=Path, default=HERE / "ontology.ttl")
    args = parser.parse_args()

    owl_path = to_rdfxml(args.ttl, HERE / "ontology.owl")
    cypher_path = to_cypher(args.ttl, HERE / "constraints.cypher")
    info = summary(args.ttl)

    print(f"wrote {owl_path}")
    print(f"wrote {cypher_path}")
    for key, values in info.items():
        print(f"{key:<20} {len(values):>2}  {', '.join(values)}")


if __name__ == "__main__":
    main()
