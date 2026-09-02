# Week 1: Ingest, Parse, Load — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fetch SEC EDGAR and GLEIF data, parse it through three modality-specific parsers into one uniform mention/edge schema, and load it into Neo4j as a provenance-bearing `:Mention` layer.

**Architecture:** A content-hash cache makes every fetch idempotent and every re-run cheap. Three parsers — structured (GLEIF CSV, tickers JSON), semi-structured (XBRL companyfacts JSON, Exhibit 21 HTML), unstructured (10-K narrative via LLM) — all emit the same two Parquet tables: `mentions.parquet` and `edge_mentions.parquet`. A batched Neo4j writer loads those tables into `:Mention` nodes with a thin schema. Entity resolution is week 3 and touches none of this.

**Tech Stack:** Python 3.10, Neo4j 5 + neosemantics (Docker), pandas + pyarrow, DuckDB, lxml, httpx, pydantic v2, typer, pytest

**Spec:** `docs/superpowers/specs/2026-09-02-enterprise-kg-design.md`

## Global Constraints

- Python 3.10 (`python --version` → 3.10.11). Do not require 3.11+ syntax.
- Bulk data root is `C:\kg-data\` — outside OneDrive. Never hardcode it; always read from config.
- `config/settings.yaml` is gitignored. `config/settings.yaml.example` is committed.
- SEC requires a `User-Agent` header containing a contact email on every request, and rate-limits to 10 requests/second. Client caps at 8 req/s.
- Raw fetched bytes are immutable and content-addressed by SHA-256. Nothing overwrites a raw file.
- Every mention and edge row carries `source_doc`, `extractor`, `extractor_version`, `confidence`. No exceptions — provenance coverage is a week-4 metric that must read 100%.
- Tests never hit the network. Network code is tested through injected fake fetchers and committed fixtures.
- Commit after every task.

---

### Task 1: Project scaffold and config layer

**Files:**
- Create: `pyproject.toml`
- Create: `src/kg/__init__.py`
- Create: `src/kg/config.py`
- Create: `config/settings.yaml.example`
- Create: `config/settings.yaml` (gitignored)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing
- Produces: `kg.config.Settings` (pydantic model) with fields `data_root: Path`, `sec_user_agent: str`, `sec_rate_limit: float`, `neo4j_uri: str`, `neo4j_user: str`, `neo4j_password: str`, `llm_model: str`. Function `load_settings(path: Path | None = None) -> Settings`. Properties `Settings.raw_dir`, `Settings.staging_dir`, `Settings.gold_dir` returning `Path`, each created on access.

- [ ] **Step 1: Create the virtualenv and install dependencies**

```bash
cd "c:/Users/shara/OneDrive/Desktop/onotology"
python -m venv .venv
.venv/Scripts/python.exe -m pip install --upgrade pip
.venv/Scripts/python.exe -m pip install pandas pyarrow duckdb httpx lxml pydantic pyyaml typer neo4j rapidfuzz scikit-learn datasketch pytest
```

Expected: all install cleanly. `pandas` and `pyarrow` are the largest.

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "kg"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "pandas", "pyarrow", "duckdb", "httpx", "lxml",
    "pydantic>=2", "pyyaml", "typer", "neo4j",
    "rapidfuzz", "scikit-learn", "datasketch",
]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 3: Write the failing test**

Create `tests/test_config.py`:

```python
from pathlib import Path

import pytest

from kg.config import Settings, load_settings


def test_load_settings_reads_yaml(tmp_path):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(
        "data_root: %s\n"
        "sec_user_agent: 'Test test@example.com'\n"
        "neo4j_password: secret\n" % (tmp_path / "kgdata").as_posix()
    )
    s = load_settings(cfg)
    assert s.sec_user_agent == "Test test@example.com"
    assert s.neo4j_password == "secret"
    assert s.sec_rate_limit == 8.0
    assert s.neo4j_uri == "bolt://localhost:7687"


def test_directories_are_created_on_access(tmp_path):
    s = Settings(
        data_root=tmp_path / "kgdata",
        sec_user_agent="Test test@example.com",
        neo4j_password="secret",
    )
    assert s.raw_dir.is_dir()
    assert s.staging_dir.is_dir()
    assert s.gold_dir.is_dir()


def test_user_agent_must_contain_email(tmp_path):
    with pytest.raises(ValueError, match="contact email"):
        Settings(
            data_root=tmp_path,
            sec_user_agent="just-a-name",
            neo4j_password="secret",
        )
```

- [ ] **Step 4: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kg.config'`

- [ ] **Step 5: Write `src/kg/config.py`**

```python
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, field_validator

DEFAULT_CONFIG = Path("config/settings.yaml")


class Settings(BaseModel):
    data_root: Path
    sec_user_agent: str
    sec_rate_limit: float = 8.0
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str
    llm_model: str = "claude-sonnet-5"

    @field_validator("sec_user_agent")
    @classmethod
    def _must_have_email(cls, v: str) -> str:
        if "@" not in v:
            raise ValueError(
                "sec_user_agent must contain a contact email; SEC fair-access "
                "policy rejects requests without one"
            )
        return v

    def _sub(self, name: str) -> Path:
        p = self.data_root / name
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def raw_dir(self) -> Path:
        return self._sub("raw")

    @property
    def staging_dir(self) -> Path:
        return self._sub("staging")

    @property
    def gold_dir(self) -> Path:
        return self._sub("gold")


def load_settings(path: Optional[Path] = None) -> Settings:
    path = Path(path) if path else DEFAULT_CONFIG
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Copy config/settings.yaml.example to "
            f"config/settings.yaml and fill it in."
        )
    data = yaml.safe_load(path.read_text()) or {}
    return Settings(**data)
```

Create `src/kg/__init__.py` as an empty file.

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py -v`
Expected: 3 passed

- [ ] **Step 7: Write both config files**

`config/settings.yaml.example`:

```yaml
data_root: C:/kg-data
sec_user_agent: "YourName your.email@example.com"
sec_rate_limit: 8.0
neo4j_uri: bolt://localhost:7687
neo4j_user: neo4j
neo4j_password: changeme_kg_local
llm_model: claude-sonnet-5
```

`config/settings.yaml` — same, with `sec_user_agent: "YourName your.email@example.com"`.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/kg/__init__.py src/kg/config.py config/settings.yaml.example tests/test_config.py
git commit -m "feat: project scaffold and validated config layer"
```

---

### Task 2: Neo4j via Docker with neosemantics

**Files:**
- Create: `docker-compose.yml`
- Create: `src/kg/load/__init__.py`
- Create: `src/kg/load/neo4j_conn.py`
- Test: `tests/test_neo4j_conn.py`

**Interfaces:**
- Consumes: `kg.config.Settings`
- Produces: `kg.load.neo4j_conn.get_driver(settings) -> neo4j.Driver`, `kg.load.neo4j_conn.check_connection(driver) -> dict` returning `{"neo4j_version": str, "n10s_available": bool}`

- [ ] **Step 1: Write `docker-compose.yml`**

```yaml
services:
  neo4j:
    image: neo4j:5.26-community
    container_name: kg-neo4j
    ports:
      - "7474:7474"
      - "7687:7687"
    environment:
      NEO4J_AUTH: neo4j/changeme_kg_local
      NEO4J_PLUGINS: '["n10s","apoc"]'
      NEO4J_dbms_security_procedures_unrestricted: "n10s.*,apoc.*"
      NEO4J_dbms_security_procedures_allowlist: "n10s.*,apoc.*"
      NEO4J_server_memory_heap_max__size: 2G
      NEO4J_server_memory_pagecache_size: 1G
    volumes:
      - C:/kg-data/neo4j/data:/data
      - C:/kg-data/neo4j/logs:/logs
```

Note: `NEO4J_PLUGINS` makes the official image download n10s and apoc on first boot. No manual jar.

- [ ] **Step 2: Start Docker Desktop, then bring up Neo4j**

```bash
mkdir -p /c/kg-data/neo4j/data /c/kg-data/neo4j/logs
docker compose up -d
docker compose logs -f neo4j
```

Expected: log line `Started.` after ~40s on first run (plugin download). Ctrl-C to stop tailing. Browser check: http://localhost:7474, login `neo4j` / `changeme_kg_local`.

- [ ] **Step 3: Write the failing test**

Create `tests/test_neo4j_conn.py`:

```python
import pytest

from kg.config import load_settings
from kg.load.neo4j_conn import check_connection, get_driver

pytestmark = pytest.mark.integration


def test_neo4j_reachable_with_n10s():
    settings = load_settings()
    driver = get_driver(settings)
    try:
        info = check_connection(driver)
    finally:
        driver.close()
    assert info["neo4j_version"].startswith("5.")
    assert info["n10s_available"] is True
```

Register the marker by appending to `pyproject.toml` under `[tool.pytest.ini_options]`:

```toml
markers = ["integration: requires a running Neo4j"]
```

- [ ] **Step 4: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_neo4j_conn.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kg.load'`

- [ ] **Step 5: Write `src/kg/load/neo4j_conn.py`**

```python
from neo4j import Driver, GraphDatabase

from kg.config import Settings


def get_driver(settings: Settings) -> Driver:
    return GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )


def check_connection(driver: Driver) -> dict:
    with driver.session() as session:
        version = session.run(
            "CALL dbms.components() YIELD versions RETURN versions[0] AS v"
        ).single()["v"]
        names = session.run(
            "SHOW PROCEDURES YIELD name WHERE name STARTS WITH 'n10s' RETURN count(*) AS c"
        ).single()["c"]
    return {"neo4j_version": version, "n10s_available": names > 0}
```

