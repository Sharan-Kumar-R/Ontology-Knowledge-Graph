// Neo4j Community Edition supports uniqueness constraints and indexes only.
// Property existence constraints are Enterprise-only, so provenance
// requirements (source_doc, extractor) are enforced in kg.load.neo4j_writer
// and measured as a quality metric in evaluate/.

CREATE CONSTRAINT mention_id_unique IF NOT EXISTS
FOR (m:Mention) REQUIRE m.mention_id IS UNIQUE;

CREATE INDEX mention_type_idx IF NOT EXISTS
FOR (m:Mention) ON (m.mention_type);

CREATE INDEX mention_name_idx IF NOT EXISTS
FOR (m:Mention) ON (m.name);

CREATE INDEX mention_modality_idx IF NOT EXISTS
FOR (m:Mention) ON (m.modality);
