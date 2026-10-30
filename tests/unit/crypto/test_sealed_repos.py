"""Regression tests for secret sealing at repository boundaries."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from cryptography.fernet import Fernet

from octop.infra.connectors.crypto import decrypt_credentials, encrypt_credentials
from octop.infra.crypto.fernet import (
    PURPOSE_PROVIDER,
    PURPOSE_STORAGE,
    PURPOSE_VOICE,
    open_text,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.providers import ProviderRepo
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.services import RepoBundle


def test_repo_bundle_seals_provider_voice_and_backend_secrets(tmp_path: Path) -> None:
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    repos = RepoBundle.from_pool(db)

    provider_id = repos.provider_repo.create(
        name="sealed-provider", kind="openai", api_key="provider-secret"
    )
    voice_id = repos.voice_provider_repo.create(
        name="sealed-voice", kind="openai", capability="tts", api_key="voice-secret"
    )
    backend_id = repos.storage_backend_repo.create(
        name="sealed-storage", kind="s3", secret_key="storage-secret"
    )

    assert repos.provider_repo.get(provider_id).api_key == "provider-secret"
    assert repos.voice_provider_repo.get(voice_id).api_key == "voice-secret"
    assert repos.storage_backend_repo.get(backend_id).secret_key == "storage-secret"
    with db.connect() as conn:
        raw_provider = conn.execute(
            "SELECT api_key FROM providers WHERE id = ?", (provider_id,)
        ).fetchone()["api_key"]
        raw_voice = conn.execute(
            "SELECT api_key FROM voice_providers WHERE id = ?", (voice_id,)
        ).fetchone()["api_key"]
        raw_storage = conn.execute(
            "SELECT secret_key FROM storage_backends WHERE id = ?", (backend_id,)
        ).fetchone()["secret_key"]
    assert raw_provider != "provider-secret"
    assert raw_voice != "voice-secret"
    assert raw_storage != "storage-secret"
    assert open_text(repos.secret_repo, PURPOSE_PROVIDER, raw_provider) == "provider-secret"
    assert open_text(repos.secret_repo, PURPOSE_VOICE, raw_voice) == "voice-secret"
    assert open_text(repos.secret_repo, PURPOSE_STORAGE, raw_storage) == "storage-secret"


def test_sealed_repo_reads_legacy_plaintext_and_preserves_update_semantics(
    tmp_path: Path,
) -> None:
    db = SqlitePool(tmp_path / "octop.db")
    run_migrations(db)
    inner = ProviderRepo(db)
    provider_id = inner.create(name="legacy-provider", kind="openai", api_key="legacy-secret")
    repos = RepoBundle.from_pool(db)
    assert repos.provider_repo.get(provider_id).api_key == "legacy-secret"

    repos.provider_repo.update(provider_id, note="changed")
    assert repos.provider_repo.get(provider_id).api_key == "legacy-secret"
    repos.provider_repo.update(provider_id, api_key="rotated-secret")
    assert repos.provider_repo.get(provider_id).api_key == "rotated-secret"
    repos.provider_repo.update(provider_id, api_key=None)
    assert repos.provider_repo.get(provider_id).api_key is None


def test_ciphertext_and_existing_credential_key_survive_database_backup_restore(
    tmp_path: Path,
) -> None:
    original = SqlitePool(tmp_path / "original.db")
    run_migrations(original)
    secrets = SecretRepo(original)
    prior_key = secrets.get_or_create("connector_fernet", Fernet.generate_key)
    connector_ciphertext = encrypt_credentials(secrets, {"token": "connector-secret"})
    repos = RepoBundle.from_pool(original)
    provider_id = repos.provider_repo.create(
        name="backup-provider", kind="openai", api_key="backup-secret"
    )
    provider_key = secrets.get("provider_fernet")
    assert provider_key is not None

    backup = tmp_path / "backup.db"
    with closing(sqlite3.connect(backup)) as destination:
        original._conn.backup(destination)
    restored = SqlitePool(backup)
    run_migrations(restored)
    restored_repos = RepoBundle.from_pool(restored)

    assert restored_repos.provider_repo.get(provider_id).api_key == "backup-secret"
    assert SecretRepo(restored).get("provider_fernet") == provider_key
    assert SecretRepo(restored).get("connector_fernet") == prior_key
    assert decrypt_credentials(SecretRepo(restored), connector_ciphertext) == {
        "token": "connector-secret"
    }
    with restored.connect() as conn:
        raw = conn.execute("SELECT api_key FROM providers WHERE id = ?", (provider_id,)).fetchone()[
            "api_key"
        ]
    assert raw != "backup-secret"