Create `src/kg/load/__init__.py` as an empty file.

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_neo4j_conn.py -v`
Expected: 1 passed

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml src/kg/load/__init__.py src/kg/load/neo4j_conn.py tests/test_neo4j_conn.py pyproject.toml
git commit -m "feat: neo4j 5 with n10s via docker compose, connection check"
```

---

### Task 3: Content-hash cache and rate-limited SEC client

**Files:**
- Create: `src/kg/ingest/__init__.py`
- Create: `src/kg/ingest/cache.py`
- Create: `src/kg/ingest/http.py`
- Test: `tests/test_cache.py`
- Test: `tests/test_http.py`

**Interfaces:**
- Consumes: `kg.config.Settings`
- Produces:
  - `kg.ingest.cache.RawCache(root: Path)` with `put(uri: str, content: bytes, content_type: str) -> str` returning the SHA-256 hex digest (the `source_doc` id used everywhere downstream), `get(doc_id: str) -> bytes`, `find_by_uri(uri: str) -> str | None`, `path_for(doc_id: str) -> Path`
  - `kg.ingest.http.RateLimiter(rate_per_sec: float)` with `.acquire()`
  - `kg.ingest.http.SecClient(settings, cache, fetch=None)` with `get_bytes(url: str, force: bool = False) -> tuple[str, bytes]` returning `(doc_id, content)`. The `fetch` parameter injects a callable `(url, headers) -> tuple[bytes, str]` for tests.

- [ ] **Step 1: Write the failing cache test**

Create `tests/test_cache.py`:

```python
from kg.ingest.cache import RawCache


def test_put_returns_sha256_and_is_content_addressed(tmp_path):
    cache = RawCache(tmp_path)
    doc_id = cache.put("https://example.com/a.json", b'{"x":1}', "application/json")
    assert len(doc_id) == 64
    assert cache.get(doc_id) == b'{"x":1}'
    assert cache.path_for(doc_id).exists()


def test_identical_content_from_two_uris_stores_once(tmp_path):
    cache = RawCache(tmp_path)
    a = cache.put("https://example.com/a", b"same", "text/plain")
    b = cache.put("https://example.com/b", b"same", "text/plain")
    assert a == b
    blobs = list((tmp_path / "blobs").rglob("*.bin"))
    assert len(blobs) == 1


def test_find_by_uri_survives_a_new_cache_instance(tmp_path):
    RawCache(tmp_path).put("https://example.com/a", b"payload", "text/plain")
    reopened = RawCache(tmp_path)
    doc_id = reopened.find_by_uri("https://example.com/a")
    assert doc_id is not None
    assert reopened.get(doc_id) == b"payload"


def test_find_by_uri_returns_none_when_absent(tmp_path):
    assert RawCache(tmp_path).find_by_uri("https://example.com/missing") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cache.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kg.ingest'`

- [ ] **Step 3: Write `src/kg/ingest/cache.py`**

```python
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class RawCache:
    """Immutable content-addressed store for fetched bytes."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.blobs = self.root / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "manifest.jsonl"
        self._uri_index: dict[str, str] = {}
        self._load_manifest()

    def _load_manifest(self) -> None:
        if not self.manifest_path.exists():
            return
        with self.manifest_path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    self._uri_index[rec["uri"]] = rec["doc_id"]

    def path_for(self, doc_id: str) -> Path:
        return self.blobs / doc_id[:2] / f"{doc_id}.bin"

    def put(self, uri: str, content: bytes, content_type: str) -> str:
        doc_id = hashlib.sha256(content).hexdigest()
        path = self.path_for(doc_id)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        rec = {
            "doc_id": doc_id,
            "uri": uri,
            "content_type": content_type,
            "bytes": len(content),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        with self.manifest_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        self._uri_index[uri] = doc_id
        return doc_id

    def get(self, doc_id: str) -> bytes:
        return self.path_for(doc_id).read_bytes()

    def find_by_uri(self, uri: str) -> Optional[str]:
        return self._uri_index.get(uri)
```

Create `src/kg/ingest/__init__.py` as an empty file.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cache.py -v`
Expected: 4 passed

- [ ] **Step 5: Write the failing HTTP client test**

Create `tests/test_http.py`:

```python
import time

from kg.config import Settings
from kg.ingest.cache import RawCache
from kg.ingest.http import RateLimiter, SecClient


def make_client(tmp_path, fetch):
    settings = Settings(
        data_root=tmp_path,
        sec_user_agent="Test test@example.com",
        sec_rate_limit=100.0,
        neo4j_password="x",
    )
    return SecClient(settings, RawCache(tmp_path / "raw"), fetch=fetch)


def test_get_bytes_sends_user_agent_and_caches(tmp_path):
    calls = []

    def fake_fetch(url, headers):
        calls.append((url, headers))
        return b"payload", "text/plain"

    client = make_client(tmp_path, fake_fetch)
    doc_id, content = client.get_bytes("https://data.sec.gov/x")
    assert content == b"payload"
    assert calls[0][1]["User-Agent"] == "Test test@example.com"

    doc_id2, content2 = client.get_bytes("https://data.sec.gov/x")
    assert doc_id2 == doc_id
    assert content2 == b"payload"
    assert len(calls) == 1


def test_force_refetches(tmp_path):
    calls = []

    def fake_fetch(url, headers):
        calls.append(url)
        return b"payload", "text/plain"

    client = make_client(tmp_path, fake_fetch)
    client.get_bytes("https://data.sec.gov/x")
    client.get_bytes("https://data.sec.gov/x", force=True)
    assert len(calls) == 2


def test_rate_limiter_spaces_calls():
    limiter = RateLimiter(20.0)
    start = time.monotonic()
    for _ in range(5):
        limiter.acquire()
    assert time.monotonic() - start >= 0.15
```

- [ ] **Step 6: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_http.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kg.ingest.http'`

- [ ] **Step 7: Write `src/kg/ingest/http.py`**

```python
import threading
import time
from typing import Callable, Optional

import httpx

from kg.config import Settings
from kg.ingest.cache import RawCache

FetchFn = Callable[[str, dict], tuple[bytes, str]]


class RateLimiter:
    """Blocks so calls are spaced at least 1/rate seconds apart."""

    def __init__(self, rate_per_sec: float):
        self.min_interval = 1.0 / rate_per_sec
        self._last = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()


def _httpx_fetch(url: str, headers: dict) -> tuple[bytes, str]:
    response = httpx.get(url, headers=headers, timeout=60.0, follow_redirects=True)
    response.raise_for_status()
    return response.content, response.headers.get("content-type", "")


class SecClient:
    """Rate-limited, cache-backed fetcher for SEC endpoints."""

    def __init__(
        self,
        settings: Settings,
        cache: RawCache,
        fetch: Optional[FetchFn] = None,
    ):
        self.settings = settings
        self.cache = cache
        self.limiter = RateLimiter(settings.sec_rate_limit)
        self._fetch = fetch or _httpx_fetch

    def get_bytes(self, url: str, force: bool = False) -> tuple[str, bytes]:
        if not force:
            doc_id = self.cache.find_by_uri(url)
            if doc_id is not None:
                return doc_id, self.cache.get(doc_id)
        self.limiter.acquire()
        headers = {
            "User-Agent": self.settings.sec_user_agent,
            "Accept-Encoding": "gzip, deflate",
        }
        content, content_type = self._fetch(url, headers)
        doc_id = self.cache.put(url, content, content_type)
        return doc_id, content
```

- [ ] **Step 8: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_http.py -v`
Expected: 3 passed

- [ ] **Step 9: Commit**

```bash
git add src/kg/ingest tests/test_cache.py tests/test_http.py
git commit -m "feat: content-addressed raw cache and rate-limited SEC client"
```

---

### Task 4: Mention and edge schema

**Files:**
- Create: `src/kg/parse/__init__.py`
- Create: `src/kg/parse/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `kg.parse.schema.Mention` dataclass: `mention_id, mention_type, name, attrs (dict), source_doc, source_uri, char_offset, extractor, extractor_version, confidence, modality`
  - `kg.parse.schema.EdgeMention` dataclass: `edge_id, src_mention_id, dst_mention_id, edge_type, attrs (dict), source_doc, char_offset, extractor, extractor_version, confidence, modality`
  - `kg.parse.schema.make_mention_id(source_doc: str, extractor: str, local_key: str) -> str`
  - `kg.parse.schema.write_mentions(mentions: list[Mention], path: Path) -> Path`
  - `kg.parse.schema.write_edges(edges: list[EdgeMention], path: Path) -> Path`
  - `kg.parse.schema.MENTION_TYPES`, `EDGE_TYPES`, `MODALITIES` — allowed value sets

This uniform schema is the reason downstream entity resolution is modality-blind. Every parser in Tasks 6–8 produces exactly these two shapes.

- [ ] **Step 1: Write the failing test**

Create `tests/test_schema.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kg.parse'`

- [ ] **Step 3: Write `src/kg/parse/schema.py`**

