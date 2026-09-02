# Competency Questions

An ontology is judged by whether it can answer the questions it was built for.
Each question below states the query, the classes and properties it exercises,
and its current status against the loaded graph.

## CQ1 — Which subsidiaries does company X own, directly and transitively?

**Exercises:** `LegalEntity`, `directParentOf`, `parentOf` (transitive)

```cypher
MATCH (p:Mention {mention_type:'LegalEntity'})-[:PARENT_OF*1..]->(s:Mention)
WHERE p.name CONTAINS 'Berkshire'
RETURN p.name AS parent, count(DISTINCT s) AS owned;
```

**Status:** answerable at depth 1. Deeper chains require subsidiary filings to be
ingested as filers in their own right, or the OWL reasoner materialising
`parentOf` from `directParentOf`.

## CQ2 — Which two filers under different names are the same legal entity?

**Exercises:** `LegalEntity`, `Identifier`, `identifiedBy`, `sameEntityAs`

```cypher
MATCH (a:Mention)-[:IDENTIFIED_BY]->(i:Mention)<-[:IDENTIFIED_BY]-(b:Mention)
WHERE a.mention_id < b.mention_id
RETURN a.name, b.name, i.name AS shared_identifier;
```

**Status:** blocked until week 3. Entity resolution produces the `:Entity` layer
and `RESOLVES_TO` edges that this question depends on. The identifier-as-node
modelling is what will make it answerable.

## CQ3 — What did X report as revenue in FY2023, and from which filing?

**Exercises:** `LegalEntity`, `FinancialFact`, `XBRLConcept`, `reports`, `sourceDoc`

```cypher
MATCH (c:Mention)-[:REPORTS]->(f:Mention {mention_type:'FinancialFact'})
WHERE c.name CONTAINS 'NVIDIA' AND f.attrs CONTAINS '"tag": "Revenues"'
RETURN c.name, f.attrs, f.source_doc;
```

**Status:** answerable. Returns multiple rows per fiscal year because XBRL
reports segment and quarterly facts alongside annual totals. Disambiguating
them requires modelling the reporting period, which `FinancialFactShape`
constrains but does not yet resolve.

## CQ4 — Which companies did X acquire, and when?

**Exercises:** `LegalEntity`, `acquired`

**Status:** blocked. `acquired` is declared in the ontology but no extractor
populates it. It comes from the unstructured parser reading 10-K narrative.

## CQ5 — Which entities are incorporated in jurisdiction J?

**Exercises:** `LegalEntity`, `Jurisdiction`, `incorporatedIn`

**Status:** blocked by data quality, and this is the most instructive gap.
Exhibit 21 states jurisdiction as free text — `"Delaware, U.S."`, `"Hong Kong"`,
`"Ireland"` — so there is no `Jurisdiction` instance to point at. Two fixes:
normalise the strings to ISO 3166-2 codes, or ingest GLEIF Level 1, which
carries `Entity.LegalJurisdiction` as a code already.

This is exactly what modelling against real data surfaces. An ontology authored
before ingestion would have declared `incorporatedIn → Jurisdiction` and looked
correct while being unfillable.

## Scorecard

| Question | Status | Blocked on |
|---|---|---|
| CQ1 subsidiaries | partial | transitive materialisation |
| CQ2 same entity | no | entity resolution (week 3) |
| CQ3 revenue | yes | — |
| CQ4 acquisitions | no | unstructured parser |
| CQ5 jurisdiction | no | jurisdiction normalisation or GLEIF |

Two of five answerable today. The ontology declares all five so the gaps are
explicit and measurable rather than discovered late.
