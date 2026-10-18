ALTER TABLE licenses ADD COLUMN restored INTEGER NOT NULL DEFAULT 0;
CREATE TEMP TABLE license_id_contract_map(old_id TEXT PRIMARY KEY, new_id TEXT NOT NULL);
INSERT INTO license_id_contract_map(old_id,new_id)
SELECT id,substr(id,5) FROM licenses
WHERE substr(id,1,4)='lic_' AND length(id)=36 AND substr(id,5) NOT GLOB '*[^0-9a-f]*';
UPDATE audit_log
SET object_id=(SELECT new_id FROM license_id_contract_map WHERE old_id=audit_log.object_id)
WHERE object_type='license' AND object_id IN (SELECT old_id FROM license_id_contract_map);
UPDATE licenses
SET id=(SELECT new_id FROM license_id_contract_map WHERE old_id=licenses.id),revision=revision+1
WHERE id IN (SELECT old_id FROM license_id_contract_map);
DROP TABLE license_id_contract_map;