```python
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

MENTION_TYPES = {
    "LegalEntity",
    "Person",
    "Filing",
    "FinancialFact",
    "XBRLConcept",
    "Jurisdiction",
    "Industry",
    "Identifier",
}

EDGE_TYPES = {
    "FILED",
    "PARENT_OF",
    "INCORPORATED_IN",
    "IDENTIFIED_BY",
    "REPORTS",
    "ACQUIRED",
    "OFFICER_OF",
    "COMPETES_WITH",
}

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


def _to_frame(rows: list) -> pd.DataFrame:
    records = []
    for row in rows:
        rec = asdict(row)
        rec["attrs"] = json.dumps(rec["attrs"], sort_keys=False)
        records.append(rec)
    return pd.DataFrame(records)


def write_mentions(mentions: list, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _to_frame(mentions).to_parquet(path, index=False)
    return path


def write_edges(edges: list, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _to_frame(edges).to_parquet(path, index=False)
    return path
```

Create `src/kg/parse/__init__.py` as an empty file.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_schema.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/kg/parse tests/test_schema.py
git commit -m "feat: uniform mention and edge schema with parquet writers"
```

---

### Task 5: EDGAR ingest

**Files:**
- Create: `src/kg/ingest/edgar.py`
- Create: `tests/fixtures/company_tickers.json`
- Create: `tests/fixtures/submissions_AAPL.json`
- Test: `tests/test_edgar.py`

**Interfaces:**
- Consumes: `SecClient`, `RawCache`
- Produces:
  - `kg.ingest.edgar.fetch_company_tickers(client) -> tuple[str, list[dict]]` — each dict has `cik_str`, `ticker`, `title`; returns `(doc_id, records)`
  - `kg.ingest.edgar.cik10(cik) -> str` — zero-padded 10-digit CIK
  - `kg.ingest.edgar.fetch_submissions(client, cik) -> tuple[str, dict]`
  - `kg.ingest.edgar.recent_filings(submissions, form="10-K", limit=5) -> list[dict]` — each has `accession`, `filing_date`, `primary_document`
  - `kg.ingest.edgar.fetch_companyfacts(client, cik) -> tuple[str, dict]`
  - `kg.ingest.edgar.fetch_filing_index(client, cik, accession) -> tuple[str, dict]`
  - `kg.ingest.edgar.find_exhibit21(index_json) -> str | None` — filename of the EX-21 document
  - `kg.ingest.edgar.filing_doc_url(cik, accession, filename) -> str`

- [ ] **Step 1: Create the fixtures**

`tests/fixtures/company_tickers.json`:

```json
{"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
 "1": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."}}
```

`tests/fixtures/submissions_AAPL.json`:

```json
{"cik": "320193",
 "name": "Apple Inc.",
 "filings": {"recent": {
   "accessionNumber": ["0000320193-23-000106", "0000320193-23-000064"],
   "form": ["10-K", "10-Q"],
   "filingDate": ["2023-11-03", "2023-08-04"],
   "primaryDocument": ["aapl-20230930.htm", "aapl-20230701.htm"]}}}
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_edgar.py`:

```python
import json
from pathlib import Path

from kg.config import Settings
from kg.ingest.cache import RawCache
from kg.ingest.edgar import (
    cik10,
    fetch_company_tickers,
    fetch_submissions,
    filing_doc_url,
    find_exhibit21,
    recent_filings,
)
from kg.ingest.http import SecClient

FIXTURES = Path(__file__).parent / "fixtures"


def make_client(tmp_path, routes):
    def fake_fetch(url, headers):
        return routes[url], "application/json"

    settings = Settings(
        data_root=tmp_path,
        sec_user_agent="Test test@example.com",
        sec_rate_limit=1000.0,
        neo4j_password="x",
    )
    return SecClient(settings, RawCache(tmp_path / "raw"), fetch=fake_fetch)


def test_cik10_pads():
    assert cik10(320193) == "0000320193"
    assert cik10("320193") == "0000320193"
    assert cik10("0000320193") == "0000320193"


def test_fetch_company_tickers_returns_flat_records(tmp_path):
    url = "https://www.sec.gov/files/company_tickers.json"
    client = make_client(tmp_path, {url: (FIXTURES / "company_tickers.json").read_bytes()})
    doc_id, records = fetch_company_tickers(client)
    assert len(doc_id) == 64
    assert len(records) == 2
    assert {r["ticker"] for r in records} == {"AAPL", "GOOGL"}


def test_recent_filings_filters_by_form(tmp_path):
    url = "https://data.sec.gov/submissions/CIK0000320193.json"
    client = make_client(tmp_path, {url: (FIXTURES / "submissions_AAPL.json").read_bytes()})
    _, submissions = fetch_submissions(client, 320193)
    filings = recent_filings(submissions, form="10-K", limit=5)
    assert len(filings) == 1
    assert filings[0]["accession"] == "0000320193-23-000106"
    assert filings[0]["filing_date"] == "2023-11-03"
    assert filings[0]["primary_document"] == "aapl-20230930.htm"


def test_find_exhibit21_matches_ex21_variants():
    index = {"directory": {"item": [
        {"name": "aapl-20230930.htm", "type": "10-K"},
        {"name": "a10-kexhibit211q423.htm", "type": "EX-21.1"},
    ]}}
    assert find_exhibit21(index) == "a10-kexhibit211q423.htm"


def test_find_exhibit21_returns_none_when_absent():
    index = {"directory": {"item": [{"name": "x.htm", "type": "10-K"}]}}
    assert find_exhibit21(index) is None


def test_filing_doc_url_strips_dashes_from_accession():
    url = filing_doc_url(320193, "0000320193-23-000106", "aapl-20230930.htm")
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019323000106/aapl-20230930.htm"
    )
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_edgar.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kg.ingest.edgar'`

- [ ] **Step 4: Write `src/kg/ingest/edgar.py`**

```python
import json
from typing import Optional

from kg.ingest.http import SecClient

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{filename}"


def cik10(cik) -> str:
    return str(int(cik)).zfill(10)


def fetch_company_tickers(client: SecClient) -> tuple[str, list[dict]]:
    doc_id, content = client.get_bytes(TICKERS_URL)
    payload = json.loads(content)
    return doc_id, list(payload.values())


def fetch_submissions(client: SecClient, cik) -> tuple[str, dict]:
    doc_id, content = client.get_bytes(SUBMISSIONS_URL.format(cik=cik10(cik)))
    return doc_id, json.loads(content)


def fetch_companyfacts(client: SecClient, cik) -> tuple[str, dict]:
    doc_id, content = client.get_bytes(COMPANYFACTS_URL.format(cik=cik10(cik)))
    return doc_id, json.loads(content)


def fetch_filing_index(client: SecClient, cik, accession: str) -> tuple[str, dict]:
    url = INDEX_URL.format(cik=int(cik), acc=accession.replace("-", ""))
    doc_id, content = client.get_bytes(url)
    return doc_id, json.loads(content)


def recent_filings(submissions: dict, form: str = "10-K", limit: int = 5) -> list[dict]:
    recent = submissions["filings"]["recent"]
    out = []
    for i, f in enumerate(recent["form"]):
        if f != form:
            continue
        out.append(
            {
                "accession": recent["accessionNumber"][i],
                "filing_date": recent["filingDate"][i],
                "primary_document": recent["primaryDocument"][i],
                "form": f,
            }
        )
        if len(out) >= limit:
            break
    return out


def find_exhibit21(index_json: dict) -> Optional[str]:
    for item in index_json.get("directory", {}).get("item", []):
        if item.get("type", "").upper().startswith("EX-21"):
            return item["name"]
    return None


def filing_doc_url(cik, accession: str, filename: str) -> str:
    return DOC_URL.format(
        cik=int(cik), acc=accession.replace("-", ""), filename=filename
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_edgar.py -v`
Expected: 6 passed

- [ ] **Step 6: Smoke-test against the live SEC API**

```bash
.venv/Scripts/python.exe -c "
from kg.config import load_settings
from kg.ingest.cache import RawCache
from kg.ingest.http import SecClient
from kg.ingest.edgar import *
s = load_settings()
c = SecClient(s, RawCache(s.raw_dir))
_, recs = fetch_company_tickers(c)
print('tickers:', len(recs))
_, sub = fetch_submissions(c, 320193)
f = recent_filings(sub, '10-K', 2)
print('filings:', f)
_, idx = fetch_filing_index(c, 320193, f[0]['accession'])
print('ex21:', find_exhibit21(idx))
"
```

Expected: ~10,000 tickers, two 10-K filings, and a non-`None` EX-21 filename. If SEC returns 403, the `User-Agent` in `config/settings.yaml` is wrong.

- [ ] **Step 7: Commit**

```bash
git add src/kg/ingest/edgar.py tests/test_edgar.py tests/fixtures
git commit -m "feat: EDGAR ingest for tickers, submissions, companyfacts, exhibit 21"
```

---

### Task 6: GLEIF ingest with streaming filter

**Files:**
- Create: `src/kg/ingest/gleif.py`
- Create: `tests/fixtures/gleif_lei_sample.csv`
- Create: `tests/fixtures/gleif_rr_sample.csv`
- Test: `tests/test_gleif.py`

**Interfaces:**
- Consumes: `kg.config.Settings`
- Produces:
  - `kg.ingest.gleif.LEI_COLUMNS`, `RR_COLUMNS` — the column subsets kept
  - `kg.ingest.gleif.filter_lei_csv(src, dest, countries={"US"}, chunksize=100_000) -> int` — streams a Level 1 CSV, keeps only listed legal-jurisdiction countries and the needed columns, writes Parquet, returns row count
  - `kg.ingest.gleif.filter_rr_csv(src, dest, keep_leis: set[str] | None = None, chunksize=100_000) -> int` — same for Level 2 relationship records
  - `kg.ingest.gleif.latest_golden_copy_urls(fetch_json) -> dict` with keys `lei2` and `rr`

