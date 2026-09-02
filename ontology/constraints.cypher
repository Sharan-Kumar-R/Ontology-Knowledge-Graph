// GENERATED from ontology.ttl by ontology/build.py -- do not edit by hand.
//
// Neo4j Community Edition supports uniqueness constraints and indexes only.
// Property existence, disjointness, cardinality and transitivity are expressed
// in ontology.ttl and shapes.ttl, and checked in kg.evaluate.

CREATE CONSTRAINT mention_id_unique IF NOT EXISTS
FOR (m:Mention) REQUIRE m.mention_id IS UNIQUE;

// identifies is inverse-functional in the ontology, meaning one
// identifier value denotes at most one entity. Expressing that
// in Neo4j needs a NODE KEY constraint, which is Enterprise
// only, so it is checked in kg.evaluate instead.

// Indexes for the 9 declared classes
CREATE INDEX mention_type_idx IF NOT EXISTS
FOR (m:Mention) ON (m.mention_type);

CREATE INDEX mention_name_idx IF NOT EXISTS
FOR (m:Mention) ON (m.name);

CREATE INDEX mention_modality_idx IF NOT EXISTS
FOR (m:Mention) ON (m.modality);

CREATE INDEX mention_source_idx IF NOT EXISTS
FOR (m:Mention) ON (m.source_doc);
