-- Offline-first issuance continued producing prefixed IDs after migration 004.
-- Repair those rows once, preserving denial state and advancing their revision.
BEGIN IMMEDIATE;
CREATE TEMP TABLE offline_license_id_map(old_id TEXT PRIMARY KEY, new_id TEXT NOT NULL);
INSERT INTO offline_license_id_map(old_id,new_id)
SELECT id,substr(id,5) FROM licenses
WHERE substr(id,1,4)='lic_' AND length(id)=36 AND substr(id,5) NOT GLOB '*[^0-9a-f]*';
UPDATE audit_log
SET object_id=(SELECT new_id FROM offline_license_id_map WHERE old_id=audit_log.object_id)
WHERE object_type='license' AND object_id IN (SELECT old_id FROM offline_license_id_map);
UPDATE licenses
SET id=(SELECT new_id FROM offline_license_id_map WHERE old_id=licenses.id),revision=revision+1
WHERE id IN (SELECT old_id FROM offline_license_id_map);
DROP TABLE offline_license_id_map;
COMMIT;