Streaming with `chunksize` is what keeps a 2.5 GB CSV inside a few hundred MB of RAM and a ~150 MB Parquet output. Never `read_csv` the whole file.

- [ ] **Step 1: Create the fixtures**

`tests/fixtures/gleif_lei_sample.csv`:

```csv
LEI,Entity.LegalName,Entity.LegalJurisdiction,Entity.LegalAddress.Country,Entity.LegalAddress.City,Entity.EntityStatus,Entity.LegalForm.EntityLegalFormCode,Registration.RegistrationStatus
HWUPKR0MPOU8FGXBT394,APPLE INC.,US-CA,US,Cupertino,ACTIVE,XTIQ,ISSUED
5493006MHB84DD0ZWV18,ALPHABET INC.,US-DE,US,Mountain View,ACTIVE,XTIQ,ISSUED
529900T8BM49AURSDO55,ALLIANZ SE,DE,DE,Muenchen,ACTIVE,2HBR,ISSUED
```

`tests/fixtures/gleif_rr_sample.csv`:

```csv
Relationship.StartNode.NodeID,Relationship.EndNode.NodeID,Relationship.RelationshipType,Relationship.RelationshipStatus
5493006MHB84DD0ZWV18,HWUPKR0MPOU8FGXBT394,IS_ULTIMATELY_CONSOLIDATED_BY,ACTIVE
529900T8BM49AURSDO55,HWUPKR0MPOU8FGXBT394,IS_DIRECTLY_CONSOLIDATED_BY,ACTIVE
```

Note: the parent/child pairing in this fixture is fabricated for test purposes only.

- [ ] **Step 2: Write the failing test**

Create `tests/test_gleif.py`:

```python
from pathlib import Path

import pandas as pd

from kg.ingest.gleif import filter_lei_csv, filter_rr_csv, latest_golden_copy_urls

FIXTURES = Path(__file__).parent / "fixtures"


def test_filter_lei_csv_keeps_only_requested_countries(tmp_path):
    dest = tmp_path / "lei.parquet"
    n = filter_lei_csv(FIXTURES / "gleif_lei_sample.csv", dest, countries={"US"}, chunksize=2)
    assert n == 2
    df = pd.read_parquet(dest)
    assert set(df["legal_name"]) == {"APPLE INC.", "ALPHABET INC."}
    assert set(df.columns) == {
        "lei", "legal_name", "legal_jurisdiction", "country", "city",
        "entity_status", "legal_form_code",
    }


def test_filter_lei_csv_all_countries_when_none(tmp_path):
    dest = tmp_path / "lei.parquet"
    n = filter_lei_csv(FIXTURES / "gleif_lei_sample.csv", dest, countries=None, chunksize=100)
    assert n == 3


def test_filter_rr_csv_restricts_to_known_leis(tmp_path):
    dest = tmp_path / "rr.parquet"
    n = filter_rr_csv(
        FIXTURES / "gleif_rr_sample.csv",
        dest,
        keep_leis={"5493006MHB84DD0ZWV18", "HWUPKR0MPOU8FGXBT394"},
        chunksize=1,
    )
    assert n == 1
    df = pd.read_parquet(dest)
    assert df.loc[0, "parent_lei"] == "HWUPKR0MPOU8FGXBT394"
    assert df.loc[0, "child_lei"] == "5493006MHB84DD0ZWV18"


def test_latest_golden_copy_urls_picks_csv_links():
    payload = {"data": [{
        "lei2": {"full_file": {"csv": {"url": "https://g/lei2.zip"}}},
        "rr": {"full_file": {"csv": {"url": "https://g/rr.zip"}}},
    }]}
    urls = latest_golden_copy_urls(lambda url: payload)
    assert urls["lei2"] == "https://g/lei2.zip"
    assert urls["rr"] == "https://g/rr.zip"
```

Note the direction inversion tested above: GLEIF Level 2 records state "child `IS_DIRECTLY_CONSOLIDATED_BY` parent", where the StartNode is the child. Our `PARENT_OF` edge runs parent to child, so `filter_rr_csv` swaps them on write. Getting this backwards silently inverts every ownership edge and every week-4 evaluation number.

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_gleif.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kg.ingest.gleif'`

- [ ] **Step 4: Write `src/kg/ingest/gleif.py`**

```python
from pathlib import Path
from typing import Callable, Optional

import httpx
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

GOLDEN_COPY_API = "https://goldencopy.gleif.org/api/v2/golden-copies/publishes"

LEI_COLUMNS = {
    "LEI": "lei",
    "Entity.LegalName": "legal_name",
    "Entity.LegalJurisdiction": "legal_jurisdiction",
    "Entity.LegalAddress.Country": "country",
    "Entity.LegalAddress.City": "city",
    "Entity.EntityStatus": "entity_status",
    "Entity.LegalForm.EntityLegalFormCode": "legal_form_code",
}

RR_COLUMNS = {
    "Relationship.StartNode.NodeID": "child_lei",
    "Relationship.EndNode.NodeID": "parent_lei",
    "Relationship.RelationshipType": "relationship_type",
    "Relationship.RelationshipStatus": "relationship_status",
}


def latest_golden_copy_urls(fetch_json: Optional[Callable[[str], dict]] = None) -> dict:
    fetch_json = fetch_json or (
        lambda url: httpx.get(url, timeout=60.0, follow_redirects=True).json()
    )
    payload = fetch_json(GOLDEN_COPY_API)
    latest = payload["data"][0]
    return {
        "lei2": latest["lei2"]["full_file"]["csv"]["url"],
        "rr": latest["rr"]["full_file"]["csv"]["url"],
    }


def _stream_filter(src, dest, usecols, rename, chunksize, row_filter) -> int:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    writer = None
    total = 0
    try:
        reader = pd.read_csv(
            src, usecols=usecols, chunksize=chunksize, dtype=str, low_memory=False
        )
        for chunk in reader:
            chunk = chunk.rename(columns=rename)
            chunk = row_filter(chunk)
            if chunk.empty:
                continue
            table = pa.Table.from_pandas(chunk, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(dest, table.schema)
            writer.write_table(table)
            total += len(chunk)
    finally:
        if writer is not None:
            writer.close()
    return total


def filter_lei_csv(src, dest, countries=frozenset({"US"}), chunksize: int = 100_000) -> int:
    def row_filter(chunk: pd.DataFrame) -> pd.DataFrame:
        if countries is None:
            return chunk
        return chunk[chunk["country"].isin(countries)]

    return _stream_filter(
        src, dest, list(LEI_COLUMNS), LEI_COLUMNS, chunksize, row_filter
    )


def filter_rr_csv(src, dest, keep_leis=None, chunksize: int = 100_000) -> int:
    def row_filter(chunk: pd.DataFrame) -> pd.DataFrame:
        chunk = chunk[chunk["relationship_status"] == "ACTIVE"]
        if keep_leis is not None:
            chunk = chunk[
                chunk["child_lei"].isin(keep_leis) & chunk["parent_lei"].isin(keep_leis)
            ]
        return chunk

    return _stream_filter(src, dest, list(RR_COLUMNS), RR_COLUMNS, chunksize, row_filter)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_gleif.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add src/kg/ingest/gleif.py tests/test_gleif.py tests/fixtures/gleif_lei_sample.csv tests/fixtures/gleif_rr_sample.csv
git commit -m "feat: streaming GLEIF level 1 and level 2 filters to parquet"
```

---

### Task 7: Structured parser

**Files:**
- Create: `src/kg/parse/structured.py`
- Test: `tests/test_parse_structured.py`

**Interfaces:**
- Consumes: `kg.parse.schema.Mention`, `EdgeMention`, `make_mention_id`; GLEIF Parquet from Task 6; ticker records from Task 5
- Produces:
  - `kg.parse.structured.parse_gleif_lei(parquet_path, source_doc, source_uri) -> tuple[list[Mention], list[EdgeMention]]`
  - `kg.parse.structured.parse_company_tickers(records, source_doc, source_uri) -> tuple[list[Mention], list[EdgeMention]]`
  - `EXTRACTOR_VERSION = "1"`

Each legal entity yields one `LegalEntity` mention plus one `Identifier` mention per identifier scheme, linked by `IDENTIFIED_BY` — the identifier-as-node decision from spec §4.3.

- [ ] **Step 1: Write the failing test**

Create `tests/test_parse_structured.py`:

