# Enterprise Knowledge Graph — SEC EDGAR

Turns US public-company filings into a queryable graph with a formal ontology
behind it and a source citation on every fact.

**Current state:** 13,800 nodes, 13,656 relationships, from 25 companies.
100% of facts trace back to the exact SEC document they came from.

---

## Table of contents

1. [What this actually does](#1-what-this-actually-does)
2. [The five commands](#2-the-five-commands)
3. [Setup from scratch](#3-setup-from-scratch)
4. [How it was built, step by step](#4-how-it-was-built-step-by-step)
5. [Every file explained](#5-every-file-explained)
6. [Command reference](#6-command-reference)
7. [When you get new data](#7-when-you-get-new-data)
8. [What is not built yet](#8-what-is-not-built-yet)

---

## 1. What this actually does

Companies file reports with the SEC. Useful facts are in them, but scattered
across three different formats and unusable as data.

This project reads those filings and produces a graph you can query.

```
SEC filings  ──▶  three readers  ──▶  one common format  ──▶  Neo4j graph
                                                                   │
                                        ontology (the rulebook) ───┘
```

Example of what becomes possible:

| Question | Answer |
|---|---|
| How many legal entities does Tesla own? | 415 |
| Johnson & Johnson? | 397 |
| Where did that fact come from? | a clickable link to the SEC document |

That first answer was buried inside one HTML table in one 200-page filing.

### Ontology vs knowledge graph

Two different things, and the distinction matters:

| | Holds | Where you see it |
|---|---|---|
| **Ontology** | the *rules* — what kinds of thing exist, how they connect | Protégé |
| **Knowledge graph** | the *data* — 13,800 actual facts | Neo4j |

Grammar versus sentences. Blueprint versus building.

---

## 2. The five commands

Everything else in this repo is machinery serving these.

```bash
python -m kg.cli check                 # is everything wired up
python -m kg.cli run-all               # build the graph  (the main one)
python -m kg.cli stats                 # what is in the graph
python -m kg.cli validate              # is the graph still valid
python -m kg.cli build-ontology        # rebuild ontology files after editing
```

Plus the graph browser at **http://localhost:7474** (`neo4j` / `changeme_kg_local`).

### Reading a command

```
python -m kg.cli build-ontology
  │      │   │       └── the command
  │      │   └────────── the module: src/kg/cli.py
  │      └────────────── "-m" means run a module, not a file path
  └───────────────────── run Python
```

`kg.cli` is a path with dots instead of slashes. Python knows where `kg` lives
because `pip install -e .` was run once during setup.

---

## 3. Setup from scratch

On a fresh machine, in order:

**Requirements:** Python 3.10+, Docker Desktop (running).
**Optional:** Protégé, to view the ontology visually.

**No network access or SEC account is needed.** All source data ships with the
repo in `data/samples/` (3.3 MB, 25 companies).

### Windows (PowerShell)

```powershell
# 1. Python environment and dependencies
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .

# 2. Copy the config template (defaults work as-is)
copy config\settings.yaml.example config\settings.yaml

# 3. Start the graph database (first boot downloads plugins, ~1 min)
docker compose up -d

# 4. Verify - should print neo4j_version and n10s_available: true
.\.venv\Scripts\python.exe -m kg.cli check

# 5. Build the whole graph from data/samples/  (~1 min, no network)
.\.venv\Scripts\python.exe -m kg.cli run-all
```

### macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
cp config/settings.yaml.example config/settings.yaml
docker compose up -d
.venv/bin/python -m kg.cli check
.venv/bin/python -m kg.cli run-all
```

The `docker-compose.yml` mounts `C:/kg-data/neo4j` — change those two volume
paths on non-Windows.

### The bundled data

```
data/samples/
  index.json                    which files belong to which company
  structured/
    company_tickers.json        25 companies, names and tickers
  semi/
    xbrl/*.json                 financial facts, trimmed to the 3 tags used
    exhibit21/*.htm             raw subsidiary lists, exactly as SEC published
  unstructured/                 empty - the prose reader is not built yet
```

The Exhibit 21 files are untouched originals, messy HTML and all. The XBRL
files are trimmed from 94 MB to 1.9 MB by keeping only the tags the parser
reads; nothing else is altered.

### Expected output from step 5

```
structured/tickers:     165 mentions
semi/xbrl:            11724 mentions
semi/exhibit21:        1952 ownership edges
loaded 13864 mentions, 13690 edges
mentions:
  FinancialFact    semi           11628
  LegalEntity      semi           1996
  Identifier       structured     80
  XBRLConcept      semi           71
  LegalEntity      structured     25
edges:
  REPORTS          11628
  PARENT_OF        1948
  IDENTIFIED_BY    80
```

Then open **http://localhost:7474** (`neo4j` / `changeme_kg_local`) and run:

```cypher
MATCH (p:Mention)-[:PARENT_OF]->(s:Mention)
WHERE p.name CONTAINS 'Apple'
RETURN p, s
```

### Troubleshooting

| Symptom | Cause |
|---|---|
| `FileNotFoundError: data/samples` | Run commands from the project root folder |
| `ServiceUnavailable` on bolt | Docker not running, or Neo4j still booting (wait ~60s) |
| `FileNotFoundError: config/settings.yaml` | Step 2 was skipped |
| `ConstraintCreationFailed ... Enterprise Edition` | You are on Neo4j Enterprise-only syntax; this repo targets Community |

Bulk data is written to `C:\kg-data\`, deliberately outside OneDrive so
gigabytes of Parquet and database files are not synced to the cloud.

---

## 4. How it was built, step by step

Each step solved one problem, in this order.

> **Note on steps 1 and 3.** The project originally downloaded from SEC on
> every run. It now ships the source files in `data/samples/` and reads them
> directly, so `ingest/cache.py`, `ingest/http.py`, and `ingest/edgar.py` were
> deleted once the data was captured. They are described below because the
> bundled files came from them, and because the EDGAR bug in step 5 is worth
> recording. `ingest/local.py` replaced all three.

### Step 1 — Configuration

**Problem:** the SEC rejects automated requests that do not identify a contact
email, and file paths differ per machine.

**Built:** `src/kg/config.py`. Reads `config/settings.yaml`, validates it, and
refuses to start if the User-Agent has no email in it — failing early beats a
confusing 403 later.

### Step 2 — The graph database

**Problem:** need somewhere to put a graph.

**Built:** `docker-compose.yml`, `src/kg/load/neo4j_conn.py`. Neo4j 5.26 in
Docker with two plugins: `n10s` (RDF and SHACL support) and `apoc` (utilities).

### Step 3 — Downloading, without abuse

**Problem:** the SEC bans clients exceeding 10 requests/second, and
re-downloading gigabytes on every run is unacceptable.

**Built:**

- `src/kg/ingest/cache.py` — stores every download under the SHA-256 hash of
  its content. Same bytes fetched from two URLs are stored once. Nothing is
  ever overwritten, so a source document is permanent evidence.
- `src/kg/ingest/http.py` — caps at 8 requests/second, checks the cache before
  every request.

**Effect:** first run takes minutes, every later run takes ~2 seconds.

### Step 4 — One format for everything

**Problem:** three readers producing three different shapes would need three
of everything downstream.

**Built:** `src/kg/parse/schema.py`. Two record types, `Mention` and
`EdgeMention`, that all readers emit. Every record carries `source_doc`,
`extractor`, `extractor_version`, `confidence`, `modality`.

This is the keystone. Because all readers write the same shape, entity
resolution never needs to know which format a fact came from — that is what
makes the multi-format claim structurally true rather than three parallel
pipelines.

### Step 5 — Fetching from EDGAR

**Built:** `src/kg/ingest/edgar.py`. Company tickers, submission history, XBRL
financial facts, and Exhibit 21.

**Bug found here, worth recording:** EDGAR's `index.json` has a `type` field
that looks like it holds the document type. It does not — it holds an *icon
filename* (`text.gif`). Exhibit 21 lookup silently returned nothing for every
filing. Since Exhibit 21 is the source of all ownership data, this would have
produced an empty result that looked like a legitimate one. Fixed by matching
filenames and by reading `index-headers.html`, which carries the real types.
Verified against Apple, Alphabet, and Microsoft.

### Step 6 — Reading tables

**Built:** `src/kg/parse/structured.py`. Straightforward column-to-property
mapping for ticker lists and (when ingested) the GLEIF registry.

One modelling decision: identifiers are **nodes, not properties**. A property
forces you to pick one true identifier per company. A node lets conflicting
claims coexist until entity resolution decides. Conflicting identifiers are
normal in enterprise data, not exceptional.

### Step 7 — Reading semi-structured documents

**Built:** `src/kg/parse/semi.py`. Two readers:

- **XBRL financial facts** from `companyfacts` JSON — clean, deterministic.
- **Exhibit 21**, the subsidiary list. Every company formats this differently.
  Sometimes a real HTML table, sometimes indented free text. Both handled.

Exhibit 21 facts get `confidence: 0.85` rather than `1.0`, because they come
from heuristics rather than a schema. Being honest about that is what lets you
review the uncertain 15% instead of everything.

**Bug found:** Microsoft's table header row was extracted as a company named
"Name" in a jurisdiction called "Where Incorporated". Fixed with a header
filter.

### Step 8 — Loading the graph

**Built:** `src/kg/load/neo4j_writer.py`. Writes in batches of 5,000 using
`UNWIND` rather than one query per row — orders of magnitude faster, and the
batch size is the knob a scalability analysis would sweep.

**Bug found:** property existence constraints (`REQUIRE x IS NOT NULL`) are
Neo4j **Enterprise only**. On Community they throw and abort the entire load.
Provenance is now enforced in Python instead, which is better anyway: it
became a measurable metric rather than a delegated assumption.

### Step 9 — The ontology

**Built:** `ontology/ontology.ttl` — 9 classes, 13 relations, 12 attributes.

The one piece of real OWL craft in it: `parentOf` is transitive (if A owns B
and B owns C then A owns C), while `directParentOf` is asymmetric and
irreflexive (nothing directly owns itself). These *must* be separate
properties. OWL 2 DL forbids asymmetry on a transitive property, and a
reasoner will reject an ontology that declares both on one property. Splitting
into a simple sub-property is the standard fix.

Verified with the HermiT reasoner: consistent, no unsatisfiable classes.

### Step 10 — Quality checking

**Built:** `ontology/shapes.ttl` and `src/kg/evaluate/shacl_eval.py`.

SHACL does the job OWL cannot. OWL is open-world: a missing name means
"unknown", never "wrong". SHACL is closed-world: a missing name is a
violation. You need both, and understanding why is most of the value here.

Current results:

```
conforms:          True
violations:        0
ownership cycles:  0
orphan nodes:      72 (0.5%)
provenance:        100%
```

### Step 11 — The CLI

**Built:** `src/kg/cli.py`. One entry point for all of it.

---

## 5. Every file explained

### `ontology/` — the rulebook

| File | Purpose | Edit it? |
|---|---|---|
| **ontology.ttl** | **The ontology. Source of truth.** 9 classes, 13 relations, plus logical rules | **Yes** |
| **competency_questions.md** | The 5 questions the graph should answer, with an honest scorecard | Yes (notes) |
| ontology.owl | Same content in RDF/XML — the format Protégé and reasoners read | No, generated |
| constraints.cypher | The subset Neo4j can enforce | No, generated |
| shapes.ttl | SHACL validation rules — catches bad data | Rarely |
| build.py | Converts `ontology.ttl` into the two generated files | No |
| 

```
ontology.ttl ──build.py──┬──▶ ontology.owl        (Protégé, reasoner)
  (you edit)             └──▶ constraints.cypher  (Neo4j)
```

### `src/kg/` — the code

| File | Purpose |
|---|---|
| `config.py` | Reads settings, validates the SEC contact email, creates data folders |
| `ingest/local.py` | Reads the bundled files in `data/samples/` |
| `parse/schema.py` | **The common format every reader writes into** |
| `parse/structured.py` | Reader for clean tables |
| `parse/semi.py` | Reader for XBRL JSON and Exhibit 21 HTML |
| `load/neo4j_conn.py` | Database connection and health check |
| `load/neo4j_writer.py` | Batched loading, provenance enforcement |
| `evaluate/shacl_eval.py` | Exports the graph to RDF and validates it |
| `cli.py` | All commands live here |

### Other

| Path | Purpose |
|---|---|
| `docker-compose.yml` | Neo4j configuration |
| `config/settings.yaml` | Your settings — **gitignored**, holds your email |
| `config/settings.yaml.example` | Template to copy |
| `pyproject.toml` | Dependencies and package config |
| `tests/` | 34 tests |
| `src/kg.egg-info/` | Auto-generated pip bookkeeping. Ignore it, never edit it |
|  `docs/DESIGN.md` | The design document |
|  `docs/PLAN.md` | The implementation plan |

### Where the data lives

Outside the repo, at `C:\kg-data\`:

```
raw/       downloaded SEC documents, named by content hash (permanent)
staging/   mentions.parquet, edge_mentions.parquet
neo4j/     the database files
```

---

## 6. Command reference

| Command | What it does | Writes |
|---|---|---|
| `check` | Verifies config and Neo4j | — |
| `parse` | Reads `data/samples/` | `mentions.parquet`, `edge_mentions.parquet` |
| `load --reset` | Loads Parquet into Neo4j | the graph |
| `stats` | Counts by type | — |
| `validate` | SHACL validation and consistency checks | — |
| `build-ontology` | Regenerates ontology files from `ontology.ttl` | `ontology.owl`, `constraints.cypher` |
| `run-all` | parse → load → stats | everything |

Tests:

```bash
python -m pytest -m "not integration"   # offline, no database needed
python -m pytest                        # everything, needs docker compose up
```

---

## 7. When you get new data

Three cases. Only one needs code.

### A. More companies

Drop the new files into `data/samples/`, add an entry to `index.json`, then:

```bash
python -m kg.cli run-all
```

No code. The parsers read whatever the index lists.

### B. A new file format, same kinds of thing

Write one reader, roughly 50 lines, copying `parse/structured.py`:

```python
def parse_my_source(data, source_doc, source_uri):
    mentions, edges = [], []
    for row in data:
        mentions.append(Mention(
            mention_id=make_mention_id(source_doc, "my_source", row["id"]),
            mention_type="LegalEntity",     # must exist in the ontology
            name=row["company_name"],
            attrs={"address": row["addr"]},
            source_doc=source_doc,           # provenance is mandatory
            source_uri=source_uri,
            char_offset=None,
            extractor="my_source",
            extractor_version="1",
            confidence=1.0,
            modality="structured",
        ))
    return mentions, edges
```

Then call it from `cli.py`. Everything downstream works unchanged.

### C. A genuinely new kind of thing

1. Add the class to `ontology/ontology.ttl`
2. `python -m kg.cli build-ontology`
3. Add the name to `MENTION_TYPES` in `parse/schema.py`
4. Write the reader (case B)
5. `python -m kg.cli validate`

Step 3 is deliberate friction. The code rejects any mention type not declared
in the ontology, so the graph cannot be polluted by accident.

### Never hand-edit the graph

The next run overwrites it. Fix the reader and re-run instead. Sources are
cached and immutable, so re-running is cheap and produces the same answer.

---

## 8. What is not built yet

| Missing | Why it matters |
|---|---|
| **Entity resolution** | "Apple Inc." and "APPLE INC." are currently two separate nodes. They are one company. This is the hard problem in the field |
| **Unstructured reader** | Facts stated only in prose — acquisitions, executives. Needs an LLM and `ANTHROPIC_API_KEY` |
| **GLEIF ingestion** | A public registry publishing official ownership records. Comparing them against our 1,948 extracted ownership edges would give real precision and recall figures at no labelling cost |
| **Scalability benchmarks** | Timing each stage at 100 / 1k / 10k filings |

Competency question status is tracked honestly in
[ontology/competency_questions.md](ontology/competency_questions.md): two of
five answerable today.

---

## Design decisions worth knowing

**Mentions, not merged entities.** Every extracted record is a `:Mention` with
its own provenance. Merging happens later as *edges*, never by overwriting.
This means a bad merge is reversible and every merge is auditable. Pipelines
that merge on write can never explain why two records became one.

**Identifiers as nodes.** Lets conflicting identifier claims coexist until
entity resolution adjudicates.

**Confidence varies by source.** Structured data is `1.0`. Exhibit 21
heuristics are `0.85`. The system is honest about which facts to trust.

**Immutable sources.** Every download is stored under its content hash and
never modified. Any fact can be checked against the original document.
