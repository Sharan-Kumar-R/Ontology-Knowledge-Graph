"""Ladder rung R0: link mentions that agree on a strong identifier."""

import hashlib
import json
from typing import List, Optional, Tuple

from kg.parse.schema import EdgeMention, Mention, make_mention_id

EXTRACTOR = "resolve_r0"
EXTRACTOR_VERSION = "1"
METHOD = "identifier-agreement"

RESOLUTION_DOC = hashlib.sha256(
    f"kg:resolve:{EXTRACTOR}:{EXTRACTOR_VERSION}".encode("utf-8")
).hexdigest()


def _attrs(row) -> dict:
    raw = row.get("attrs") if isinstance(row, dict) else row.attrs
    if isinstance(raw, dict):
        return raw
    return json.loads(raw or "{}")


def _get(row, key):
    return row.get(key) if isinstance(row, dict) else getattr(row, key)


def _survivor(members: list) -> str:
    """Pick the canonical name: highest confidence, then structured, then longest."""
    ranked = sorted(
        members,
        key=lambda m: (
            -float(_get(m, "confidence") or 0),
            0 if _get(m, "modality") == "structured" else 1,
            -len(_get(m, "name") or ""),
        ),
    )
    return _get(ranked[0], "name")


def _group_by_identifier(rows: list, scheme: str) -> dict:
    """Group LegalEntity mentions by identifier value, deduplicating on mention_id."""
    groups: dict = {}
    for row in rows:
        if _get(row, "mention_type") != "LegalEntity":
            continue
        value = _attrs(row).get(scheme)
        if value:
            groups.setdefault(str(value), {})[_get(row, "mention_id")] = row
    return {value: list(members.values()) for value, members in groups.items()}


def resolve_by_cik(
    rows: list, scheme: str = "cik"
) -> Tuple[List[Mention], List[EdgeMention]]:
    """Collapse same-identifier mentions into one canonical Entity each, skipping singletons."""
    entities: List[Mention] = []
    edges: List[EdgeMention] = []

    for value, members in sorted(_group_by_identifier(rows, scheme).items()):
        if len(members) < 2:
            continue

        entity = Mention(
            mention_id=make_mention_id(RESOLUTION_DOC, EXTRACTOR, f"{scheme}:{value}"),
            mention_type="Entity",
            name=_survivor(members),
            attrs={
                scheme: value,
                "method": METHOD,
                "member_count": len(members),
                "members": sorted(_get(m, "mention_id") for m in members),
            },
            source_doc=RESOLUTION_DOC,
            source_uri=f"kg:resolve/{EXTRACTOR}",
            char_offset=None,
            extractor=EXTRACTOR,
            extractor_version=EXTRACTOR_VERSION,
            confidence=1.0,
            modality="structured",
        )
        entities.append(entity)

        for member in members:
            member_id = _get(member, "mention_id")
            edges.append(
                EdgeMention(
                    edge_id=make_mention_id(
                        RESOLUTION_DOC, EXTRACTOR, f"resolves:{member_id}"
                    ),
                    src_mention_id=member_id,
                    dst_mention_id=entity.mention_id,
                    edge_type="RESOLVES_TO",
                    attrs={"method": METHOD, "scheme": scheme},
                    source_doc=RESOLUTION_DOC,
                    char_offset=None,
                    extractor=EXTRACTOR,
                    extractor_version=EXTRACTOR_VERSION,
                    confidence=1.0,
                    modality="structured",
                )
            )

    return entities, edges


def resolve_parquet(mentions_path, scheme: str = "cik"):
    """Read staged mentions and resolve them without touching a database."""
    import pandas as pd

    df = pd.read_parquet(mentions_path)
    rows = df.astype(object).where(pd.notna(df), None).to_dict("records")
    return resolve_by_cik(rows, scheme=scheme)


def summarise(entities: list, edges: list, total_mentions: Optional[int] = None) -> dict:
    """Count what resolution produced, for the CLI to print."""
    return {
        "canonical_entities": len(entities),
        "mentions_linked": len(edges),
        "mentions_collapsed": len(edges) - len(entities),
        "total_mentions": total_mentions,
    }
