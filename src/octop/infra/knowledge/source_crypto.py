"""Fernet encryption for a source's connection secret.

Same mechanism the connector credentials already use — a key kept in ``secrets``
and a JSON payload — under this domain's own key name, so a knowledge source's
secret and a connector's are rotated and reasoned about separately. The two
modules are deliberately not shared: ``infra/connectors`` is another domain's
package, and routing knowledge sources through it would make this domain depend
on that one for no gain beyond twenty lines.

A secret never leaves this module as text: callers store the blob and hand back
a ``has_credentials`` flag, which is what keeps design §4's "API 不返回明文凭据"
true by construction rather than by remembering it at each route.
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet

from octop.infra.db.repos.secrets import SecretRepo

_FERNET_KEY = "knowledge_source_fernet"


def _get_fernet(repo: SecretRepo) -> Fernet:
    return Fernet(repo.get_or_create(_FERNET_KEY, Fernet.generate_key))


def encrypt_source_secret(repo: SecretRepo, payload: dict[str, Any]) -> bytes:
    """Encrypt one source's connection secret."""
    return _get_fernet(repo).encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


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
        data = json.loads(_get_fernet(repo).decrypt(bytes(blob)).decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}
