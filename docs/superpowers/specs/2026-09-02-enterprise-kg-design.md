# Enterprise Knowledge Graph Construction — Design

Date: 2026-09-02
Status: Approved, pre-implementation

## 1. Goal

Build a working knowledge graph construction pipeline over open-source enterprise data, covering five areas: ontology design, multi-modality extraction, entity resolution at scale, KG quality evaluation, and scalability analysis.

Purpose is learning. Optimize for understanding each stage, not for production hardening or maximum breadth of technique.

## 2. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Domain | SEC EDGAR + GLEIF | Genuinely enterprise data; ships all three format modalities natively; CIK/LEI/ticker crosswalks give free entity-resolution ground truth; GLEIF Level 2 gives free ownership-edge ground truth |
| Modality meaning | Data format only | Structured, semi-structured, unstructured. No images or audio |
| Graph store | Neo4j 5 (labeled property graph), Docker | Store of record; queried via Cypher |
| Ontology formalism | OWL authored in Protégé, SHACL shapes, owlready2 reasoner | Ontology design is an explicit deliverable and deserves a formal artifact |
| Entity resolution | Hand-built ladder, R0 through R5 | Libraries become baselines to compare against, not the implementation |
| Ontology timing | Data first, formalize in week 2 | Avoids modeling in a vacuum |
| Compute | Laptop plus bounded LLM API budget | Extraction over a sampled corpus, cached by content hash |

Neo4j-first and hand-built ER are reconciled as follows: Neo4j is the store of record. Mentions land in Neo4j early. Entity resolution runs as external Python, reading batches out and writing canonical entities back. Neo4j GDS similarity algorithms are used only as a benchmark baseline.

## 3. Architecture

### 3.1 Two-layer graph

The central modeling decision.

```
source docs --> :Mention nodes  --ER-->  :Entity nodes
                one per occurrence,      canonical, deduplicated
                provenance-bearing
```

Every `:Mention` carries `source_doc`, `char_offset`, `extractor`, `extractor_version`, `confidence`. Every mention-to-entity link is `:RESOLVES_TO` carrying score and method version.

Nothing is ever destructively merged. Entity resolution decisions are edges, so they are reversible, auditable, and re-runnable when the matcher improves. Pipelines that merge on write cannot explain why two records became one.

### 3.2 Data flow

```
ingest/    EDGAR REST + GLEIF bulk    --> data/raw/ (immutable, content-hashed)
parse/     three modality parsers     --> mentions.parquet (uniform schema)
load/      batched UNWIND writes      --> Neo4j :Mention layer
resolve/   blocking, score, cluster   --> :Entity layer + :RESOLVES_TO
enrich/    relation edges attach to entities, not mentions
evaluate/  ER metrics, edge metrics, SHACL violations, completeness
```

DuckDB sits beside Neo4j as the entity-resolution scratchpad. Pairwise candidate generation over millions of rows is a columnar join, not a graph traversal.

All three parsers emit the same `mentions.parquet` schema, so downstream entity resolution is modality-blind. That uniformity is what makes the multi-modality claim structurally true rather than three pipelines sharing a directory.

## 4. Ontology

### 4.1 Competency questions

Written before any schema. The ontology is judged by whether it can answer these.

1. Which subsidiaries does company X own, directly and transitively?
2. Which two filers under different names are the same legal entity?
3. What did X report as revenue in FY2023, and from which filing?
4. Which companies did X acquire, and when?
5. Which entities are incorporated in jurisdiction J?

### 4.2 Classes and relations

Classes: `LegalEntity`, `Person`, `Filing`, `FinancialFact`, `XBRLConcept`, `Jurisdiction`, `Industry` (SIC), `Identifier`.

Relations: `FILED`, `PARENT_OF` (transitive), `INCORPORATED_IN`, `IDENTIFIED_BY`, `REPORTS`, `ACQUIRED`, `OFFICER_OF`, `RESOLVES_TO`.

### 4.3 Identifiers as nodes

```
(:LegalEntity)-[:IDENTIFIED_BY]->(:Identifier {scheme:'LEI'})
```

