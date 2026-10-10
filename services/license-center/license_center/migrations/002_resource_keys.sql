PRAGMA foreign_keys=OFF;
BEGIN IMMEDIATE;

CREATE TABLE customers_new (
    pk INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    contact TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
);
INSERT INTO customers_new(id,name,contact,notes,active,version,created_at)
SELECT id,name,contact,notes,active,version,created_at FROM customers;

CREATE TABLE instances_new (
    pk INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    public_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active',
    last_seen_at INTEGER
);
INSERT INTO instances_new(id,customer_id,public_key,fingerprint,status,last_seen_at)
SELECT id,customer_id,public_key,fingerprint,status,last_seen_at FROM instances;

CREATE TABLE licenses_new (
    pk INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    instance_id TEXT NOT NULL UNIQUE REFERENCES instances(id),
    expires_at INTEGER NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    expired INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
);
INSERT INTO licenses_new(id,instance_id,expires_at,revoked,expired,revision,created_at)
SELECT id,instance_id,expires_at,revoked,expired,revision,created_at FROM licenses;

CREATE TABLE activation_codes_new (
    pk INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    digest TEXT NOT NULL UNIQUE,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    license_expires_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER,
    bound_instance_id TEXT,
    revoked_at INTEGER
);
INSERT INTO activation_codes_new(id,digest,customer_id,license_expires_at,expires_at,used_at,bound_instance_id,revoked_at)
SELECT id,digest,customer_id,license_expires_at,expires_at,used_at,bound_instance_id,revoked_at FROM activation_codes;

DROP TABLE licenses;
DROP TABLE instances;
DROP TABLE activation_codes;
DROP TABLE customers;
ALTER TABLE customers_new RENAME TO customers;
ALTER TABLE instances_new RENAME TO instances;
ALTER TABLE licenses_new RENAME TO licenses;
ALTER TABLE activation_codes_new RENAME TO activation_codes;
CREATE INDEX idx_activation_customer ON activation_codes(customer_id);

COMMIT;
PRAGMA foreign_keys=ON;
