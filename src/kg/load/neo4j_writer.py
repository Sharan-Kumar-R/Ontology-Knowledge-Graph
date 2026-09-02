from pathlib import Path

import pandas as pd
from neo4j import Driver

from kg.parse.schema import EDGE_TYPES

MENTION_QUERY = """
UNWIND $rows AS row
MERGE (m:Mention {mention_id: row.mention_id})
SET m.mention_type = row.mention_type,
    m.name = row.name,
    m.attrs = row.attrs,
    m.source_doc = row.source_doc,
    m.source_uri = row.source_uri,
    m.char_offset = row.char_offset,
    m.extractor = row.extractor,
    m.extractor_version = row.extractor_version,
    m.confidence = row.confidence,
    m.modality = row.modality
"""

EDGE_QUERY_TEMPLATE = """
UNWIND $rows AS row
MATCH (a:Mention {mention_id: row.src_mention_id})
MATCH (b:Mention {mention_id: row.dst_mention_id})
MERGE (a)-[r:%s {edge_id: row.edge_id}]->(b)
SET r.attrs = row.attrs,
    r.source_doc = row.source_doc,
    r.char_offset = row.char_offset,
    r.extractor = row.extractor,
    r.extractor_version = row.extractor_version,
    r.confidence = row.confidence,
    r.modality = row.modality
"""


def apply_constraints(driver: Driver, cypher_path) -> int:
    statements = [
        s.strip()
        for s in Path(cypher_path).read_text(encoding="utf-8").split(";")
        if s.strip()
    ]
    with driver.session() as session:
        for statement in statements:
            session.run(statement)
    return len(statements)


def clear_graph(driver: Driver) -> None:
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")


def _batches(df: pd.DataFrame, size: int):
    for start in range(0, len(df), size):
        yield df.iloc[start : start + size].to_dict("records")


REQUIRED_PROVENANCE = ("source_doc", "extractor", "extractor_version")


def _assert_provenance(df: pd.DataFrame, label: str) -> None:
    """Community Edition cannot enforce property existence, so check here."""
    for column in REQUIRED_PROVENANCE:
        missing = int(df[column].isna().sum())
        if missing:
            raise ValueError(
                f"{missing} {label} rows are missing {column}; "
                "provenance coverage must be 100%"
            )


def load_mentions(driver: Driver, parquet_path, batch_size: int = 5000) -> int:
    df = pd.read_parquet(parquet_path)
    _assert_provenance(df, "mention")
    df = df.astype(object).where(pd.notna(df), None)
    total = 0
    with driver.session() as session:
        for rows in _batches(df, batch_size):
            session.run(MENTION_QUERY, rows=rows)
            total += len(rows)
    return total


def load_edges(driver: Driver, parquet_path, batch_size: int = 5000) -> int:
    df = pd.read_parquet(parquet_path)
    _assert_provenance(df, "edge")
    df = df.astype(object).where(pd.notna(df), None)
    total = 0
    with driver.session() as session:
        for edge_type, group in df.groupby("edge_type"):
            if edge_type not in EDGE_TYPES:
                raise ValueError(f"refusing to load unknown edge_type: {edge_type}")
            query = EDGE_QUERY_TEMPLATE % edge_type
            for rows in _batches(group, batch_size):
                session.run(query, rows=rows)
                total += len(rows)
    return total