```python
from pathlib import Path

from kg.ingest.gleif import filter_lei_csv
from kg.parse.structured import parse_company_tickers, parse_gleif_lei

FIXTURES = Path(__file__).parent / "fixtures"
DOC = "d" * 64


def test_parse_gleif_emits_entity_identifier_and_edge(tmp_path):
    parquet = tmp_path / "lei.parquet"
    filter_lei_csv(FIXTURES / "gleif_lei_sample.csv", parquet, countries={"US"})
    mentions, edges = parse_gleif_lei(parquet, DOC, "https://gleif/lei2.zip")

    entities = [m for m in mentions if m.mention_type == "LegalEntity"]
    identifiers = [m for m in mentions if m.mention_type == "Identifier"]
    assert len(entities) == 2
    assert len(identifiers) == 2
    assert {m.name for m in entities} == {"APPLE INC.", "ALPHABET INC."}

    apple = next(m for m in entities if m.name == "APPLE INC.")
    assert apple.attrs["legal_jurisdiction"] == "US-CA"
    assert apple.modality == "structured"
    assert apple.source_doc == DOC
    assert apple.confidence == 1.0

    assert all(e.edge_type == "IDENTIFIED_BY" for e in edges)
    assert len(edges) == 2
    apple_edge = next(e for e in edges if e.src_mention_id == apple.mention_id)
    lei_node = next(m for m in identifiers if m.mention_id == apple_edge.dst_mention_id)
    assert lei_node.attrs["scheme"] == "LEI"
    assert lei_node.name == "HWUPKR0MPOU8FGXBT394"


def test_parse_company_tickers_emits_two_identifiers_per_company():
    records = [{"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}]
    mentions, edges = parse_company_tickers(records, DOC, "https://sec/tickers.json")

    schemes = sorted(
        m.attrs["scheme"] for m in mentions if m.mention_type == "Identifier"
    )
    assert schemes == ["CIK", "TICKER"]
    cik_node = next(m for m in mentions if m.attrs.get("scheme") == "CIK")
    assert cik_node.name == "0000320193"
    assert len(edges) == 2


def test_mention_ids_are_stable_across_runs():
    records = [{"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}]
    first, _ = parse_company_tickers(records, DOC, "u")
    second, _ = parse_company_tickers(records, DOC, "u")
    assert [m.mention_id for m in first] == [m.mention_id for m in second]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_parse_structured.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kg.parse.structured'`

- [ ] **Step 3: Write `src/kg/parse/structured.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_parse_structured.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/kg/parse/structured.py tests/test_parse_structured.py
git commit -m "feat: structured parser for GLEIF LEI and SEC ticker records"
```

---

### Task 8: Semi-structured parser — XBRL facts and Exhibit 21

**Files:**
- Create: `src/kg/parse/semi.py`
- Create: `tests/fixtures/companyfacts_small.json`
- Create: `tests/fixtures/ex21_table.htm`
- Create: `tests/fixtures/ex21_freetext.htm`
- Test: `tests/test_parse_semi.py`

**Interfaces:**
- Consumes: `kg.parse.schema.Mention`, `EdgeMention`, `make_mention_id`
- Produces:
  - `kg.parse.semi.parse_companyfacts(facts, source_doc, source_uri, cik, tags=None) -> tuple[list[Mention], list[EdgeMention]]`
  - `kg.parse.semi.parse_exhibit21(html_bytes, source_doc, source_uri, parent_cik, parent_name) -> tuple[list[Mention], list[EdgeMention]]`
  - `kg.parse.semi.DEFAULT_TAGS = ("Revenues", "Assets", "NetIncomeLoss")`
  - `kg.parse.semi.EXTRACTOR_VERSION = "1"`

Exhibit 21 arrives in two shapes and both must work: a real HTML `<table>`, and an indented free-text blob inside `<p>` or `<pre>`. The parser tries the table path first and falls back to line parsing.

- [ ] **Step 1: Create the fixtures**

`tests/fixtures/companyfacts_small.json`:

```json
{"cik": 320193, "entityName": "Apple Inc.",
 "facts": {"us-gaap": {
   "Revenues": {"label": "Revenues", "units": {"USD": [
     {"start": "2022-10-01", "end": "2023-09-30", "val": 383285000000,
      "fy": 2023, "fp": "FY", "form": "10-K", "accn": "0000320193-23-000106"},
     {"start": "2021-10-01", "end": "2022-09-30", "val": 394328000000,
      "fy": 2022, "fp": "FY", "form": "10-K", "accn": "0000320193-22-000108"}]}},
   "Assets": {"label": "Assets", "units": {"USD": [
     {"start": "2023-09-30", "end": "2023-09-30", "val": 352583000000,
      "fy": 2023, "fp": "FY", "form": "10-K", "accn": "0000320193-23-000106"}]}},
   "GoodwillImpairmentLoss": {"label": "Goodwill", "units": {"USD": [
     {"start": "2023-01-01", "end": "2023-09-30", "val": 0,
      "fy": 2023, "fp": "FY", "form": "10-K", "accn": "0000320193-23-000106"}]}}}}}
```

`tests/fixtures/ex21_table.htm`:

```html
<html><body>
<p>Subsidiaries of the Registrant</p>
<table>
<tr><td>Name</td><td>Jurisdiction</td></tr>
<tr><td>Apple Operations International Limited</td><td>Ireland</td></tr>
<tr><td>Braeburn Capital, Inc.</td><td>Nevada</td></tr>
</table>
</body></html>
```

`tests/fixtures/ex21_freetext.htm`:

```html
<html><body>
<p>SUBSIDIARIES OF THE REGISTRANT</p>
<pre>
Alphabet Holdings LLC .................... Delaware
Google LLC ............................... Delaware
YouTube, LLC ............................. Delaware
</pre>
</body></html>
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_parse_semi.py`:

```python
import json
from pathlib import Path

from kg.parse.semi import parse_companyfacts, parse_exhibit21

FIXTURES = Path(__file__).parent / "fixtures"
DOC = "e" * 64


def test_parse_companyfacts_keeps_only_default_tags():
    facts = json.loads((FIXTURES / "companyfacts_small.json").read_text())
    mentions, edges = parse_companyfacts(facts, DOC, "https://sec/facts", cik="0000320193")

    values = [m for m in mentions if m.mention_type == "FinancialFact"]
    concepts = {m.name for m in mentions if m.mention_type == "XBRLConcept"}
    assert concepts == {"Revenues", "Assets"}
    assert len(values) == 3
    assert all(m.modality == "semi" for m in values)

    revenue_2023 = next(
        m for m in values
        if m.attrs["tag"] == "Revenues" and m.attrs["fy"] == 2023
    )
    assert revenue_2023.attrs["val"] == 383285000000
    assert revenue_2023.attrs["unit"] == "USD"
    assert revenue_2023.attrs["accn"] == "0000320193-23-000106"

    assert {e.edge_type for e in edges} == {"REPORTS"}
    assert len(edges) == 3


def test_parse_exhibit21_table_form():
    html = (FIXTURES / "ex21_table.htm").read_bytes()
    mentions, edges = parse_exhibit21(
        html, DOC, "https://sec/ex21", parent_cik="0000320193", parent_name="Apple Inc."
    )
    subs = [m for m in mentions if m.mention_type == "LegalEntity" and m.attrs.get("role") == "subsidiary"]
    assert {m.name for m in subs} == {
        "Apple Operations International Limited",
        "Braeburn Capital, Inc.",
    }
    braeburn = next(m for m in subs if m.name.startswith("Braeburn"))
    assert braeburn.attrs["jurisdiction_text"] == "Nevada"
    assert all(e.edge_type == "PARENT_OF" for e in edges)
    assert len(edges) == 2


def test_parse_exhibit21_freetext_form():
    html = (FIXTURES / "ex21_freetext.htm").read_bytes()
    mentions, edges = parse_exhibit21(
        html, DOC, "https://sec/ex21", parent_cik="0001652044", parent_name="Alphabet Inc."
    )
    subs = [m for m in mentions if m.attrs.get("role") == "subsidiary"]
    assert {m.name for m in subs} == {
        "Alphabet Holdings LLC",
        "Google LLC",
        "YouTube, LLC",
    }
    google = next(m for m in subs if m.name == "Google LLC")
    assert google.attrs["jurisdiction_text"] == "Delaware"
    assert len(edges) == 3


def test_parent_edge_points_from_parent_to_subsidiary():
    html = (FIXTURES / "ex21_table.htm").read_bytes()
    mentions, edges = parse_exhibit21(
        html, DOC, "https://sec/ex21", parent_cik="0000320193", parent_name="Apple Inc."
    )
    parent = next(m for m in mentions if m.attrs.get("role") == "parent")
    assert all(e.src_mention_id == parent.mention_id for e in edges)


def test_exhibit21_confidence_is_below_one():
    html = (FIXTURES / "ex21_freetext.htm").read_bytes()
    _, edges = parse_exhibit21(
        html, DOC, "u", parent_cik="0001652044", parent_name="Alphabet Inc."
    )
    assert all(0.0 < e.confidence < 1.0 for e in edges)
```

The last test encodes a real requirement: heuristically parsed edges must not claim the certainty that deterministic mappings do, or week 4's quality numbers become meaningless.

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_parse_semi.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kg.parse.semi'`

- [ ] **Step 4: Write `src/kg/parse/semi.py`**

```python
import re
from typing import Optional

from lxml import html as lxml_html

from kg.parse.schema import EdgeMention, Mention, make_mention_id

EXTRACTOR_VERSION = "1"
DEFAULT_TAGS = ("Revenues", "Assets", "NetIncomeLoss")
EX21_CONFIDENCE = 0.85

_DOTS = re.compile(r"\.{3,}|\s{3,}|\t+")
_HEADING = re.compile(r"subsidiar|jurisdiction|name of|state of|registrant", re.I)


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
                    local = f"{taxonomy}:{tag}:{unit}:{obs.get('accn')}:{obs.get('start')}:{obs.get('end')}"
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

    rows = [r for r in _rows_from_tables(tree) if not _HEADING.search(r[0])]
    if not rows:
        rows = _rows_from_text(tree)

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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_parse_semi.py -v`
Expected: 5 passed

