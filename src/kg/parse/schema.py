import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional, Set, Tuple

import pandas as pd

ONTOLOGY_PATH = Path("ontology/ontology.ttl")
KG_NS = "http://kg.local/sec#"


def load_vocabulary(path: Optional[Path] = None) -> Tuple[Set[str], Set[str]]:
    """Read the allowed mention and edge types straight from the ontology.

    Classes become mention types. Object properties become edge types via their
    kg:edgeLabel annotation, so the ontology is the only place the vocabulary is
    declared and the two cannot drift apart.
    """
    from rdflib import OWL, RDF, Graph, URIRef

    path = Path(path) if path else ONTOLOGY_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. schema.py derives its vocabulary from the "
            f"ontology, so run commands from the project root."
        )
    graph = Graph()
    graph.parse(path, format="turtle")

    mention_types = {
        str(c).split("#")[-1] for c in graph.subjects(RDF.type, OWL.Class)
    }
    edge_label = URIRef(KG_NS + "edgeLabel")
    edge_types = {str(o) for o in graph.objects(None, edge_label)}
    return mention_types, edge_types


MENTION_TYPES, EDGE_TYPES = load_vocabulary()

MODALITIES = {"structured", "semi", "unstructured"}


def make_mention_id(source_doc: str, extractor: str, local_key: str) -> str:
    payload = f"{source_doc}|{extractor}|{local_key}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


@dataclass
class Mention:
    mention_id: str
    mention_type: str
    name: Optional[str]
    attrs: dict
    source_doc: str
    source_uri: str
    char_offset: Optional[int]
    extractor: str
    extractor_version: str
    confidence: float
    modality: str

    def __post_init__(self):
        if self.mention_type not in MENTION_TYPES:
            raise ValueError(f"unknown mention_type: {self.mention_type}")
        if self.modality not in MODALITIES:
            raise ValueError(f"unknown modality: {self.modality}")


@dataclass
class EdgeMention:
    edge_id: str
    src_mention_id: str
    dst_mention_id: str
    edge_type: str
    attrs: dict
    source_doc: str
    char_offset: Optional[int]
    extractor: str
    extractor_version: str
    confidence: float
    modality: str

    def __post_init__(self):
        if self.edge_type not in EDGE_TYPES:
            raise ValueError(f"unknown edge_type: {self.edge_type}")
        if self.modality not in MODALITIES:
            raise ValueError(f"unknown modality: {self.modality}")


def _to_frame(rows: List) -> pd.DataFrame:
    records = []
    for row in rows:
        rec = asdict(row)
        rec["attrs"] = json.dumps(rec["attrs"], sort_keys=False)
        records.append(rec)
    return pd.DataFrame(records)


def write_mentions(mentions: List, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _to_frame(mentions).to_parquet(path, index=False)
    return path


def write_edges(edges: List, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _to_frame(edges).to_parquet(path, index=False)
    return path