Identifiers are nodes, not properties. A property forces one true identifier per entity. The identifier-as-node pattern lets conflicting identifier claims coexist until entity resolution adjudicates. Conflicting identifiers are the normal case in enterprise data.

### 4.4 Formalization

OWL is the source of truth and generates the rest.

```
ontology.owl (Protégé)
   |
   +--> constraints.cypher   generated; Neo4j enforces uniqueness/existence
   +--> shapes.ttl (SHACL)   validated against live data via n10s
   +--> WebVOWL diagram      for the README
```

Three mechanisms:

- `n10s.onto.import.fetch` loads the class hierarchy into Neo4j as queryable nodes.
- `n10s.validation.shacl.validate` runs SHACL against live graph data, producing the machine-checked violation counts used in section 7.
- `owlready2` plus HermiT runs description-logic entailment over a sampled ABox of roughly 2000 entities, deriving transitive ownership without a hand-written traversal.

Accepted limitation: Neo4j enforces approximately uniqueness and not-null. It cannot enforce disjointness, transitivity, or domain/range. Those checks live in `evaluate/`.

Accepted tension, and the most valuable thing this section teaches: OWL is open-world, so a missing `PARENT_OF` is unknown rather than false, which means OWL alone cannot detect incompleteness. SHACL is closed-world and validation-oriented. OWL handles hierarchy and inference; SHACL handles quality checks.

## 5. Extraction

| Modality | Source | Method | Yields |
|---|---|---|---|
| Structured | GLEIF LEI golden copy CSV; `company_tickers.json` | Deterministic typed column-to-property mapping | Legal names, jurisdictions, HQ addresses, LEI-to-name |
| Semi-structured | `companyfacts` JSON (XBRL); Exhibit 21 HTML | Schema-tolerant JSON walker; HTML table parser with heuristics for free-text variants | Financial facts; parent-to-subsidiary edges |
| Unstructured | 10-K Item 1, Item 1A, 8-K narrative | LLM with structured output, chunked, cached by content hash | `ACQUIRED`, `OFFICER_OF`, `COMPETES_WITH`, plus evidence span |

Exhibit 21, "Subsidiaries of the Registrant", is the centerpiece. It is a per-filing list of every subsidiary with jurisdiction, formatted differently by every filer, sometimes a clean table and sometimes an indented text blob. It is the hard semi-structured case, it produces the ownership edges the ontology needs, and GLEIF Level 2 relationship records provide ground truth to score those edges against.

Note on proportion: roughly 95 percent of nodes and edges arrive with no NLP at all. NLP is one adapter among three, used only where facts exist nowhere else.

## 6. Entity resolution ladder

Six rungs. Each is measured and compared against the rung below. The comparison is the deliverable.

- **R0 deterministic.** Exact match on CIK, LEI, ticker. Near-perfect precision, poor recall. The floor everything must beat.
- **R1 normalization.** Legal-suffix stripping (INC, INCORPORATED, CORP, LLC, LTD, PLC, N.V., S.A.), case, punctuation, Unicode, ampersand-to-AND, leading THE. Expect the largest single recall jump here.
- **R2 blocking.** Three strategies compared: standard blocking (first four characters of normalized name plus jurisdiction), sorted neighborhood (sort by normalized name, sliding window w), MinHash LSH over character 3-grams via `datasketch`. Scored on reduction ratio, pair completeness, pair quality. The RR/PC trade-off curve is the most useful plot in the project.
- **R3 pairwise scoring.** Features: Jaro-Winkler on name, token Jaccard, TF-IDF cosine (rare-token agreement matters far more than common-token agreement), jurisdiction equality, address similarity, SIC match, numeric-token exact match. Two scorers compared: Fellegi-Sunter with m/u probabilities fit by EM, and a supervised gradient-boosted classifier trained on the CIK/LEI gold.
- **R4 clustering.** Connected components takes transitive closure, so one bad edge merges unrelated conglomerates into a single blob. Compared against threshold-based hierarchical agglomerative clustering and a correlation-clustering approximation. Cluster-size distributions are plotted; the pathology is visible immediately.
- **R5 canonicalization.** Attribute survivorship rules, write `:Entity`, link `:RESOLVES_TO` with score and method version.