- [ ] **Step 6: Check the parser against three real Exhibit 21 documents**

```bash
.venv/Scripts/python.exe -c "
from kg.config import load_settings
from kg.ingest.cache import RawCache
from kg.ingest.http import SecClient
from kg.ingest.edgar import *
from kg.parse.semi import parse_exhibit21
s = load_settings(); c = SecClient(s, RawCache(s.raw_dir))
for cik, name in [(320193,'Apple Inc.'), (1652044,'Alphabet Inc.'), (789019,'Microsoft Corp')]:
    _, sub = fetch_submissions(c, cik)
    f = recent_filings(sub, '10-K', 1)[0]
    _, idx = fetch_filing_index(c, cik, f['accession'])
    fn = find_exhibit21(idx)
    if not fn:
        print(name, 'no EX-21'); continue
    doc_id, content = c.get_bytes(filing_doc_url(cik, f['accession'], fn))
    m, e = parse_exhibit21(content, doc_id, 'x', str(cik), name)
    print(name, 'subsidiaries:', len(e))
    for s2 in m[1:4]:
        print('   ', s2.name, '|', s2.attrs['jurisdiction_text'])
"
```

Expected: each company yields a non-zero subsidiary count with plausible names and jurisdictions. If a count is 0 or the names are obviously junk (page headers, footnote markers), record the failing layout as a new fixture and extend `_rows_from_text`. Do not move on with a silently empty parse.

- [ ] **Step 7: Commit**

```bash
git add src/kg/parse/semi.py tests/test_parse_semi.py tests/fixtures/companyfacts_small.json tests/fixtures/ex21_table.htm tests/fixtures/ex21_freetext.htm
git commit -m "feat: semi-structured parser for XBRL facts and exhibit 21"
```

---

### Task 9: Unstructured parser via LLM

**Files:**
- Create: `src/kg/parse/unstructured.py`
- Create: `tests/fixtures/item1_excerpt.txt`
- Test: `tests/test_parse_unstructured.py`

**Interfaces:**
- Consumes: `kg.parse.schema.Mention`, `EdgeMention`, `make_mention_id`
- Produces:
  - `kg.parse.unstructured.extract_text(html_bytes) -> str`
  - `kg.parse.unstructured.chunk_text(text, size=6000, overlap=200) -> list[tuple[int, str]]` — returns `(char_offset, chunk)` pairs
  - `kg.parse.unstructured.EXTRACTION_PROMPT` — the instruction string
  - `kg.parse.unstructured.parse_narrative(text, source_doc, source_uri, subject_name, call_llm) -> tuple[list[Mention], list[EdgeMention]]` where `call_llm: Callable[[str], list[dict]]` is injected, keeping tests offline
  - `kg.parse.unstructured.build_llm_caller(settings, cache_dir) -> Callable[[str], list[dict]]` — real Anthropic caller, cached by prompt hash

Requires `pip install anthropic` and `ANTHROPIC_API_KEY` in the environment. The injected `call_llm` seam means every test here runs without a key.

- [ ] **Step 1: Create the fixture**

`tests/fixtures/item1_excerpt.txt`:

```text
In March 2023, Alphabet Inc. acquired Photomath, a mobile education company.
Sundar Pichai serves as Chief Executive Officer of Alphabet Inc.
The Company competes with Microsoft Corporation and Amazon.com, Inc. in cloud services.
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_parse_unstructured.py`:

```python
from pathlib import Path

from kg.parse.unstructured import chunk_text, extract_text, parse_narrative

FIXTURES = Path(__file__).parent / "fixtures"
DOC = "f" * 64


def fake_llm(prompt: str) -> list:
    return [
        {"subject": "Alphabet Inc.", "predicate": "ACQUIRED", "object": "Photomath",
         "object_type": "LegalEntity", "evidence": "acquired Photomath", "confidence": 0.9},
        {"subject": "Sundar Pichai", "predicate": "OFFICER_OF", "object": "Alphabet Inc.",
         "object_type": "LegalEntity", "evidence": "serves as Chief Executive Officer",
         "confidence": 0.95},
        {"subject": "Alphabet Inc.", "predicate": "DESTROYED", "object": "Mars",
         "object_type": "LegalEntity", "evidence": "nonsense", "confidence": 0.99},
    ]


def test_extract_text_strips_markup():
    text = extract_text(b"<html><body><p>Hello</p><script>bad()</script><p>World</p></body></html>")
    assert "Hello" in text
    assert "World" in text
    assert "bad()" not in text


def test_chunk_text_overlaps_and_reports_offsets():
    chunks = chunk_text("abcdefghij" * 10, size=40, overlap=10)
    assert chunks[0][0] == 0
    assert chunks[1][0] == 30
    assert all(len(c) <= 40 for _, c in chunks)


def test_parse_narrative_drops_unknown_predicates():
    text = (FIXTURES / "item1_excerpt.txt").read_text()
    mentions, edges = parse_narrative(text, DOC, "https://sec/10k", "Alphabet Inc.", fake_llm)
    assert {e.edge_type for e in edges} == {"ACQUIRED", "OFFICER_OF"}
    assert len(edges) == 2


def test_parse_narrative_carries_evidence_and_llm_confidence():
    text = (FIXTURES / "item1_excerpt.txt").read_text()
    _, edges = parse_narrative(text, DOC, "u", "Alphabet Inc.", fake_llm)
    acquired = next(e for e in edges if e.edge_type == "ACQUIRED")
    assert acquired.attrs["evidence"] == "acquired Photomath"
    assert acquired.confidence == 0.9
    assert acquired.modality == "unstructured"


def test_person_subject_becomes_person_mention():
    text = (FIXTURES / "item1_excerpt.txt").read_text()
    mentions, _ = parse_narrative(text, DOC, "u", "Alphabet Inc.", fake_llm)
    pichai = next(m for m in mentions if m.name == "Sundar Pichai")
    assert pichai.mention_type == "Person"
```

The third test is the important one. An LLM will occasionally emit a predicate outside your ontology, and a parser that accepts it corrupts the graph schema silently.

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_parse_unstructured.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kg.parse.unstructured'`

- [ ] **Step 4: Write `src/kg/parse/unstructured.py`**

```python
import hashlib
import json
import os
from pathlib import Path
from typing import Callable

from lxml import html as lxml_html

from kg.parse.schema import EdgeMention, Mention, make_mention_id

EXTRACTOR_VERSION = "1"
ALLOWED_PREDICATES = {"ACQUIRED", "OFFICER_OF", "COMPETES_WITH"}

EXTRACTION_PROMPT = """Extract relationships from this SEC filing excerpt.

Return ONLY a JSON array. Each element must have exactly these keys:
  subject        the acting entity, as written in the text
  predicate      one of: ACQUIRED, OFFICER_OF, COMPETES_WITH
  object         the other entity, as written in the text
  object_type    LegalEntity or Person
  evidence       the exact span of text supporting this, under 200 characters
  confidence     your confidence from 0.0 to 1.0

Rules:
- Use ONLY the three predicates listed. Emit nothing for other relationships.
- Do not infer. If the text does not state it, do not extract it.
- Return [] if nothing matches.

Text:
---
{text}
---"""


def extract_text(html_bytes: bytes) -> str:
    tree = lxml_html.fromstring(html_bytes)
    for bad in tree.xpath("//script|//style"):
        bad.getparent().remove(bad)
    return "\n".join(
        line.strip() for line in tree.text_content().splitlines() if line.strip()
    )


def chunk_text(text: str, size: int = 6000, overlap: int = 200) -> list:
    chunks, start = [], 0
    step = size - overlap
    while start < len(text):
        chunks.append((start, text[start : start + size]))
        start += step
    return chunks


def _mention_type_for(name: str, declared: str) -> str:
    return "Person" if declared == "Person" else "LegalEntity"


def parse_narrative(
    text: str,
    source_doc: str,
    source_uri: str,
    subject_name: str,
    call_llm: Callable[[str], list],
):
    extractor = "llm_narrative"
    mentions_by_key: dict = {}
    edges = []

    def get_or_make(name: str, mention_type: str, offset: int) -> Mention:
        key = (name, mention_type)
        if key not in mentions_by_key:
            mentions_by_key[key] = Mention(
                mention_id=make_mention_id(source_doc, extractor, f"{mention_type}:{name}"),
                mention_type=mention_type,
                name=name,
                attrs={},
                source_doc=source_doc,
                source_uri=source_uri,
                char_offset=offset,
                extractor=extractor,
                extractor_version=EXTRACTOR_VERSION,
                confidence=0.7,
                modality="unstructured",
            )
        return mentions_by_key[key]

    for offset, chunk in chunk_text(text):
        for triple in call_llm(EXTRACTION_PROMPT.format(text=chunk)):
            predicate = triple.get("predicate")
            if predicate not in ALLOWED_PREDICATES:
                continue
            subject_type = "Person" if predicate == "OFFICER_OF" else "LegalEntity"
            src = get_or_make(triple["subject"], subject_type, offset)
            dst = get_or_make(
                triple["object"],
                _mention_type_for(triple["object"], triple.get("object_type", "LegalEntity")),
                offset,
            )
            local = f"{src.mention_id}:{predicate}:{dst.mention_id}"
            edges.append(
                EdgeMention(
                    edge_id=make_mention_id(source_doc, extractor, local),
                    src_mention_id=src.mention_id,
                    dst_mention_id=dst.mention_id,
                    edge_type=predicate,
                    attrs={"evidence": triple.get("evidence", "")},
                    source_doc=source_doc,
                    char_offset=offset,
                    extractor=extractor,
                    extractor_version=EXTRACTOR_VERSION,
                    confidence=float(triple.get("confidence", 0.5)),
                    modality="unstructured",
                )
            )
    return list(mentions_by_key.values()), edges


def build_llm_caller(settings, cache_dir: Path) -> Callable[[str], list]:
    import anthropic

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def call(prompt: str) -> list:
        key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        cached = cache_dir / f"{key}.json"
        if cached.exists():
            return json.loads(cached.read_text(encoding="utf-8"))
        response = client.messages.create(
            model=settings.llm_model,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        try:
            triples = json.loads(raw)
        except json.JSONDecodeError:
            triples = []
        cached.write_text(json.dumps(triples), encoding="utf-8")
        return triples

    return call
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_parse_unstructured.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add src/kg/parse/unstructured.py tests/test_parse_unstructured.py tests/fixtures/item1_excerpt.txt
git commit -m "feat: unstructured parser with injectable LLM caller and predicate whitelist"
```

