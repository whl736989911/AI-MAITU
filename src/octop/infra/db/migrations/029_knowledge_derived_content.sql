-- Schema v29: the derived content a parsed document yields.
--
-- Design §3.4 (``ExtractionResult``) stores "the body text and the unified
-- document structure" as derived content, and §6.2 defines that structure:
-- title, sections, tables, images, pages, metadata. It is written once, by the
-- index pipeline, right after a file parses.
--
-- Why the structure is stored rather than recomputed on demand: opening a
-- ``.doc`` costs a LibreOffice process (design §6.1), so anything that wants to
-- look at a document's shape twice would pay that twice. The *text* is
-- deliberately not duplicated here — the chunk table already holds it, and
-- ``derived_json`` is what the text cannot express: which sheet a table came
-- from, which heading a paragraph sits under, how many pages the PDF declared.
--
-- SQLite boots apply this through
-- migrate.py::_ensure_knowledge_derived_schema (idempotent, re-run every boot
-- so a database whose watermark skipped 29 still converges). The statement
-- below is what that helper executes, listed here so the file stays the
-- readable record of the change; this file is not executed on SQLite.

ALTER TABLE knowledge_documents ADD COLUMN derived_json TEXT NOT NULL DEFAULT '';

UPDATE _schema_version SET version = 29;
