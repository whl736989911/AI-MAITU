"""Fernet encryption for a source's connection secret.

Uses the shared Fernet primitives with a separate purpose key, so knowledge
source credentials remain cryptographically isolated from connector credentials.
A secret never leaves this module as text: callers store the blob and hand back
a ``has_credentials`` flag, which is what keeps design §4's "API 不返回明文凭据"
true by construction rather than by remembering it at each route.
"""

from __future__ import annotations

from typing import Any

from octop.infra.crypto.fernet import (
    PURPOSE_KNOWLEDGE_SOURCE,
    decrypt_json,
    encrypt_json,
)
from octop.infra.db.repos.secrets import SecretRepo


def encrypt_source_secret(repo: SecretRepo, payload: dict[str, Any]) -> bytes:
    """Encrypt one source's connection secret."""
    return encrypt_json(repo, PURPOSE_KNOWLEDGE_SOURCE, payload)


def decrypt_source_secret(repo: SecretRepo, blob: bytes | None) -> dict[str, Any]:
    """Decrypt a stored secret, degrading to ``{}`` on an unreadable blob.

    An empty secret is a real state — a share that allows anonymous or
    guest access — so this is not an error path, and an undecryptable blob
    (a rotated key) behaves the same way: the connection then fails with the
    transport's own message instead of crashing the request.
    """
    if not blob:
        return {}
    try:
        return decrypt_json(repo, PURPOSE_KNOWLEDGE_SOURCE, bytes(blob))
    except Exception:
        return {}