---

### Task 10: Batched Neo4j mention loader with thin schema

**Files:**
- Create: `ontology/constraints_thin.cypher`
- Create: `src/kg/load/neo4j_writer.py`
- Test: `tests/test_neo4j_writer.py`

**Interfaces:**
- Consumes: `get_driver`, `mentions.parquet` and `edge_mentions.parquet` from Tasks 7–9
- Produces:
  - `kg.load.neo4j_writer.apply_constraints(driver, cypher_path) -> int`
  - `kg.load.neo4j_writer.load_mentions(driver, parquet_path, batch_size=5000) -> int`
  - `kg.load.neo4j_writer.load_edges(driver, parquet_path, batch_size=5000) -> int`
  - `kg.load.neo4j_writer.clear_graph(driver) -> None`

Loads use `UNWIND $rows` with a parameter list, never one `MERGE` per row. Task 10's batch size is the knob week 4's scalability benchmark sweeps.

- [ ] **Step 1: Write `ontology/constraints_thin.cypher`**

```cypher
CREATE CONSTRAINT mention_id_unique IF NOT EXISTS
FOR (m:Mention) REQUIRE m.mention_id IS UNIQUE;

CREATE CONSTRAINT mention_source_exists IF NOT EXISTS
FOR (m:Mention) REQUIRE m.source_doc IS NOT NULL;

CREATE CONSTRAINT mention_extractor_exists IF NOT EXISTS
FOR (m:Mention) REQUIRE m.extractor IS NOT NULL;

CREATE INDEX mention_type_idx IF NOT EXISTS
FOR (m:Mention) ON (m.mention_type);

CREATE INDEX mention_name_idx IF NOT EXISTS
FOR (m:Mention) ON (m.name);
```

This is deliberately thin. The generated `constraints.cypher` from the OWL ontology replaces it in week 2.

- [ ] **Step 2: Write the failing test**

Create `tests/test_neo4j_writer.py`:

```python
import json
from pathlib import Path

import pytest

from kg.config import load_settings
from kg.load.neo4j_conn import get_driver
from kg.load.neo4j_writer import (
    apply_constraints,
    clear_graph,
    load_edges,
    load_mentions,
)
from kg.parse.schema import EdgeMention, Mention, make_mention_id, write_edges, write_mentions

pytestmark = pytest.mark.integration

DOC = "c" * 64


@pytest.fixture
def driver():
    d = get_driver(load_settings())
    clear_graph(d)
    yield d
    clear_graph(d)
    d.close()


def make_pair(tmp_path):
    parent = Mention(
        mention_id=make_mention_id(DOC, "t", "parent"), mention_type="LegalEntity",
        name="Apple Inc.", attrs={"cik": "0000320193"}, source_doc=DOC,
        source_uri="u", char_offset=None, extractor="t", extractor_version="1",
        confidence=1.0, modality="structured",
    )
    child = Mention(
        mention_id=make_mention_id(DOC, "t", "child"), mention_type="LegalEntity",
        name="Braeburn Capital, Inc.", attrs={}, source_doc=DOC,
        source_uri="u", char_offset=None, extractor="t", extractor_version="1",
        confidence=0.85, modality="semi",
    )
    edge = EdgeMention(
        edge_id="edge1", src_mention_id=parent.mention_id,
        dst_mention_id=child.mention_id, edge_type="PARENT_OF", attrs={},
        source_doc=DOC, char_offset=None, extractor="t", extractor_version="1",
        confidence=0.85, modality="semi",
    )
    m_path = write_mentions([parent, child], tmp_path / "m.parquet")
    e_path = write_edges([edge], tmp_path / "e.parquet")
    return m_path, e_path


def test_load_mentions_and_edges(driver, tmp_path):
    apply_constraints(driver, Path("ontology/constraints_thin.cypher"))
    m_path, e_path = make_pair(tmp_path)
    assert load_mentions(driver, m_path) == 2
    assert load_edges(driver, e_path) == 1

    with driver.session() as session:
        count = session.run("MATCH (m:Mention) RETURN count(m) AS c").single()["c"]
        rel = session.run(
            "MATCH (:Mention)-[r:PARENT_OF]->(:Mention) RETURN count(r) AS c"
        ).single()["c"]
        apple = session.run(
            "MATCH (m:Mention {name:'Apple Inc.'}) RETURN m.attrs AS a, m.modality AS mo"
        ).single()
    assert count == 2
    assert rel == 1
    assert json.loads(apple["a"])["cik"] == "0000320193"
    assert apple["mo"] == "structured"


def test_loading_twice_is_idempotent(driver, tmp_path):
    apply_constraints(driver, Path("ontology/constraints_thin.cypher"))
    m_path, e_path = make_pair(tmp_path)
    load_mentions(driver, m_path)
    load_mentions(driver, m_path)
    load_edges(driver, e_path)
    load_edges(driver, e_path)
    with driver.session() as session:
        nodes = session.run("MATCH (m:Mention) RETURN count(m) AS c").single()["c"]
        rels = session.run("MATCH ()-[r:PARENT_OF]->() RETURN count(r) AS c").single()["c"]
    assert nodes == 2
    assert rels == 1
```

Idempotency matters because you will re-run the loader many times this week, and a loader that duplicates on re-run makes every later count wrong.

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_neo4j_writer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kg.load.neo4j_writer'`

- [ ] **Step 4: Write `src/kg/load/neo4j_writer.py`**

```python
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


def load_mentions(driver: Driver, parquet_path, batch_size: int = 5000) -> int:
    df = pd.read_parquet(parquet_path)
    df = df.astype(object).where(pd.notna(df), None)
    total = 0
    with driver.session() as session:
        for rows in _batches(df, batch_size):
            session.run(MENTION_QUERY, rows=rows)
            total += len(rows)
    return total


def load_edges(driver: Driver, parquet_path, batch_size: int = 5000) -> int:
    df = pd.read_parquet(parquet_path)
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
```

The `EDGE_TYPES` check before string-interpolating a relationship type is what keeps that interpolation safe — Cypher cannot parameterize relationship types, so the whitelist is the guard.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_neo4j_writer.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add ontology/constraints_thin.cypher src/kg/load/neo4j_writer.py tests/test_neo4j_writer.py
git commit -m "feat: batched idempotent neo4j mention and edge loader"
```

---

### Task 11: CLI and end-to-end pipeline run

**Files:**
- Create: `src/kg/cli.py`
- Create: `README.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above
- Produces: `kg.cli.app` (typer app) with commands `check`, `ingest-sec`, `ingest-gleif`, `parse`, `load`, `stats`, `run-all`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli.py`:

```python
from typer.testing import CliRunner

from kg.cli import app

runner = CliRunner()


def test_cli_exposes_expected_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ["check", "ingest-sec", "ingest-gleif", "parse", "load", "stats", "run-all"]:
        assert command in result.stdout


def test_parse_command_help_documents_limit():
    result = runner.invoke(app, ["ingest-sec", "--help"])
    assert result.exit_code == 0
    assert "--limit" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kg.cli'`

- [ ] **Step 3: Write `src/kg/cli.py`**

