# Graph commands

Cypher for exploring the loaded graph, in the Aura **Query** tab or the Neo4j
Browser at `http://localhost:7474`.

Two things about this graph before you start:

- **Every node has one label, `:Mention`.** The class lives in the
  `mention_type` *property*, not as a label, so the sidebar's label list shows
  only `Mention`. Filter with `{mention_type: 'LegalEntity'}`.
- **`attrs` is a JSON string, not a map.** Use `apoc.convert.fromJsonMap(m.attrs).cik`
  to read a key, or `m.attrs CONTAINS '"tag": "Revenues"'` for a cheap match.

---

## Viewing the whole graph

You cannot usefully render all 14,000 nodes at once, and the browser will not
try — it caps the display at a few hundred by default.

**83% of the graph is `FinancialFact` leaves.** 11,628 fact nodes hang off 25
filers through `REPORTS`. Drawing them gives you 25 dense balls of dots and
hides everything else. The structure worth looking at is the other ~2,400 nodes.

### 1. The shape of the model

```cypher
CALL db.schema.visualization()
```

Node types and relationship types in one diagram. Your schema, not your data.

### 2. The whole ownership forest

```cypher
MATCH p=(parent:Mention)-[:PARENT_OF]->(sub)
RETURN p
```

2,123 relationships. Raise the display cap first, in the browser command line:

```
:config initialNodeDisplay: 3000
```

Slow and hairball-shaped, but it is the complete ownership graph.

### 3. Everything except the facts — recommended

```cypher
MATCH p=(a:Mention)-[r]->(b:Mention)
WHERE type(r) <> 'REPORTS'
RETURN p
```

Ownership, identifiers and resolution: 2,276 edges. The real corporate-structure
graph.

### 4. One company, fully expanded

The most readable view:

```cypher
MATCH p=(parent:Mention {mention_type:'LegalEntity'})-[:PARENT_OF]->(sub)
WHERE parent.name CONTAINS 'Alphabet'
RETURN p
```

Swap the name for Apple, NVIDIA, Microsoft, Walmart.

### 5. Facts for one company only

```cypher
MATCH p=(f:Mention)-[:REPORTS]->(fact)
WHERE f.name CONTAINS 'NVIDIA'
RETURN p LIMIT 300
```

### 6. The whole graph as numbers

```cypher
MATCH (m:Mention)
RETURN m.mention_type AS type, m.modality AS modality, count(*) AS n
ORDER BY n DESC
```

Five rows tell you more about 14,000 nodes than any picture.

---

## Crossing document boundaries

Each source document produces its own island — parsers only ever see one
document, so nothing they emit can link two. `RESOLVES_TO` edges, written by
`kg resolve`, are the only bridges.

### The same company, seen by three extractors

```cypher
MATCH (m:Mention {mention_type:'LegalEntity'})
WHERE m.name CONTAINS 'Amazon'
RETURN m.extractor, m.modality, m.source_doc, m.mention_id
```

Three rows for one company: one per source document, each with a different
`mention_id`, because the id is a hash of `source_doc | extractor | key`.

### What a canonical entity ties together

```cypher
MATCH (e:Mention {mention_type:'Entity'})<-[:RESOLVES_TO]-(m:Mention)
WHERE e.name CONTAINS 'Amazon'
RETURN e.name AS entity, m.extractor AS extractor,
       size([(m)-[:PARENT_OF]->() | 1])     AS subsidiaries,
       size([(m)-[:IDENTIFIED_BY]->() | 1]) AS identifiers,
       size([(m)-[:REPORTS]->() | 1])       AS facts
```

One mention holds the ownership, another the identifiers, a third the
financials. The `Entity` is what makes all three reachable from one another.

### The query that needs resolution

"What did the parent of Apple Distribution International report?" Ownership is
in the Exhibit 21 island, financials in the XBRL island.

