from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
from contextlib import suppress
from pathlib import Path
from typing import Annotated

import pyotp
import typer
import uvicorn
from argon2 import PasswordHasher

from . import releases
from .connection import create_bundle, encode_bundle, validate_ca_file
from .crypto import (
    encrypt_secret,
    generate_release_signing_key,
    generate_signing_key,
    new_totp_secret,
    valid_totp,
)
from .storage import DATA_DIR, DB_PATH, audit, connect, migrate, now, transaction

app = typer.Typer(
    help="Standalone Maitu instance authorization center operations", no_args_is_help=True
)


@app.command()
def init(
    username: str = typer.Option(..., prompt=True),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
) -> None:
    """Initialize the center after verifying owner MFA and create its first signing key."""
    if len(password) < 12:
        raise typer.BadParameter("Password must contain at least 12 characters")
    migrate()
    with connect() as conn:
        if conn.execute("SELECT 1 FROM admin_users LIMIT 1").fetchone():
            raise typer.BadParameter("An administrator already exists")
    secret = new_totp_secret()
    typer.echo(f"TOTP secret (shown once): {secret}")
    typer.echo(
        f"Provisioning URI: {pyotp.TOTP(secret).provisioning_uri(username, issuer_name='Maitu License Center')}"
    )
    code = typer.prompt("Enter the current authenticator code to activate owner")
    ok, counter = valid_totp(secret, code)
    if not ok:
        with transaction() as conn:
            audit(
                conn,
                "local-cli",
                "admin.bootstrap_failed",
                "admin_user",
                username,
                "{}",
                "{}",
                "TOTP verification failed",
            )
        raise typer.BadParameter("TOTP verification failed; no owner or signing key was created")

    password_hash = PasswordHasher().hash(password)
    kid = "license-" + secrets.token_hex(8)
    private_path, public_pem = generate_signing_key(kid)
    try:
        with transaction() as conn:
            cur = conn.execute(
                "INSERT INTO admin_users(username,password_hash,role,totp_ciphertext,active,last_totp_counter,created_at) VALUES(?,?,'owner',?,1,?,?)",
                (username, password_hash, encrypt_secret(secret), counter, now()),
            )
            uid = cur.lastrowid
            audit(
                conn,
                "local-cli",
                "admin.bootstrap",
                "admin_user",
                str(uid),
                "{}",
                json.dumps({"username": username, "role": "owner"}),
            )
            conn.execute(
                "INSERT INTO signing_keys(kid,private_path,public_pem,state,created_at) VALUES(?,?,?,'active',?)",
                (kid, private_path.name, public_pem, now()),
            )
            audit(
                conn,
                "local-cli",
                "signing_key.bootstrap",
                "signing_key",
                kid,
                "{}",
                json.dumps({"state": "active"}),
            )
    except BaseException:
        private_path.unlink(missing_ok=True)
        raise
    with transaction() as conn:
        audit(
            conn,
            "local-cli",
            "admin.bootstrap_enrolled",
            "admin_user",
            str(uid),
            "{}",
            json.dumps({"active": True}),
        )
    typer.echo(f"Initialized center database at {DB_PATH}; active signing kid={kid}")
    typer.echo("Public key is available using `license-center export-trust`.")


@app.command()
def run(host: str = "127.0.0.1", port: int = 8088, workers: int = 1) -> None:
    """Run the API process. Production TLS must terminate at a trusted reverse proxy."""
    if workers != 1:
        raise typer.BadParameter("SQLite license center supports exactly one worker")
    if host not in {"127.0.0.1", "::1", "localhost"}:
        typer.echo(
            "Warning: bind only behind a trusted HTTPS reverse proxy; restrict the admin route.",
            err=True,
        )
    uvicorn.run(
        "license_center.api:app",
        host=host,
        port=port,
        workers=1,
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("LICENSE_CENTER_TRUSTED_PROXY_IPS", "127.0.0.1"),
    )


@app.command()
def backup(destination: Annotated[Path, typer.Argument(writable=True)]) -> None:
    """Create a WAL-consistent database snapshot using SQLite's backup API."""
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".tmp-" + secrets.token_hex(4))
    source = sqlite3.connect(DB_PATH, timeout=30)
    target = sqlite3.connect(temp)
    try:
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise typer.BadParameter("Backup failed SQLite integrity_check")
        target.commit()
    finally:
        target.close()
        source.close()
    os.replace(temp, destination)
    with suppress(OSError):
        destination.chmod(0o600)
    typer.echo(
        f"Consistent database backup written to {destination}. Back up the key directory separately using protected storage."
    )