### 6.1 Gold labels and their bias

CIK-LEI-ticker crosswalks give positives only, and only for entities holding both identifiers. Hard negatives are sampled from within blocks (same block, known different). The coverage bias is stated explicitly in all reported numbers.

### 6.2 Metrics

Pairwise precision, recall, F1, and B-cubed at cluster level. B-cubed is the correct cluster metric; pairwise F1 flatters large clusters.

### 6.3 Domain hard cases

Same name in different jurisdictions as genuinely different companies. Name changes over time with CIK constant, which makes this temporal entity resolution. Post-merger successor entities. Reused tickers.

## 7. Quality evaluation

Five dimensions plus two cheap additions.

1. **Conformance.** n10s SHACL violations per shape, per 1000 nodes.
2. **Entity resolution quality.** Pairwise F1 and B-cubed against gold, reported per ladder rung.
3. **Extraction accuracy.** Exhibit 21 `PARENT_OF` edges scored against GLEIF Level 2 ownership records, requiring no annotation. LLM-extracted triples scored against roughly 150 hand-labeled spans.
4. **Completeness.** Percentage of filers with an LEI, percentage of entities with a jurisdiction, orphan-node rate. Reported with the open-world caveat that absence is not falsehood.
5. **Consistency.** Cycles in `PARENT_OF` (logically impossible), entities with conflicting LEIs, disjointness violations, out-of-range dates.

Additions: provenance coverage (percentage of edges tracing to a source document, which should be 100 percent, and where it is not, a bug is found) and determinism (re-run and diff the graph).

## 8. Scalability analysis

Measured at 100, 1000, and 10000 filings, then extrapolated with a stated cost model.

- Per-stage throughput: documents per second parsed, rows per second written, pairs per second scored.
- Candidate pairs versus n for each blocking strategy against the quadratic baseline. This plot demonstrates that blocking is not optional.
- Neo4j write tuning: batched `UNWIND` versus per-row `MERGE`, batch size sweep, index-before versus index-after load, `apoc.periodic.iterate`.
- Description-logic reasoner ceiling: the entity count at which HermiT stops completing, expected in the low thousands. This is a finding, not a failure.
- LLM cost model: tokens per filing, cache hit rate, dollars per 1000 filings.
- Named bottleneck and extrapolation to 10 million mentions.

## 9. Stack and repository layout

Python 3.11, Neo4j 5 with neosemantics in Docker, DuckDB, pandas and pyarrow, lxml, rapidfuzz, scikit-learn, datasketch, owlready2, typer for the CLI, pytest.

```
onotology/
  docker-compose.yml            neo4j + n10s plugin
  config/settings.yaml
  ontology/
    ontology.owl                Protégé, week 2
    shapes.ttl                  SHACL
    constraints.cypher          generated from OWL
    competency_questions.md
    generate_cypher.py
  src/kg/
    ingest/      edgar.py  gleif.py  cache.py
    parse/       structured.py  semi.py  unstructured.py  schema.py
    load/        neo4j_writer.py
    resolve/     normalize.py  blocking.py  features.py  scorer.py  cluster.py
    evaluate/    er_eval.py  edge_eval.py  shacl_eval.py  completeness.py
    scale/       bench.py
  data/          raw/  staging/  gold/
  notebooks/
  tests/
```

Every stage is a CLI command, configuration-driven, cached by content hash so re-runs are cheap.

## 10. Build order

| Week | Work |
|---|---|
| 1 | Ingest plus three parsers to `mentions.parquet` to Neo4j, thin schema |
| 2 | Protégé ontology informed by what the data showed; generate constraints; wire up SHACL |
| 3 | Entity resolution ladder R0 through R5 with metrics at each rung |
| 4 | Quality evaluation suite, scalability benchmarks, writeup |

## 11. Out of scope

- Image and audio modalities
- Production monitoring, alerting, incident response
- Distributed compute (Spark, Ray). Scale beyond the measured tiers is extrapolated, and labeled as extrapolated
- Real-time or streaming ingestion
- Authentication, multi-tenancy, access control