```cypher
MATCH (sub:Mention)<-[:PARENT_OF]-(pm:Mention)-[:RESOLVES_TO]->(e:Mention {mention_type:'Entity'})
MATCH (e)<-[:RESOLVES_TO]-(fm:Mention)-[:REPORTS]->(f:Mention)
WHERE sub.name CONTAINS 'Apple Distribution'
WITH e, sub, apoc.convert.fromJsonMap(f.attrs) AS a
WHERE a.tag = 'Assets' AND a.fp = 'FY'
RETURN DISTINCT e.name AS entity, sub.name AS subsidiary,
       a.fy AS fiscal_year, a.val AS assets, a.form AS form
ORDER BY fiscal_year DESC LIMIT 5
```

Drop the `RESOLVES_TO` hops and it returns nothing — the parent in the first
hop and the filer in the second are different nodes.

### GraphRAG retrieval pattern

Seed on a name, hop up to the entity, come back down into every other
document's neighbourhood:

```cypher
MATCH (m:Mention)-[:RESOLVES_TO]->(e:Mention {mention_type:'Entity'})
MATCH (e)<-[:RESOLVES_TO]-(other:Mention)-[r]->(neighbour:Mention)
WHERE m.name CONTAINS $seed
RETURN e.name, type(r), neighbour.name, other.source_uri
LIMIT 100
```

`source_uri` gives you a citation for every row.

---

## Competency questions

Tracked with their status in
[ontology/competency_questions.md](../ontology/competency_questions.md).

### CQ1 — which subsidiaries does X own?

```cypher
MATCH (p:Mention {mention_type:'LegalEntity'})-[:PARENT_OF*1..]->(s:Mention)
WHERE p.name CONTAINS 'Berkshire'
RETURN p.name AS parent, count(DISTINCT s) AS owned
```

Answerable at depth 1. Deeper chains need subsidiary filings ingested as filers
in their own right.

### CQ2 — which mentions are the same legal entity?

```cypher
MATCH (a:Mention)-[:RESOLVES_TO]->(e:Mention {mention_type:'Entity'})<-[:RESOLVES_TO]-(b:Mention)
WHERE a.mention_id < b.mention_id
RETURN e.name AS entity, a.extractor, b.extractor
```

Unblocked by R0.

### CQ3 — what did X report, and from which filing?

```cypher
MATCH (c:Mention)-[:REPORTS]->(f:Mention {mention_type:'FinancialFact'})
WHERE c.name CONTAINS 'NVIDIA' AND f.attrs CONTAINS '"tag": "Revenues"'
RETURN c.name, f.attrs, f.source_doc, f.source_uri
LIMIT 20
```

Multiple rows per fiscal year: XBRL reports segment and quarterly facts
alongside annual totals.

---

## Provenance and quality

### Trace any fact to its document

```cypher
MATCH (m:Mention) WHERE m.name CONTAINS 'Apple Canada'
RETURN m.name, m.extractor, m.extractor_version, m.confidence,
       m.source_doc, m.source_uri
```

Every node carries all six. Provenance coverage is 100% and SHACL enforces it.

### Everything the heuristics produced

```cypher
MATCH (m:Mention) WHERE m.confidence < 1.0
RETURN m.extractor, count(*) AS n
```

`0.85` marks rows recovered from Exhibit 21 layout heuristics rather than a
schema.

### Orphans — nodes nothing links to

```cypher
MATCH (m:Mention) WHERE NOT (m)--()
RETURN m.mention_type, count(*) AS n ORDER BY n DESC
```

Expect ~71 `XBRLConcept` nodes: no parser emits `ABOUT_CONCEPT` yet.

### Ownership cycles — should always be zero

```cypher
MATCH path=(m:Mention)-[:PARENT_OF*1..6]->(m)
RETURN count(path) AS cycles
```

A company cannot own itself. Non-zero means an extractor bug.

### Which documents fed the graph

```cypher
MATCH (m:Mention)
RETURN m.source_doc AS document, m.extractor AS extractor, count(*) AS nodes
ORDER BY nodes DESC LIMIT 20
```

---

## Housekeeping

```cypher
MATCH (n) RETURN count(n) AS nodes
MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS n ORDER BY n DESC
SHOW INDEXES
SHOW CONSTRAINTS
```

Wipe the graph — `python -m kg.cli load --reset` does this for you:

```cypher
MATCH (n) DETACH DELETE n
```
