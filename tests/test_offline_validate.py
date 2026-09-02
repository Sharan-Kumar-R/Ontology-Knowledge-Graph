from pathlib import Path

import pytest

from kg.evaluate.shacl_eval import (
    export_rdf_from_parquet,
    parquet_only_checks,
    summarise,
    validate as shacl_validate,
)
from kg.parse.schema import EdgeMention, Mention, make_mention_id, write_edges, write_mentions

DOC = "a" * 64


def mention(local_key, mention_type="LegalEntity", name="ACME INC.", **overrides):
    base = dict(
        mention_id=make_mention_id(DOC, "test", local_key),
        mention_type=mention_type,
        name=name,
        attrs={},
        source_doc=DOC,
        source_uri="https://example.com/x",
        char_offset=None,
        extractor="test",
        extractor_version="1",
        confidence=1.0,
        modality="structured",
    )
    base.update(overrides)
    return Mention(**base)


def edge(src, dst, edge_type="PARENT_OF"):
    return EdgeMention(
        edge_id=make_mention_id(DOC, "test", f"{src.mention_id}->{dst.mention_id}"),
        src_mention_id=src.mention_id,
        dst_mention_id=dst.mention_id,
        edge_type=edge_type,
        attrs={},
        source_doc=DOC,
        char_offset=None,
        extractor="test",
        extractor_version="1",
        confidence=1.0,
        modality="structured",
    )


@pytest.fixture
def staged(tmp_path):
    def build(mentions, edges):
        m = write_mentions(mentions, tmp_path / "mentions.parquet")
        e = write_edges(edges, tmp_path / "edges.parquet")
        return m, e

    return build


def test_export_from_parquet_types_and_names_every_mention(staged):
    parent, sub = mention("parent"), mention("sub", name="ACME SUBSIDIARY LLC")
    graph = export_rdf_from_parquet(*staged([parent, sub], [edge(parent, sub)]))

    subjects = {str(s) for s in graph.subjects()}
    assert len(subjects) == 2
    assert any("LegalEntity" in str(o) for o in graph.objects())
    assert any("directParentOf" in str(p) for p in graph.predicates())


def test_export_from_parquet_drops_edges_outside_the_sample(staged):
    """An edge whose endpoint was cut by --limit must not become a dangling triple."""
    parent, sub = mention("parent"), mention("sub", name="ACME SUBSIDIARY LLC")
    paths = staged([parent, sub], [edge(parent, sub)])
    graph = export_rdf_from_parquet(*paths, limit=1)

    assert not any("directParentOf" in str(p) for p in graph.predicates())


def test_offline_graph_conforms_to_the_shapes(staged):
    parent, sub = mention("parent"), mention("sub", name="ACME SUBSIDIARY LLC")
    graph = export_rdf_from_parquet(*staged([parent, sub], [edge(parent, sub)]))

    conforms, results, _ = shacl_validate(
        graph, Path("ontology/shapes.ttl"), Path("ontology/ontology.ttl")
    )
    assert conforms, dict(summarise(results))


def test_checks_count_orphans(staged):
    parent, sub = mention("parent"), mention("sub", name="ACME SUBSIDIARY LLC")
    lonely = mention("lonely", name="UNLINKED CORP")
    checks = parquet_only_checks(*staged([parent, sub, lonely], [edge(parent, sub)]))

    assert checks["orphan_nodes"] == 1
    assert checks["total_nodes"] == 3
    assert checks["orphan_rate"] == round(1 / 3, 4)
    assert checks["ownership_cycles"] == 0


def test_checks_detect_an_ownership_cycle(staged):
    a, b = mention("a"), mention("b", name="BETA INC.")
    checks = parquet_only_checks(*staged([a, b], [edge(a, b), edge(b, a)]))

    assert checks["ownership_cycles"] > 0
