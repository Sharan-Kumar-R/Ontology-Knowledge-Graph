from pathlib import Path

import pandas as pd

from kg.parse.schema import EdgeMention, Mention, make_mention_id

EXTRACTOR_VERSION = "1"


def _identifier(scheme, value, source_doc, source_uri, extractor):
    return Mention(
        mention_id=make_mention_id(source_doc, extractor, f"{scheme}:{value}"),
        mention_type="Identifier",
        name=value,
        attrs={"scheme": scheme},
        source_doc=source_doc,
        source_uri=source_uri,
        char_offset=None,
        extractor=extractor,
        extractor_version=EXTRACTOR_VERSION,
        confidence=1.0,
        modality="structured",
    )


def _identified_by(entity, identifier, extractor):
    return EdgeMention(
        edge_id=make_mention_id(
            entity.source_doc, extractor, f"{entity.mention_id}->{identifier.mention_id}"
        ),
        src_mention_id=entity.mention_id,
        dst_mention_id=identifier.mention_id,
        edge_type="IDENTIFIED_BY",
        attrs={},
        source_doc=entity.source_doc,
        char_offset=None,
        extractor=extractor,
        extractor_version=EXTRACTOR_VERSION,
        confidence=1.0,
        modality="structured",
    )


def parse_gleif_lei(parquet_path, source_doc: str, source_uri: str):
    extractor = "gleif_lei"
    df = pd.read_parquet(Path(parquet_path))
    mentions, edges = [], []
    for row in df.itertuples(index=False):
        entity = Mention(
            mention_id=make_mention_id(source_doc, extractor, row.lei),
            mention_type="LegalEntity",
            name=row.legal_name,
            attrs={
                "legal_jurisdiction": row.legal_jurisdiction,
                "country": row.country,
                "city": row.city,
                "entity_status": row.entity_status,
                "legal_form_code": row.legal_form_code,
            },
            source_doc=source_doc,
            source_uri=source_uri,
            char_offset=None,
            extractor=extractor,
            extractor_version=EXTRACTOR_VERSION,
            confidence=1.0,
            modality="structured",
        )
        lei_node = _identifier("LEI", row.lei, source_doc, source_uri, extractor)
        mentions.extend([entity, lei_node])
        edges.append(_identified_by(entity, lei_node, extractor))
    return mentions, edges


def parse_company_tickers(records: list, source_doc: str, source_uri: str):
    extractor = "sec_tickers"
    mentions, edges = [], []
    for rec in records:
        cik = str(int(rec["cik_str"])).zfill(10)
        entity = Mention(
            mention_id=make_mention_id(source_doc, extractor, cik),
            mention_type="LegalEntity",
            name=rec["title"],
            attrs={"cik": cik},
            source_doc=source_doc,
            source_uri=source_uri,
            char_offset=None,
            extractor=extractor,
            extractor_version=EXTRACTOR_VERSION,
            confidence=1.0,
            modality="structured",
        )
        mentions.append(entity)
        for scheme, value in (("CIK", cik), ("TICKER", rec["ticker"])):
            ident = _identifier(scheme, value, source_doc, source_uri, extractor)
            mentions.append(ident)
            edges.append(_identified_by(entity, ident, extractor))
    return mentions, edges
