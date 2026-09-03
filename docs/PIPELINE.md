# How the pipeline works, end to end

This document traces one full run of the pipeline from a clean checkout to a
validated graph in Neo4j. It follows the data, not the file layout: every
section is a stage, and each stage names the exact function that does the work.

If you want the *why* behind the design, read [DESIGN.md](DESIGN.md). If you
want the setup commands, read the [README](../README.md). This file is the
*what happens when you press go*.

---

## Table of contents

1. [The one-paragraph version](#1-the-one-paragraph-version)
2. [The central idea: mentions, not entities](#2-the-central-idea-mentions-not-entities)
3. [Stage 0 — Configuration](#3-stage-0--configuration)
4. [Stage 1 — Ontology build (offline, run once)](#4-stage-1--ontology-build-offline-run-once)
5. [Stage 2 — Ingest](#5-stage-2--ingest)
6. [Stage 3 — Parse](#6-stage-3--parse)
7. [Stage 4 — Staging (Parquet)](#7-stage-4--staging-parquet)
8. [Stage 5 — Load into Neo4j](#8-stage-5--load-into-neo4j)
9. [Stage 6 — Validate](#9-stage-6--validate)
10. [The commands, in order](#10-the-commands-in-order)
11. [Worked example: one Apple subsidiary](#11-worked-example-one-apple-subsidiary)
12. [Invariants the pipeline enforces](#12-invariants-the-pipeline-enforces)
13. [Failure modes and what they mean](#13-failure-modes-and-what-they-mean)

---

## 1. The one-paragraph version

`kg run-all` reads JSON files bundled under `data/samples/`, converts every row
into a uniform `Mention` or `EdgeMention` record, writes those two record types
to Parquet in the staging directory, replays the Parquet into Neo4j as
`:Mention` nodes and typed relationships, then prints counts. `kg validate`
afterwards exports that graph back into RDF and checks it against the SHACL
shapes plus two graph queries SHACL cannot express. Nothing touches the network:
the SEC data ships with the repo.

```
data/samples/*.json
        |
        |  kg.ingest.local          (read files, hand back dicts)
        v
   raw Python dicts
        |
        |  kg.parse.structured / kg.parse.semi   (one dict -> N Mention + M EdgeMention)
        v
  Mention / EdgeMention dataclasses    <-- vocabulary checked against ontology.ttl here
        |
        |  kg.parse.schema.write_mentions / write_edges
        v
  <data_root>/staging/mentions.parquet
  <data_root>/staging/edge_mentions.parquet
        |
        |  kg.load.neo4j_writer      (batched MERGE)
        v
     Neo4j :Mention nodes + typed relationships
        |
        |  kg.evaluate.shacl_eval    (export back to RDF, run pyshacl + Cypher checks)
        v
     conformance report
```

---

## 2. The central idea: mentions, not entities

Every node the pipeline writes is a **mention**: a claim that *some document
said this*. It is not a claim that the thing exists, and two mentions of the
same company from two documents stay two separate nodes.

That is why every node carries the same ten fields, defined once in
[schema.py:50-67](../src/kg/parse/schema.py#L50-L67):

| Field | Meaning |
|---|---|
| `mention_id` | SHA-1 of `source_doc \| extractor \| local_key`, 40 hex chars |
| `mention_type` | One of the OWL classes in `ontology.ttl` |
| `name` | The surface string as printed in the document |
| `attrs` | Everything type-specific, JSON-encoded into one column |
| `source_doc` | SHA-256 of the source document — the provenance anchor |
| `source_uri` | Where a human can go read it |
| `char_offset` | Position in the source, when known |
| `extractor` | Which parser produced it |
| `extractor_version` | Bumped when a parser's output changes |
| `confidence` | 1.0 for schema-backed data, lower for heuristics |
| `modality` | `structured`, `semi`, or `unstructured` |

Edges carry the same provenance block, plus `src_mention_id`, `dst_mention_id`
and `edge_type` ([schema.py:70-88](../src/kg/parse/schema.py#L70-L88)).

Two consequences worth internalising:

- **Deduplication is deferred, not lost.** Apple appears as a mention from
  `company_tickers.json`, again from its XBRL facts, again as the parent in its
  Exhibit 21 — three nodes, three different `mention_id`s, because the
  `source_doc` differs. Entity resolution (the `resolvesTo` property in the
  ontology) is the layer that would collapse them, and it is not built yet.
- **The ID is a pure function of the inputs.** Re-running `parse` on unchanged
  input regenerates the identical `mention_id`, so `load` uses `MERGE` and a
  second run is a no-op rather than a duplicate.

`attrs` being one JSON string is deliberate: it lets one Parquet schema and one
Cypher `SET` clause carry every mention type without a column per type.

---

## 3. Stage 0 — Configuration

`config/settings.yaml` (copied from `config/settings.yaml.example`) supplies
four values: `data_root`, `neo4j_uri`, `neo4j_user`, `neo4j_password`.

[`load_settings`](../src/kg/config.py#L24-L33) reads it, drops any key not
declared on the `Settings` model, and validates through pydantic. A missing
file raises a `FileNotFoundError` that tells you to copy the example.

`Settings.staging_dir` is a property, not a stored field: it computes
`data_root / "staging"` and calls `mkdir(parents=True, exist_ok=True)` on every
access ([config.py:16-21](../src/kg/config.py#L16-L21)). So the staging folder
exists by the time anyone asks for its path.

Note the split: config holds *where things go*, the repo holds *what things
mean*. `data_root` points outside the repo (`C:/kg-data` by default) so the
generated Parquet and the Neo4j volumes never land in git.

---

## 4. Stage 1 — Ontology build (offline, run once)

`ontology/ontology.ttl` is the hand-written source of truth. Everything else in
`ontology/` is generated from it by [`ontology/build.py`](../ontology/build.py),
via `kg build-ontology`.

It emits two artifacts:

- **`ontology.owl`** — the same graph serialised as RDF/XML, so Protégé and OWL
  reasoners can open it ([build.py:19-23](../ontology/build.py#L19-L23)).
- **`constraints.cypher`** — a uniqueness constraint on `mention_id` plus four
  indexes ([build.py:30-58](../ontology/build.py#L30-L58)). It is deliberately
  thin, because Neo4j Community Edition supports only uniqueness constraints
  and indexes. Property existence, disjointness, cardinality and transitivity
  are all declared in the TTL and enforced later by SHACL and Cypher instead.
  The generated header says exactly this, so nobody edits the file expecting
  more.

The important coupling runs in the other direction. At import time,
[`load_vocabulary`](../src/kg/parse/schema.py#L13-L39) parses `ontology.ttl`
with rdflib and derives:

- `MENTION_TYPES` — every `owl:Class` local name (`Entity`, `LegalEntity`,
  `Person`, `Identifier`, `Filing`, `FinancialFact`, `XBRLConcept`,
  `Jurisdiction`, `Industry`).
- `EDGE_TYPES` — every value of the `kg:edgeLabel` annotation on an object
  property (`PARENT_OF`, `IDENTIFIED_BY`, `REPORTS`, `INCORPORATED_IN`,
  `OFFICER_OF`, `ACQUIRED`, `COMPETES_WITH`, `FILED`).

`Mention.__post_init__` and `EdgeMention.__post_init__` raise `ValueError` on
anything outside those sets. So the ontology is not documentation sitting
beside the code — it is the runtime type check. Add a class to the TTL and the
parsers can immediately emit it; misspell a type in a parser and the run dies
at construction, not at load time.

Two ontology details that explain shapes you will see downstream:

- `kg:directParentOf` is asymmetric and irreflexive; its *super*-property
  `kg:parentOf` is the transitive one. OWL 2 DL forbids one property being both
  transitive and asymmetric, hence the split.
- `kg:identifies` is inverse-functional — one identifier value denotes at most
  one entity. That single axiom is what makes identifier agreement a legitimate
  entity-resolution signal later.

---

## 5. Stage 2 — Ingest

[`src/kg/ingest/local.py`](../src/kg/ingest/local.py) is the only reader. It
returns plain dicts and lists; it never builds a `Mention`.

- `load_index()` — reads `data/samples/index.json`, the manifest. Each entry is
  `{cik, title, xbrl, exhibit21, exhibit21_url}`, where `xbrl` and `exhibit21`
  are paths relative to `data/samples/`.
- `load_tickers()` — reads `structured/company_tickers.json` and returns
  `.values()` as a list, flattening the SEC's numeric-key-indexed object.
- `load_xbrl(entry)` / `load_exhibit21(entry)` — resolve one index entry's path
  and parse the JSON. Both return `None` if the entry lacks that field, which
  is how companies with no Exhibit 21 in the sample set are skipped.
- `sample_root()` raises a `FileNotFoundError` naming the fix if
  `data/samples/` is absent — the usual cause is running from the wrong
  directory.

### `doc_id_for` and the provenance anchor

Real fetches would hash the downloaded bytes. Bundled files instead get
`doc_id_for(relative_path)`: SHA-256 of the *path*
([local.py:50-58](../src/kg/ingest/local.py#L50-L58)). It is deterministic, it
is 64 hex characters, and that matters — `shapes.ttl` enforces
`^[0-9a-f]{64}$` on `kg:sourceDoc`, so a shorter or non-hex id would fail
validation. Provenance stays populated with zero network access.

### Where the Exhibit 21 JSON comes from

`data/samples/semi/exhibit21/*.json` are not SEC-native. SEC ships Exhibit 21
as HTML; a one-off converter turned it into JSON once, offline, and that JSON
is what is committed. The converter has since been removed: the source HTML was
never committed, so it could not be re-run from a clean clone anyway.

Its job was genuinely hard, because filers lay out the same list in
incompatible ways:

- `rows_from_tables` takes the first two non-empty cells of each `<tr>` **by
  position**, never by tag name, because filers mix `<td>` and `<th>` freely.
- `rows_from_text` is the fallback for filers who use dot leaders instead of a
  table. It splits on runs of dots, wide whitespace, or tabs.
- `split_run_on` handles the worst case: a whole subsidiary list as one run-on
  paragraph. It splits on the jurisdiction name that closes each entry,
  matching against a ~180-name list of US states and countries sorted
  longest-first so "New York" wins over "York".
- `is_header_row` and `is_footnote_row` drop the column headings and the
  `(1)`-style footnote legend rows that would otherwise become fake
  subsidiaries.

Each output file records `layout` (`table` or `free_text`), the subsidiary
rows, and the parent's name, CIK and source URL.

Because these rows came from layout heuristics rather than a schema, everything
derived from them carries `confidence = 0.85`
([semi.py:7](../src/kg/parse/semi.py#L7)) rather than 1.0. The confidence
column is where "we guessed" is recorded honestly.

---

## 6. Stage 3 — Parse

Driven by [`cli.parse`](../src/kg/cli.py#L45-L85). It accumulates two flat
lists, `mentions` and `edges`, across three extractors.

### 6.1 `parse_company_tickers` — structured, confidence 1.0

[structured.py:40-63](../src/kg/parse/structured.py#L40-L63). Per record:

- One `LegalEntity` mention, named by `title`, with the CIK zero-padded to ten
  digits (`str(int(cik_str)).zfill(10)` — the SEC feed stores it as an int).
- Two `Identifier` mentions: scheme `CIK` and scheme `TICKER`.
- Two `IDENTIFIED_BY` edges from the entity to each identifier.

Identifiers are nodes rather than properties on purpose. Two documents can
claim different CIKs for the same name, and a node-shaped identifier lets both
claims coexist until entity resolution adjudicates. The zero-padding is not
cosmetic: `shapes.ttl` carries a SPARQL constraint requiring CIK values to
match `^[0-9]{10}$`.

### 6.2 `parse_companyfacts` — XBRL, semi, confidence 1.0

[semi.py:9-100](../src/kg/parse/semi.py#L9-L100). Input is the SEC
`companyfacts` structure: `facts -> taxonomy -> tag -> units -> [observations]`.

- One `LegalEntity` mention for the filer.
- For each tag in `DEFAULT_TAGS = ("Revenues", "Assets", "NetIncomeLoss")` —
  every other tag is skipped, which is what keeps the run to about a minute —
  one `XBRLConcept` mention.
- For each observation under each unit, one `FinancialFact` mention. Its
  `local_key` is `taxonomy:tag:unit:accn:start:end`, which is what makes the
  same fact restated in two filings distinguishable, and the same fact parsed
  twice identical.
- One `REPORTS` edge from the filer to each fact.

Note what is *not* built: there is no `ABOUT_CONCEPT` edge from fact to
concept. The ontology declares `kg:aboutConcept` and SHACL bounds it at
`maxCount 1`, but no parser emits it yet — the tag lives in the fact's `attrs`
instead. Concept nodes are therefore reachable only as orphans, which is part
of why `validate` reports a non-zero orphan rate.

### 6.3 `parse_exhibit21_json` — ownership, semi, confidence 0.85

[semi.py:103-176](../src/kg/parse/semi.py#L103-L176). Reads the converted JSON,
keeps rows with a non-empty `name`, and hands them to `_build_ownership`:

- One `LegalEntity` for the parent, `attrs.role = "parent"`, confidence 1.0
  (the parent's identity comes from `index.json`, not from the HTML).
- One `LegalEntity` per subsidiary, `attrs.role = "subsidiary"`, carrying
  `jurisdiction_text` and `parent_cik`, confidence 0.85.
- One `PARENT_OF` edge parent → subsidiary, confidence 0.85.

`jurisdiction_text` is stored raw, exactly as printed ("Delaware, U.S.",
"Hong Kong"). Normalising it to an ISO-coded `Jurisdiction` node — and emitting
`INCORPORATED_IN` — is a later stage that does not exist yet.

---

## 7. Stage 4 — Staging (Parquet)

[`write_mentions` / `write_edges`](../src/kg/parse/schema.py#L100-L111) turn the
dataclass lists into DataFrames via `_to_frame`, which does one transformation:
`json.dumps` the `attrs` dict into a string, so the column is a plain string
and the Parquet schema stays fixed regardless of mention type.

Output lands in `<data_root>/staging/`:

- `mentions.parquet`
- `edge_mentions.parquet`

Parquet, rather than writing straight to Neo4j, because it gives you a durable,
inspectable intermediate. You can `pd.read_parquet` it and diff two runs to see
exactly what an extractor change did, with no database in the loop — and `load`
becomes replayable against a fresh graph without re-parsing.

---

## 8. Stage 5 — Load into Neo4j

[`cli.load`](../src/kg/cli.py#L88-L101) runs three steps in order.

**1. `clear_graph` (only with `--reset`)** — `MATCH (n) DETACH DELETE n`.
`run-all` passes `reset=True`, so the standard run always starts from an empty
graph.

**2. `apply_constraints`** — splits `ontology/constraints.cypher` on `;` and
runs each statement ([neo4j_writer.py:38-47](../src/kg/load/neo4j_writer.py#L38-L47)).
Every statement is `IF NOT EXISTS`, so it is idempotent. The `mention_id`
uniqueness constraint must exist *before* the data lands, because the loader
relies on `MERGE` matching on it.

**3. `load_mentions`, then `load_edges`** — mentions first, always, since the
edge query `MATCH`es both endpoints and silently writes nothing if either is
missing.

Both loaders do the same three things
([neo4j_writer.py:74-99](../src/kg/load/neo4j_writer.py#L74-L99)):

- **`_assert_provenance`** — fails the run if any row has a null `source_doc`,
  `extractor`, or `extractor_version`. Community Edition cannot enforce
  property existence, so this is the substitute, checked in pandas before a
  single row is sent.
- **NaN → None** — `df.astype(object).where(pd.notna(df), None)`. pandas
  represents a missing `char_offset` as `NaN`; the Neo4j driver would store
  that as a float NaN property. `None` becomes a proper Cypher `null`.
- **Batched writes** — 5000 rows per `UNWIND`, one transaction per batch.

The mention query is a single `MERGE` on `mention_id` followed by a `SET` of
every field, so a re-run overwrites in place instead of duplicating.

The edge query is a template with the relationship type interpolated —
`EDGE_QUERY_TEMPLATE % edge_type` — because Cypher cannot parameterise a
relationship type. That is a string substituted into a query, so `load_edges`
groups by `edge_type` and **re-checks each one against `EDGE_TYPES` from the
ontology before interpolating**, raising `refusing to load unknown edge_type`
otherwise ([neo4j_writer.py:92-95](../src/kg/load/neo4j_writer.py#L92-L95)).
The values can only come from the ontology's own `edgeLabel` annotations, never
from file content.

### The shape of the resulting graph

Every node carries the single label `:Mention`, with the OWL class stored in
the `mention_type` *property* rather than as a label. Relationships, by
contrast, are genuinely typed (`:PARENT_OF`, `:REPORTS`, `:IDENTIFIED_BY`),
because Cypher traversal syntax and the variable-length path queries used in
validation need real relationship types.

---

## 9. Stage 6 — Validate

`kg validate` ([cli.py:128-155](../src/kg/cli.py#L128-L155)) is a round trip:
property graph → RDF → SHACL. `--limit` defaults to 3000 nodes for speed;
`--limit 0` validates the whole graph.

**Export** — [`export_rdf`](../src/kg/evaluate/shacl_eval.py#L33-L93) reads
`:Mention` nodes and rebuilds them as RDF subjects under
`http://kg.local/sec/node/<mention_id>`:

- `mention_type` becomes `rdf:type kg:<Class>`.
- `name` becomes `kg:identifierValue` for identifiers, `kg:legalName` otherwise.
- Provenance fields map to `kg:sourceDoc`, `kg:extractor`,
  `kg:extractorVersion`, `kg:modality`, `kg:confidence`.
- Selected keys from the JSON `attrs` are promoted to typed literals via
  `ATTR_TO_PROPERTY`: `scheme`, `val`, `fy`, `unit`, `jurisdiction_text`.
  Anything outside that map stays invisible to SHACL — that map is the seam
  between the loose `attrs` blob and the formal ontology.
- Relationships map through `EDGE_TO_PROPERTY`, and an edge is skipped unless
  **both** endpoints were exported. This is what stops `--limit` from producing
  dangling triples that would fail validation for the wrong reason.

**Validate** — [`validate`](../src/kg/evaluate/shacl_eval.py#L96-L115) runs
pyshacl with `shapes.ttl` as the shapes graph and `ontology.ttl` as the ontology
graph, `advanced=True` (needed for the SPARQL-based shapes), inference off.
`summarise` then counts violations by `sh:message`, and the CLI prints the ten
most common.

The shapes check what OWL cannot, because SHACL is closed-world — a missing
required property is a violation, not an unknown:

- Every `LegalEntity`, `Person`, `Identifier`, `FinancialFact` and
  `XBRLConcept` must carry a 64-hex `sourceDoc`, an `extractor`, a `confidence`
  in [0,1], and a `modality` from the three-value list.
- A legal entity has exactly one name of at least two characters, and at most
  one jurisdiction.
- An identifier has exactly one scheme from `{LEI, CIK, TICKER}` and exactly
  one value; SPARQL shapes then enforce the CIK ten-digit format and the LEI
  18-alphanumeric-plus-2-check-digit format.
- A financial fact has exactly one decimal value and a fiscal year between
  1900 and 2100.
- A SPARQL shape rejects `$this kg:directParentOf $this` — self-ownership.

**Cypher-only checks** —
[`cypher_only_checks`](../src/kg/evaluate/shacl_eval.py#L128-L143) covers the
two things SHACL structurally cannot:

- `ownership_cycles` — `MATCH path=(m)-[:PARENT_OF*1..6]->(m)`. SHACL has no
  transitive closure, so cycles beyond depth 1 need a variable-length Cypher
  path.
- `orphan_nodes` / `orphan_rate` — nodes with no relationship at all. A rising
  orphan rate means an extractor is producing nodes it never links.

Both run against the whole graph regardless of `--limit`.

`kg stats` is the cheaper sibling: mention counts grouped by type and modality,
relationship counts by type, no RDF involved.

---

## 10. The commands, in order

All of them live in [`src/kg/cli.py`](../src/kg/cli.py), wired up with Typer.

| Command | What it does |
|---|---|
| `kg check` | Prints `data_root`, counts sample JSON files, and reports the Neo4j version plus whether the n10s procedures are installed. Run this first — it fails loudly if the container is not up. |
| `kg parse` | Stages 2–4. Reads `data/samples/`, writes both Parquet files, prints per-source counts. No database needed. |
| `kg load [--reset]` | Stage 5. Constraints, then mentions, then edges. |
| `kg stats` | Node counts by type and modality, relationship counts by type. |
| `kg validate [--limit N]` | Stage 6. RDF export, SHACL report, Cypher checks. |
| `kg build-ontology` | Stage 1. Regenerates `ontology.owl` and `constraints.cypher` from `ontology.ttl`. Run after editing the TTL. |
| `kg run-all` | `parse()`, then `load(reset=True)`, then `stats()`. |

Typical first run:

```
docker compose up -d
kg check
kg run-all
kg validate
```

---

## 11. Worked example: one Apple subsidiary

Trace "Apple Canada Inc." from disk to validated triple.

**1. On disk.** `data/samples/semi/exhibit21/apple_inc.json` contains
`{"name": "Apple Canada Inc.", "jurisdiction_text": "Canada"}`, converted
earlier from `a10-kexhibit21109272025.htm` via the table-parsing path.

**2. Ingest.** `load_index()` yields the Apple entry; `load_exhibit21(entry)`
parses that JSON. `doc_id_for("semi/exhibit21/apple_inc.json")` produces the
64-hex `source_doc`.

**3. Parse.** `parse_exhibit21_json` keeps the row, and `_build_ownership`
emits:

- a `LegalEntity` mention,
  `mention_id = sha1(source_doc|exhibit21|sub:Apple Canada Inc.)`,
  `name = "Apple Canada Inc."`,
  `attrs = {"role": "subsidiary", "jurisdiction_text": "Canada", "parent_cik": "0000320193"}`,
  `confidence = 0.85`, `modality = "semi"`;
- a `PARENT_OF` edge from Apple's parent mention to it, same confidence.

`__post_init__` confirms `LegalEntity` is in `MENTION_TYPES` and `PARENT_OF` is
in `EDGE_TYPES` — both read out of `ontology.ttl` at import.

**4. Stage.** Both rows land in Parquet, `attrs` serialised to a JSON string.

**5. Load.** `MERGE (m:Mention {mention_id: ...})` creates the node;
`load_edges` groups it under `PARENT_OF`, verifies that against `EDGE_TYPES`,
and merges the relationship.

**6. Validate.** `export_rdf` emits `node:<id> rdf:type kg:LegalEntity`,
`kg:legalName "Apple Canada Inc."`, `kg:jurisdictionText "Canada"`,
`kg:confidence "0.85"^^xsd:decimal`, plus the provenance triples, and
`kg:directParentOf` from the parent node. The provenance shape passes (64-hex
doc, extractor present, confidence in range, modality valid), the legal-entity
shape passes (one name, at least two characters), and the self-ownership shape
does not fire.

---

## 12. Invariants the pipeline enforces

These hold by construction, and breaking one is a bug:

1. **Vocabulary is single-source.** Mention and edge types exist only in
   `ontology.ttl`. `schema.py` reads them at import; the parsers cannot invent
   a type; `load_edges` re-checks before interpolating into Cypher.
2. **Provenance is 100%.** `source_doc`, `extractor` and `extractor_version`
   are non-null on every row — asserted in pandas before load, and again by
   SHACL after.
3. **IDs are deterministic.** Same input, same `mention_id`. Combined with
   `MERGE`, re-running is idempotent.
4. **Confidence reflects method.** 1.0 for schema-backed extraction, 0.85 for
   HTML layout heuristics. It is never rounded up.
5. **Mentions are never merged during parse.** One document's claim is one
   node. Resolution is a separate, later concern.
6. **Generated files are never hand-edited.** `ontology.owl` and
   `constraints.cypher` carry a generated header; edit `ontology.ttl` and
   re-run `kg build-ontology`.

---

## 13. Failure modes and what they mean

| Symptom | Cause |
|---|---|
| `FileNotFoundError: config/settings.yaml not found` | Copy `config/settings.yaml.example` to `config/settings.yaml`. |
| `data/samples not found` | Running from somewhere other than the project root. Every path in the pipeline is repo-relative. |
| `ontology/ontology.ttl not found` at import | Same cause — `schema.py` reads the ontology at import time, so even `kg --help` needs the project root. |
| `unknown mention_type: X` | A parser emitted a type not declared as an `owl:Class`. Add the class to `ontology.ttl`, or fix the parser. |
| `unknown edge_type: X` | Same, but the object property also needs a `kg:edgeLabel` annotation — a property without one is invisible to `EDGE_TYPES`. |
| `refusing to load unknown edge_type` | A stale `edge_mentions.parquet` from before an ontology change. Re-run `kg parse`. |
| `N mention rows are missing source_doc` | An extractor built a `Mention` without provenance. |
| Edges load as 0 | `load_edges` ran against a graph missing the endpoints — mentions must load first, and `--reset` wipes them. |
| SHACL: "every assertion must cite a SHA-256 source document" | A `source_doc` that is not 64 lowercase hex characters. |
| SHACL: "CIK values must be zero-padded to ten digits" | A parser skipped the `.zfill(10)`. |
| Non-zero `orphan_rate` | Expected today: `XBRLConcept` nodes have no `ABOUT_CONCEPT` edge because no parser emits one yet. |