```python
import json
from pathlib import Path

import typer

from kg.config import load_settings
from kg.ingest.cache import RawCache
from kg.ingest.edgar import (
    fetch_company_tickers,
    fetch_companyfacts,
    fetch_filing_index,
    fetch_submissions,
    filing_doc_url,
    find_exhibit21,
    recent_filings,
)
from kg.ingest.http import SecClient
from kg.load.neo4j_conn import check_connection, get_driver
from kg.load.neo4j_writer import apply_constraints, clear_graph, load_edges, load_mentions
from kg.parse.schema import write_edges, write_mentions
from kg.parse.semi import parse_companyfacts, parse_exhibit21
from kg.parse.structured import parse_company_tickers, parse_gleif_lei

app = typer.Typer(help="Enterprise KG construction pipeline")


def _client(settings):
    return SecClient(settings, RawCache(settings.raw_dir))


@app.command()
def check():
    """Verify config, data root, and Neo4j connectivity."""
    settings = load_settings()
    typer.echo(f"data_root: {settings.data_root}")
    driver = get_driver(settings)
    try:
        typer.echo(json.dumps(check_connection(driver), indent=2))
    finally:
        driver.close()


@app.command(name="ingest-sec")
def ingest_sec(limit: int = typer.Option(25, help="number of companies to ingest")):
    """Fetch tickers, submissions, companyfacts, and Exhibit 21 for N companies."""
    settings = load_settings()
    client = _client(settings)
    doc_id, records = fetch_company_tickers(client)
    (settings.staging_dir / "tickers_doc_id.txt").write_text(doc_id)
    selected = records[:limit]
    manifest = []
    for rec in selected:
        cik = rec["cik_str"]
        facts_doc, _ = fetch_companyfacts(client, cik)
        _, submissions = fetch_submissions(client, cik)
        entry = {"cik": cik, "title": rec["title"], "facts_doc": facts_doc, "ex21": None}
        filings = recent_filings(submissions, "10-K", 1)
        if filings:
            _, index = fetch_filing_index(client, cik, filings[0]["accession"])
            filename = find_exhibit21(index)
            if filename:
                url = filing_doc_url(cik, filings[0]["accession"], filename)
                ex21_doc, _ = client.get_bytes(url)
                entry["ex21"] = {"doc_id": ex21_doc, "url": url}
        manifest.append(entry)
        typer.echo(f"{rec['title']}: ex21={'yes' if entry['ex21'] else 'no'}")
    (settings.staging_dir / "sec_manifest.json").write_text(json.dumps(manifest, indent=2))
    typer.echo(f"ingested {len(manifest)} companies")


@app.command(name="ingest-gleif")
def ingest_gleif(
    lei_csv: Path = typer.Option(..., help="path to unzipped GLEIF level 1 CSV"),
    rr_csv: Path = typer.Option(None, help="path to unzipped GLEIF level 2 CSV"),
):
    """Stream-filter GLEIF CSVs to US-only Parquet."""
    from kg.ingest.gleif import filter_lei_csv, filter_rr_csv

    settings = load_settings()
    dest = settings.staging_dir / "gleif_lei.parquet"
    n = filter_lei_csv(lei_csv, dest, countries={"US"})
    typer.echo(f"lei rows: {n} -> {dest}")
    if rr_csv:
        import pandas as pd

        leis = set(pd.read_parquet(dest, columns=["lei"])["lei"])
        rr_dest = settings.gold_dir / "gleif_rr.parquet"
        m = filter_rr_csv(rr_csv, rr_dest, keep_leis=leis)
        typer.echo(f"relationship rows: {m} -> {rr_dest}")


@app.command()
def parse():
    """Run all three parsers into mentions.parquet and edge_mentions.parquet."""
    settings = load_settings()
    cache = RawCache(settings.raw_dir)
    mentions, edges = [], []

    gleif_parquet = settings.staging_dir / "gleif_lei.parquet"
    if gleif_parquet.exists():
        doc = "gleif_lei_golden_copy"
        m, e = parse_gleif_lei(gleif_parquet, doc.ljust(64, "0"), str(gleif_parquet))
        mentions += m
        edges += e
        typer.echo(f"structured/gleif: {len(m)} mentions")

    tickers_doc_file = settings.staging_dir / "tickers_doc_id.txt"
    if tickers_doc_file.exists():
        doc_id = tickers_doc_file.read_text().strip()
        records = list(json.loads(cache.get(doc_id)).values())
        m, e = parse_company_tickers(records, doc_id, "https://www.sec.gov/files/company_tickers.json")
        mentions += m
        edges += e
        typer.echo(f"structured/tickers: {len(m)} mentions")

    manifest_file = settings.staging_dir / "sec_manifest.json"
    if manifest_file.exists():
        for entry in json.loads(manifest_file.read_text()):
            facts = json.loads(cache.get(entry["facts_doc"]))
            m, e = parse_companyfacts(
                facts, entry["facts_doc"], "companyfacts", str(entry["cik"]).zfill(10)
            )
            mentions += m
            edges += e
            if entry["ex21"]:
                m, e = parse_exhibit21(
                    cache.get(entry["ex21"]["doc_id"]),
                    entry["ex21"]["doc_id"],
                    entry["ex21"]["url"],
                    str(entry["cik"]).zfill(10),
                    entry["title"],
                )
                mentions += m
                edges += e
        typer.echo(f"semi: cumulative {len(mentions)} mentions")

    m_path = write_mentions(mentions, settings.staging_dir / "mentions.parquet")
    e_path = write_edges(edges, settings.staging_dir / "edge_mentions.parquet")
    typer.echo(f"wrote {len(mentions)} mentions -> {m_path}")
    typer.echo(f"wrote {len(edges)} edges -> {e_path}")


@app.command()
def load(reset: bool = typer.Option(False, help="delete all nodes first")):
    """Apply constraints and load Parquet into Neo4j."""
    settings = load_settings()
    driver = get_driver(settings)
    try:
        if reset:
            clear_graph(driver)
        apply_constraints(driver, Path("ontology/constraints_thin.cypher"))
        n = load_mentions(driver, settings.staging_dir / "mentions.parquet")
        m = load_edges(driver, settings.staging_dir / "edge_mentions.parquet")
        typer.echo(f"loaded {n} mentions, {m} edges")
    finally:
        driver.close()


@app.command()
def stats():
    """Print node and relationship counts by type."""
    settings = load_settings()
    driver = get_driver(settings)
    try:
        with driver.session() as session:
            rows = session.run(
                "MATCH (m:Mention) RETURN m.mention_type AS t, m.modality AS mo, "
                "count(*) AS c ORDER BY c DESC"
            ).data()
            rels = session.run(
                "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY c DESC"
            ).data()
        typer.echo("mentions:")
        for r in rows:
            typer.echo(f"  {r['t']:<16} {r['mo']:<14} {r['c']}")
        typer.echo("edges:")
        for r in rels:
            typer.echo(f"  {r['t']:<16} {r['c']}")
    finally:
        driver.close()


@app.command(name="run-all")
def run_all(limit: int = 25):
    """ingest-sec, parse, load, stats."""
    ingest_sec(limit=limit)
    parse()
    load(reset=True)
    stats()


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli.py -v`
Expected: 2 passed

- [ ] **Step 5: Run the full pipeline for real**

```bash
.venv/Scripts/python.exe -m kg.cli check
.venv/Scripts/python.exe -m kg.cli run-all --limit 25
```

Expected: `check` shows Neo4j 5.x with `n10s_available: true`. `run-all` finishes and `stats` prints non-zero counts for `LegalEntity`, `Identifier`, `FinancialFact`, `XBRLConcept` across `structured` and `semi` modalities, plus `PARENT_OF`, `IDENTIFIED_BY`, `REPORTS` edges.

Sanity-check one competency question in Neo4j Browser at http://localhost:7474:

```cypher
MATCH (p:Mention)-[:PARENT_OF]->(s:Mention)
RETURN p.name AS parent, collect(s.name)[0..5] AS subsidiaries, count(s) AS n
ORDER BY n DESC LIMIT 10;
```

Expected: recognizable parent companies with plausible subsidiary lists. This is spec competency question 1 answerable end to end, which is the week 1 exit criterion.

- [ ] **Step 6: Write `README.md`**

```markdown
# Enterprise Knowledge Graph — SEC EDGAR + GLEIF

Ontology-driven KG construction over structured, semi-structured, and
unstructured enterprise data.

## Setup

1. Install Docker Desktop and start it.
2. `python -m venv .venv && .venv/Scripts/python.exe -m pip install -e .`
3. `cp config/settings.yaml.example config/settings.yaml` and set
   `sec_user_agent` to `YourName your.email@example.com` — SEC rejects
   requests without a contact email.
4. `docker compose up -d` (first boot downloads the n10s and apoc plugins)
5. `.venv/Scripts/python.exe -m kg.cli check`

## Run

```bash
python -m kg.cli run-all --limit 25
python -m kg.cli stats
```

Bulk data lives at `C:\kg-data\`, outside OneDrive. Raw fetches are
content-addressed by SHA-256, so re-runs hit the cache instead of the
network.

## Layout

- `src/kg/ingest/` — EDGAR and GLEIF fetchers, content-hash cache
- `src/kg/parse/` — three modality parsers, one uniform output schema
- `src/kg/load/` — batched Neo4j writer
- `ontology/` — schema constraints; OWL ontology lands in week 2
- `docs/superpowers/specs/` — design spec
- `docs/superpowers/plans/` — implementation plans

## Tests

```bash
python -m pytest -m "not integration"   # offline, no Neo4j needed
python -m pytest                        # requires docker compose up
```
```

- [ ] **Step 7: Commit**

```bash
git add src/kg/cli.py README.md tests/test_cli.py
git commit -m "feat: typer CLI and end-to-end pipeline run"
```

---

## Week 1 exit criteria

- `python -m pytest -m "not integration"` — all green with no network access
- `python -m pytest` — all green with Docker running
- `python -m kg.cli stats` reports non-zero mentions in all three modalities (unstructured requires `ANTHROPIC_API_KEY` and is wired in Task 9 but not called by `run-all`; add it once a key is present)
- The `PARENT_OF` Cypher query returns recognizable parent/subsidiary structure
- `C:\kg-data\raw\manifest.jsonl` exists and every mention's `source_doc` appears in it

## What week 2 inherits

Real data in Neo4j, and evidence about which classes and attributes actually populate. That evidence drives the Protégé ontology, per the data-first decision in spec §2. The thin `constraints_thin.cypher` gets replaced by the OWL-generated `constraints.cypher`.
