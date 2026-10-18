"""One-way adoption of the former standalone license ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid

from . import crypto, storage

_SOURCE = "legacy-licensing-center-v1"


def migrate_legacy_licensing() -> None:
    legacy_db = storage.DATA_DIR / "licensing.sqlite3"
    if not legacy_db.is_file():
        return
    with storage.connect() as conn:
        if conn.execute("SELECT 1 FROM center_imports WHERE source=?", (_SOURCE,)).fetchone():
            return
    key_file = storage.DATA_DIR / "release-signing-key.pem"
    if not key_file.is_file():
        raise RuntimeError(
            "Legacy licensing.sqlite3 needs its original release-signing-key.pem; restore the signer backup before migration"
        )
    source = sqlite3.connect(legacy_db.as_uri() + "?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        licenses = source.execute("SELECT * FROM licenses ORDER BY created_at,id").fetchall()
        codes = source.execute("SELECT * FROM activation_codes").fetchall()
        events = source.execute("SELECT * FROM license_audit ORDER BY id").fetchall()
    finally:
        source.close()
    kid = "maitu-license-1"
    path, public_pem = crypto.import_license_signing_key(kid, key_file.read_bytes())
    customers: dict[str, str] = {}
    by_id = {row["id"]: row for row in licenses}
    with storage.transaction() as conn:
        if conn.execute("SELECT 1 FROM center_imports WHERE source=?", (_SOURCE,)).fetchone():
            return
        key = conn.execute("SELECT public_pem FROM signing_keys WHERE kid=?", (kid,)).fetchone()
        if key is not None and key["public_pem"] != public_pem:
            raise RuntimeError("Legacy signer conflicts with an existing license key ID")
        if key is None:
            active = conn.execute("SELECT 1 FROM signing_keys WHERE state='active'").fetchone()
            conn.execute(
                "INSERT INTO signing_keys(kid,private_path,public_pem,state,created_at) VALUES(?,?,?,?,?)",
                (kid, path.name, public_pem, "retired" if active else "active", storage.now()),
            )
        for row in licenses:
            customer = "cus_" + uuid.uuid5(uuid.NAMESPACE_URL, _SOURCE + ":" + row["id"]).hex
            customers[row["id"]] = customer
            conn.execute(
                "INSERT INTO customers(id,name,notes,created_at) VALUES(?,?,?,?)",
                (
                    customer,
                    row["customer_name"],
                    json.dumps({"imported_license_id": row["id"], "revision": row["version"]}),
                    row["created_at"],
                ),
            )
            if row["instance_id"] is None:
                # Unbound grants have never issued an instance credential. Their
                # activation codes retain expiry/revocation below; the original
                # ledger and audit retain their pre-activation revision history.
                continue
            if (
                not row["public_key_pem"]
                or crypto.fingerprint(row["public_key_pem"]) != row["key_fingerprint"]
            ):
                raise RuntimeError("Legacy license instance key binding is inconsistent")
            conn.execute(
                "INSERT INTO instances(id,customer_id,public_key,fingerprint,status) VALUES(?,?,?,?,?)",
                (
                    row["instance_id"],
                    customer,
                    row["public_key_pem"],
                    row["key_fingerprint"],
                    "active",
                ),
            )
            conn.execute(
                "INSERT INTO licenses(id,instance_id,expires_at,revoked,revision,created_at,restored) VALUES(?,?,?,?,?,?,?)",
                (
                    row["id"],
                    row["instance_id"],
                    row["license_expires_at"],
                    int(row["status"] == "revoked"),
                    row["version"],
                    row["created_at"],
                    row["restored"],
                ),
            )
        for code in codes:
            lic = by_id[code["license_id"]]
            code_id = "act_" + uuid.uuid5(uuid.NAMESPACE_URL, _SOURCE + ":" + code["digest"]).hex
            conn.execute(
                "INSERT INTO activation_codes(id,digest,customer_id,license_expires_at,expires_at,used_at,bound_instance_id,revoked_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    code_id,
                    code["digest"],
                    customers[code["license_id"]],
                    lic["license_expires_at"],
                    code["expires_at"],
                    code["used_at"],
                    code["instance_id"],
                    lic["updated_at"] if lic["status"] == "revoked" else None,
                ),
            )
        for event in events:
            conn.execute(
                "INSERT INTO audit_log(actor,action,object_type,object_id,before_json,after_json,reason,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    "legacy:" + event["actor"],
                    event["action"],
                    "license",
                    event["license_id"] or "",
                    "{}",
                    event["details"],
                    _SOURCE,
                    event["at"],
                ),
            )
        conn.execute(
            "INSERT INTO center_imports(source,imported_at) VALUES(?,?)", (_SOURCE, storage.now())
        )
        storage.audit(
            conn,
            "local-system",
            "license.legacy_import",
            "migration",
            _SOURCE,
            "{}",
            json.dumps(
                {
                    "licenses": len(licenses),
                    "activation_codes": len(codes),
                    "signer_sha256": hashlib.sha256(public_pem.encode()).hexdigest(),
                }
            ),
        )
