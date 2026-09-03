from kg.parse.schema import Mention, make_mention_id
from kg.resolve.deterministic import RESOLUTION_DOC, resolve_by_cik


def mention(doc, extractor, name, cik, confidence=1.0, modality="structured"):
    return Mention(
        mention_id=make_mention_id(doc, extractor, cik),
        mention_type="LegalEntity",
        name=name,
        attrs={"cik": cik},
        source_doc=doc,
        source_uri="https://example.com/x",
        char_offset=None,
        extractor=extractor,
        extractor_version="1",
        confidence=confidence,
        modality=modality,
    )


def test_same_cik_across_documents_collapses_to_one_entity():
    rows = [
        mention("a" * 64, "sec_tickers", "APPLE INC.", "0000320193"),
        mention("b" * 64, "xbrl_companyfacts", "Apple Inc.", "0000320193"),
    ]
    entities, edges = resolve_by_cik(rows)

    assert len(entities) == 1
    assert len(edges) == 2
    assert {e.dst_mention_id for e in edges} == {entities[0].mention_id}
    assert entities[0].mention_type == "Entity"


def test_different_ciks_stay_separate():
    rows = [
        mention("a" * 64, "sec_tickers", "APPLE INC.", "0000320193"),
        mention("b" * 64, "xbrl_companyfacts", "APPLE INC.", "0000320193"),
        mention("c" * 64, "sec_tickers", "NVIDIA CORP", "0001045810"),
        mention("d" * 64, "xbrl_companyfacts", "NVIDIA CORP", "0001045810"),
    ]
    entities, _ = resolve_by_cik(rows)
    assert len(entities) == 2


def test_a_lone_mention_produces_no_entity():
    entities, edges = resolve_by_cik([mention("a" * 64, "sec_tickers", "SOLO INC.", "1")])
    assert entities == []
    assert edges == []


def test_duplicate_rows_for_one_mention_id_count_once():
    row = mention("a" * 64, "sec_tickers", "APPLE INC.", "0000320193")
    other = mention("b" * 64, "xbrl_companyfacts", "Apple Inc.", "0000320193")
    entities, edges = resolve_by_cik([row, row, other])

    assert len(edges) == 2
    assert entities[0].attrs["member_count"] == 2


def test_survivorship_prefers_structured_over_heuristic():
    rows = [
        mention("a" * 64, "exhibit21", "apple", "0000320193", confidence=0.85, modality="semi"),
        mention("b" * 64, "sec_tickers", "APPLE INC.", "0000320193"),
    ]
    entities, _ = resolve_by_cik(rows)
    assert entities[0].name == "APPLE INC."


def test_entity_carries_resolver_provenance():
    rows = [
        mention("a" * 64, "sec_tickers", "APPLE INC.", "0000320193"),
        mention("b" * 64, "xbrl_companyfacts", "Apple Inc.", "0000320193"),
    ]
    entities, edges = resolve_by_cik(rows)

    assert entities[0].source_doc == RESOLUTION_DOC
    assert len(RESOLUTION_DOC) == 64
    assert entities[0].extractor == "resolve_r0"
    assert all(e.edge_type == "RESOLVES_TO" for e in edges)


def test_resolution_is_deterministic():
    rows = [
        mention("a" * 64, "sec_tickers", "APPLE INC.", "0000320193"),
        mention("b" * 64, "xbrl_companyfacts", "Apple Inc.", "0000320193"),
    ]
    first, _ = resolve_by_cik(rows)
    second, _ = resolve_by_cik(rows)
    assert first[0].mention_id == second[0].mention_id