@app.command()
def restore(
    source: Annotated[Path, typer.Argument(exists=True, readable=True)],
    confirm: bool = typer.Option(False, "--confirm"),
) -> None:
    """Restore a validated SQLite backup while the service is stopped."""
    if not confirm:
        raise typer.BadParameter(
            "Pass --confirm after stopping the service and safeguarding current data"
        )
    source = source.expanduser().resolve()
    check = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise typer.BadParameter("Backup failed SQLite integrity_check")
        if not check.execute("SELECT 1 FROM schema_migrations LIMIT 1").fetchone():
            raise typer.BadParameter("File is not a license-center database backup")
    finally:
        check.close()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = DB_PATH.with_name(DB_PATH.name + ".restore-tmp")
    shutil.copyfile(source, temp)
    with suppress(OSError):
        temp.chmod(0o600)
    os.replace(temp, DB_PATH)
    for suffix in ("-wal", "-shm"):
        side = Path(str(DB_PATH) + suffix)
        if side.exists():
            side.unlink()
    typer.echo(
        f"Restored database from {source}; signing/encryption key files are separate and must match this backup."
    )


@app.command("stage-key")
def stage_key() -> None:
    """Generate a staged signing key and print only its public trust material."""
    migrate()
    kid = "license-" + secrets.token_hex(8)
    path, public_pem = generate_signing_key(kid)
    with transaction() as conn:
        conn.execute(
            "INSERT INTO signing_keys(kid,private_path,public_pem,state,created_at) VALUES(?,?,?,'staged',?)",
            (kid, path.name, public_pem, now()),
        )
        audit(
            conn,
            "local-cli",
            "signing_key.stage",
            "signing_key",
            kid,
            "{}",
            json.dumps({"public_pem": public_pem, "state": "staged"}),
        )
    typer.echo(json.dumps({"kid": kid, "public_pem": public_pem, "state": "staged"}))


@app.command("activate-key")
def activate_key(kid: str, confirm: bool = typer.Option(False, "--confirm")) -> None:
    """Switch to a staged key after distributing its public key to trusted clients."""
    if not confirm:
        raise typer.BadParameter("Distribute the public key first, then pass --confirm")
    with transaction() as conn:
        row = conn.execute(
            "SELECT kid FROM signing_keys WHERE kid=? AND state='staged'", (kid,)
        ).fetchone()
        if row is None:
            raise typer.BadParameter("Staged signing key not found")
        old = [
            r["kid"]
            for r in conn.execute("SELECT kid FROM signing_keys WHERE state='active'").fetchall()
        ]
        conn.execute("UPDATE signing_keys SET state='retired' WHERE state='active'")
        conn.execute("UPDATE signing_keys SET state='active' WHERE kid=?", (kid,))
        audit(
            conn,
            "local-cli",
            "signing_key.activate",
            "signing_key",
            kid,
            json.dumps({"active": old}),
            json.dumps({"active": kid}),
        )
    typer.echo(f"Active signing key: {kid}")


@app.command("stage-release-key")
def stage_release_key(reason: str = typer.Option(..., "--reason")) -> None:
    """Stage a release signer; export its public keyring before activation."""
    migrate()
    releases._migrate_legacy_distribution()
    kid = "release-" + secrets.token_hex(8)
    path, public_pem = generate_release_signing_key(kid)
    with transaction() as conn:
        conn.execute(
            "INSERT INTO release_signing_keys(kid,private_path,public_pem,state,created_at) "
            "VALUES(?,?,?,'staged',?)",
            (kid, path.name, public_pem, now()),
        )
        audit(
            conn,
            "local-cli",
            "release_key.stage",
            "release_signing_key",
            kid,
            "{}",
            json.dumps({"public_pem": public_pem, "state": "staged"}),
            reason,
        )
    typer.echo(json.dumps({"kid": kid, "public_pem": public_pem, "state": "staged"}))


@app.command("activate-release-key")
def activate_release_key(
    kid: str,
    reason: str = typer.Option(..., "--reason"),
    confirm: bool = typer.Option(False, "--confirm"),
) -> None:
    """Activate a staged release signer after distributing its keyring."""
    if not confirm:
        raise typer.BadParameter("Export the new connection bundle first, then pass --confirm")
    migrate()
    with transaction() as conn:
        staged = conn.execute(
            "SELECT kid FROM release_signing_keys WHERE kid=? AND state='staged'", (kid,)
        ).fetchone()
        if staged is None:
            raise typer.BadParameter("Staged release signing key not found")
        old = [
            row["kid"]
            for row in conn.execute(
                "SELECT kid FROM release_signing_keys WHERE state='active'"
            ).fetchall()
        ]
        conn.execute("UPDATE release_signing_keys SET state='retired' WHERE state='active'")
        conn.execute("UPDATE release_signing_keys SET state='active' WHERE kid=?", (kid,))
        audit(
            conn,
            "local-cli",
            "release_key.activate",
            "release_signing_key",
            kid,
            json.dumps({"active": old}),
            json.dumps({"active": kid}),
            reason,
        )
    typer.echo(f"Active release signing kid: {kid}")


