import re
from typing import Optional

from lxml import html as lxml_html

from kg.parse.schema import EdgeMention, Mention, make_mention_id

EXTRACTOR_VERSION = "1"
DEFAULT_TAGS = ("Revenues", "Assets", "NetIncomeLoss")
EX21_CONFIDENCE = 0.85

_DOTS = re.compile(r"\.{3,}|\s{3,}|\t+")
_HEADING = re.compile(r"subsidiar|jurisdiction|name of|state of|registrant", re.I)
_HEADING_LABEL = re.compile(
    r"^(name|entity|entity name|company|subsidiary|legal name)$", re.I
)
_HEADING_VALUE = re.compile(
    r"incorporat|jurisdiction|domicile|organiz|state or|country|location", re.I
)


def _is_header_row(name: str, value: str) -> bool:
    return bool(
        _HEADING.search(name)
        or _HEADING_LABEL.match(name.strip())
        or _HEADING_VALUE.search(value)
    )


def parse_companyfacts(
    facts: dict,
    source_doc: str,
    source_uri: str,
    cik: str,
    tags: Optional[tuple] = None,
):
    extractor = "xbrl_companyfacts"
    tags = tags or DEFAULT_TAGS
    mentions, edges = [], []

    filer = Mention(
        mention_id=make_mention_id(source_doc, extractor, f"filer:{cik}"),
        mention_type="LegalEntity",
        name=facts.get("entityName"),
        attrs={"cik": cik},
        source_doc=source_doc,
        source_uri=source_uri,
        char_offset=None,
        extractor=extractor,
        extractor_version=EXTRACTOR_VERSION,
        confidence=1.0,
        modality="semi",
    )
    mentions.append(filer)

    for taxonomy, concepts in facts.get("facts", {}).items():
        for tag, body in concepts.items():
            if tag not in tags:
                continue
            concept = Mention(
                mention_id=make_mention_id(source_doc, extractor, f"concept:{taxonomy}:{tag}"),
                mention_type="XBRLConcept",
                name=tag,
                attrs={"taxonomy": taxonomy, "label": body.get("label")},
                source_doc=source_doc,
                source_uri=source_uri,
                char_offset=None,
                extractor=extractor,
                extractor_version=EXTRACTOR_VERSION,
                confidence=1.0,
                modality="semi",
            )
            mentions.append(concept)
            for unit, observations in body.get("units", {}).items():
                for obs in observations:
                    local = (
                        f"{taxonomy}:{tag}:{unit}:{obs.get('accn')}:"
                        f"{obs.get('start')}:{obs.get('end')}"
                    )
                    fact = Mention(
                        mention_id=make_mention_id(source_doc, extractor, local),
                        mention_type="FinancialFact",
                        name=None,
                        attrs={
                            "tag": tag,
                            "taxonomy": taxonomy,
                            "unit": unit,
                            "val": obs.get("val"),
                            "start": obs.get("start"),
                            "end": obs.get("end"),
                            "fy": obs.get("fy"),
                            "fp": obs.get("fp"),
                            "form": obs.get("form"),
                            "accn": obs.get("accn"),
                            "cik": cik,
                        },
                        source_doc=source_doc,
                        source_uri=source_uri,
                        char_offset=None,
                        extractor=extractor,
                        extractor_version=EXTRACTOR_VERSION,
                        confidence=1.0,
                        modality="semi",
                    )
                    mentions.append(fact)
                    edges.append(
                        EdgeMention(
                            edge_id=make_mention_id(source_doc, extractor, f"reports:{local}"),
                            src_mention_id=filer.mention_id,
                            dst_mention_id=fact.mention_id,
                            edge_type="REPORTS",
                            attrs={},
                            source_doc=source_doc,
                            char_offset=None,
                            extractor=extractor,
                            extractor_version=EXTRACTOR_VERSION,
                            confidence=1.0,
                            modality="semi",
                        )
                    )
    return mentions, edges


def _rows_from_tables(tree) -> list:
    rows = []
    for table in tree.xpath("//table"):
        for tr in table.xpath(".//tr"):
            cells = [
                " ".join(td.text_content().split())
                for td in tr.xpath("./td|./th")
            ]
            cells = [c for c in cells if c]
            if len(cells) >= 2:
                rows.append((cells[0], cells[1]))
    return rows


def _rows_from_text(tree) -> list:
    rows = []
    for line in tree.text_content().splitlines():
        line = line.strip()
        if not line or _HEADING.search(line):
            continue
        parts = [p.strip(" .") for p in _DOTS.split(line) if p.strip(" .")]
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    return rows


def parse_exhibit21(
    html_bytes: bytes,
    source_doc: str,
    source_uri: str,
    parent_cik: str,
    parent_name: str,
):
    extractor = "exhibit21"
    tree = lxml_html.fromstring(html_bytes)

    rows = [r for r in _rows_from_tables(tree) if not _is_header_row(r[0], r[1])]
    if not rows:
        rows = [r for r in _rows_from_text(tree) if not _is_header_row(r[0], r[1])]

    parent = Mention(
        mention_id=make_mention_id(source_doc, extractor, f"parent:{parent_cik}"),
        mention_type="LegalEntity",
        name=parent_name,
        attrs={"cik": parent_cik, "role": "parent"},
        source_doc=source_doc,
        source_uri=source_uri,
        char_offset=None,
        extractor=extractor,
        extractor_version=EXTRACTOR_VERSION,
        confidence=1.0,
        modality="semi",
    )
    mentions, edges = [parent], []

    for name, jurisdiction in rows:
        sub = Mention(
            mention_id=make_mention_id(source_doc, extractor, f"sub:{name}"),
            mention_type="LegalEntity",
            name=name,
            attrs={
                "role": "subsidiary",
                "jurisdiction_text": jurisdiction,
                "parent_cik": parent_cik,
            },
            source_doc=source_doc,
            source_uri=source_uri,
            char_offset=None,
            extractor=extractor,
            extractor_version=EXTRACTOR_VERSION,
            confidence=EX21_CONFIDENCE,
            modality="semi",
        )
        mentions.append(sub)
        edges.append(
            EdgeMention(
                edge_id=make_mention_id(source_doc, extractor, f"parent_of:{name}"),
                src_mention_id=parent.mention_id,
                dst_mention_id=sub.mention_id,
                edge_type="PARENT_OF",
                attrs={},
                source_doc=source_doc,
                char_offset=None,
                extractor=extractor,
                extractor_version=EXTRACTOR_VERSION,
                confidence=EX21_CONFIDENCE,
                modality="semi",
            )
        )
    return mentions, edges
