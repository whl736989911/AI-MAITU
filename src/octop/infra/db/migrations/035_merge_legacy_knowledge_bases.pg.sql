-- Schema v35: every legacy user knowledge base is folded into the enterprise space.
--
-- Read this together with 035_merge_legacy_knowledge_bases.sql; the reasoning is
-- there and is identical for both dialects. What differs is only that PostgreSQL
-- runs the statements below, while SQLite runs the same steps as the helper
-- migrate.py::_merge_legacy_knowledge_bases.
--
-- The split is deliberate and narrow. Everything that can be said in SQL is said
-- here, in the same order and for the same reasons as the record file. What is
-- *not* here is the part SQL cannot do: moving the documents' files on disk. The
-- helper runs after this file and does exactly that — plus the steps below, which
-- it re-applies as no-ops, so a boot whose watermark skipped 35 converges through
-- the helper alone and both paths end in the same state.
--
-- Two orderings are load-bearing.
--
--   * A document's audience is read from the base it still belongs to, so every
--     ``resource_acl`` row is written *before* the ``kb_id`` update below.
--   * The audience has to be written at all, because the space is public (v27):
--     silence would migrate a file that only some people could read into
--     everyone's library, which is what design §14 forbids.

-- 1. One file-level entry per document, carrying its base's audience. A document
--    that already wears an entry of its own keeps it: that rule was written on
--    purpose, and this migration is not the place to overrule it.
INSERT INTO resource_acl(
  resource_type, resource_id, owner_user_id, visibility, unit_key, version, updated_at
)
SELECT 'knowledge_document', d.document_id, a.owner_user_id, a.visibility, a.unit_key,
       a.version, EXTRACT(EPOCH FROM NOW())::bigint
FROM knowledge_documents d
JOIN knowledge_bases b
  ON b.knowledge_base_id = d.kb_id AND b.is_enterprise = 0
JOIN resource_acl a
  ON a.resource_type = 'knowledge_base' AND a.resource_id = d.kb_id
WHERE NOT EXISTS (
  SELECT 1 FROM resource_acl x
  WHERE x.resource_type = 'knowledge_document' AND x.resource_id = d.document_id
)
ON CONFLICT (resource_type, resource_id) DO NOTHING;

-- 2. A base with no ACL row of its own reached administrators only. The space
--    reaches everyone, so those documents need the narrow rule spelled out
--    rather than inherited.
INSERT INTO resource_acl(
  resource_type, resource_id, owner_user_id, visibility, unit_key, version, updated_at
)
SELECT 'knowledge_document', d.document_id, NULL, 'private', NULL, 1,
       EXTRACT(EPOCH FROM NOW())::bigint
FROM knowledge_documents d
JOIN knowledge_bases b
  ON b.knowledge_base_id = d.kb_id AND b.is_enterprise = 0
WHERE NOT EXISTS (
  SELECT 1 FROM resource_acl a
  WHERE a.resource_type = 'knowledge_base' AND a.resource_id = d.kb_id
)
AND NOT EXISTS (
  SELECT 1 FROM resource_acl x
  WHERE x.resource_type = 'knowledge_document' AND x.resource_id = d.document_id
)
ON CONFLICT (resource_type, resource_id) DO NOTHING;

-- 3. The base's grants come along, one document at a time.
INSERT INTO resource_acl_grants(resource_type, resource_id, grantee_type, grantee_id)
SELECT 'knowledge_document', d.document_id, g.grantee_type, g.grantee_id
FROM knowledge_documents d
JOIN knowledge_bases b
  ON b.knowledge_base_id = d.kb_id AND b.is_enterprise = 0
JOIN resource_acl_grants g
  ON g.resource_type = 'knowledge_base' AND g.resource_id = d.kb_id
WHERE NOT EXISTS (
  SELECT 1 FROM resource_acl_grants x
  WHERE x.resource_type = 'knowledge_document' AND x.resource_id = d.document_id
)
ON CONFLICT DO NOTHING;

-- 4. The documents and their data sources move into the space. Their bytes move
--    with them in the helper: a stored document's path is derived from the base
--    it belongs to, so a row that moved without its file would be a document the
--    space lists and cannot open.
UPDATE knowledge_documents
SET kb_id = (SELECT knowledge_base_id FROM knowledge_bases WHERE is_enterprise = 1),
    updated_at = EXTRACT(EPOCH FROM NOW())::bigint
WHERE kb_id IN (SELECT knowledge_base_id FROM knowledge_bases WHERE is_enterprise = 0);

UPDATE data_sources
SET knowledge_base_id = (SELECT knowledge_base_id FROM knowledge_bases WHERE is_enterprise = 1)
WHERE knowledge_base_id IN (
  SELECT knowledge_base_id FROM knowledge_bases WHERE is_enterprise = 0
);

-- 5. The emptied bases, their ACL rows and their grants are dropped, and the
--    space's ``doc_count`` is recomputed from what it actually holds. design §1
--    is one logical knowledge base per enterprise, and an emptied one is not a
--    second library — it is a name and an ACL row for documents that live in the
--    space.
DELETE FROM resource_acl_grants
WHERE resource_type = 'knowledge_base'
  AND resource_id IN (SELECT knowledge_base_id FROM knowledge_bases WHERE is_enterprise = 0);

DELETE FROM resource_acl
WHERE resource_type = 'knowledge_base'
  AND resource_id IN (SELECT knowledge_base_id FROM knowledge_bases WHERE is_enterprise = 0);

DELETE FROM knowledge_bases WHERE is_enterprise = 0;

UPDATE knowledge_bases SET doc_count = (
  SELECT COUNT(*) FROM knowledge_documents d
  WHERE d.kb_id = knowledge_bases.knowledge_base_id AND d.is_dir = 0
) WHERE is_enterprise = 1;

-- 6. An agent that named a legacy base names the space afterwards, because that
--    is where the documents it was reading went. It is the helper's job in both
--    dialects: the list is JSON text, and rewriting it means parsing it.