@app.command("export-connection")
def export_connection(
    destination: Annotated[Path, typer.Argument(writable=True)],
    server_url: str = typer.Option(..., "--server-url"),
    ca_file: Annotated[Path | None, typer.Option("--ca-file", exists=True, dir_okay=False)] = None,
    confirm: bool = typer.Option(False, "--confirm"),
) -> None:
    """Export public center connection settings for platform deployment."""
    try:
        migrate()
        releases._migrate_legacy_distribution()
        ca_certificate_pem = validate_ca_file(ca_file) if ca_file is not None else None
        with connect() as reader:
            license_rows = reader.execute(
                "SELECT kid,public_pem FROM signing_keys ORDER BY created_at,kid"
            ).fetchall()
            release_rows = reader.execute(
                "SELECT kid,public_pem FROM release_signing_keys ORDER BY created_at,kid"
            ).fetchall()
        bundle = create_bundle(
            server_url,
            {row["kid"]: row["public_pem"] for row in license_rows},
            {row["kid"]: row["public_pem"] for row in release_rows},
            ca_certificate_pem,
        )
        encoded = encode_bundle(bundle)
    except (ValueError, OSError, sqlite3.Error, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    destination = destination.expanduser().absolute()
    if destination.is_symlink():
        raise typer.BadParameter("Refusing to overwrite a symbolic link")
    if destination.exists():
        if not destination.is_file():
            raise typer.BadParameter("Destination exists and is not a regular file")
        try:
            if destination.read_text(encoding="utf-8") == encoded:
                typer.echo(f"Connection bundle already up to date at {destination}")
                return
        except (OSError, UnicodeDecodeError) as exc:
            raise typer.BadParameter(f"Cannot read existing destination: {destination}") from exc
        if not confirm:
            raise typer.BadParameter(
                "Destination differs; pass --confirm to replace the existing file"
            )
    temporary = destination.with_name(destination.name + ".tmp-" + secrets.token_hex(8))
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("x", encoding="utf-8", newline="") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        with suppress(OSError):
            temporary.chmod(0o644)
        os.replace(temporary, destination)
    except OSError as exc:
        raise typer.BadParameter(f"Could not write connection bundle: {destination}") from exc
    finally:
        with suppress(OSError):
            temporary.unlink()
    typer.echo(f"Public connection bundle exported to {destination}")


@app.command()
def recover(
    username: str = typer.Argument(...),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
    confirm: bool = typer.Option(False, "--confirm"),
) -> None:
    """Locally replace a lost admin MFA factor, revoke sessions, and audit recovery."""
    if not confirm:
        raise typer.BadParameter("Run this only on the center host; pass --confirm")
    if len(password) < 12:
        raise typer.BadParameter("Password must contain at least 12 characters")
    secret = new_totp_secret()
    with transaction() as conn:
        user = conn.execute("SELECT id FROM admin_users WHERE username=?", (username,)).fetchone()
        if user is None:
            raise typer.BadParameter("Administrator not found")
        conn.execute(
            "UPDATE admin_users SET password_hash=?,totp_ciphertext=?,active=0,last_totp_counter=-1 WHERE id=?",
            (PasswordHasher().hash(password), encrypt_secret(secret), user["id"]),
        )
        conn.execute("DELETE FROM admin_sessions WHERE user_id=?", (user["id"],))
        audit(
            conn,
            "local-cli",
            "admin.recovery",
            "admin_user",
            str(user["id"]),
            "{}",
            json.dumps({"active": False}),
            "MFA and password reset",
        )
    typer.echo(f"Recovery TOTP secret (shown once): {secret}")
    code = typer.prompt("Enter current authenticator code to complete recovery")
    ok, counter = valid_totp(secret, code)
    if not ok:
        raise typer.BadParameter("TOTP verification failed; account remains inactive")
    with transaction() as conn:
        conn.execute(
            "UPDATE admin_users SET active=1,last_totp_counter=? WHERE username=?",
            (counter, username),
        )
        audit(
            conn,
            "local-cli",
            "admin.recovery_enrolled",
            "admin_user",
            username,
            "{}",
            json.dumps({"active": True}),
        )
    typer.echo("Account recovered; all prior sessions were invalidated.")
