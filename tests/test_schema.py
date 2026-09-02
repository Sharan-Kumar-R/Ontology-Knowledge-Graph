import pandas as pd
import pytest

from kg.parse.schema import (
    EdgeMention,
    Mention,
    make_mention_id,
    write_edges,
    write_mentions,
)


def sample_mention(**overrides):
    base = dict(
        mention_type="LegalEntity",
        name="ALPHABET INC.",
        attrs={"jurisdiction": "US-DE"},
        source_doc="a" * 64,
        source_uri="https://example.com/x",
        char_offset=None,
        extractor="test",
        extractor_version="1",
        confidence=1.0,
        modality="structured",
    )
    base.update(overrides)
    base["mention_id"] = make_mention_id(base["source_doc"], base["extractor"], base["name"])
    return Mention(**base)


def test_mention_id_is_deterministic_and_collision_resistant():
    a = make_mention_id("doc1", "gleif", "key1")
    b = make_mention_id("doc1", "gleif", "key1")
    c = make_mention_id("doc1", "gleif", "key2")
    assert a == b
    assert a != c
    assert len(a) == 40


def test_invalid_mention_type_is_rejected():
    with pytest.raises(ValueError, match="mention_type"):
        sample_mention(mention_type="Sandwich")


def test_invalid_modality_is_rejected():
    with pytest.raises(ValueError, match="modality"):
        sample_mention(modality="telepathy")


def test_write_mentions_roundtrips_through_parquet(tmp_path):
    path = write_mentions([sample_mention()], tmp_path / "mentions.parquet")
    df = pd.read_parquet(path)
    assert len(df) == 1
    assert df.loc[0, "name"] == "ALPHABET INC."
    assert df.loc[0, "attrs"] == '{"jurisdiction": "US-DE"}'
    assert df.loc[0, "modality"] == "structured"


def test_write_edges_roundtrips_through_parquet(tmp_path):
    edge = EdgeMention(
        edge_id="e1",
        src_mention_id="m1",
        dst_mention_id="m2",
        edge_type="PARENT_OF",
        attrs={},
        source_doc="b" * 64,
        char_offset=12,
        extractor="test",
        extractor_version="1",
        confidence=0.9,
        modality="semi",
    )
    df = pd.read_parquet(write_edges([edge], tmp_path / "edges.parquet"))
    assert df.loc[0, "edge_type"] == "PARENT_OF"
    assert df.loc[0, "char_offset"] == 12
