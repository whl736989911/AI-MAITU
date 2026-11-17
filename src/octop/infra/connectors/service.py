"""Connector service — MCP config assembly and credential access."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from octop.config import OctopConfig
from octop.infra.connectors import qcc
from octop.infra.connectors.builder import build_http_mcp_spec, mcp_server_name, new_internal_token
from octop.infra.connectors.catalog import get_catalog_entry, uses_internal_http_mcp
from octop.infra.connectors.crypto import decrypt_credentials, encrypt_credentials
from octop.infra.connectors.custom_mcp import (
    CUSTOM_MCP_DISPLAY_NAME,
    CUSTOM_MCP_KIND,
    build_oauth_storage,
    enabled_harness_configs,
    expand_custom_instances,
    extract_servers,
    is_custom_mcp_kind,
    mark_oauth_reauth_required,
    merge_preserved_oauth,
    oauth_configured,
    oauth_tokens_from_spec,
    redact_servers_for_api,
    server_enabled,
    set_oauth_required_in_spec,
    shared_mcp_server_name,
    validate_servers_map,
    wrap_servers,
)
from octop.infra.connectors.default_open import merge_mcp_servers_with_defaults, read_default_open
from octop.infra.connectors.gateway.cli_dirs import (
    cleanup_creds_cli_dirs,
    cleanup_keys_for_creds,
    resolve_cli_config_key,
)
from octop.infra.connectors.gateway.feishu_user_auth import (
    complete_user_device_login,
    start_user_device_login,
)
from octop.infra.connectors.oauth import refresh_oauth_credentials
from octop.infra.connectors.oauth.registry import refresh_custom_mcp_oauth
from octop.infra.db.repos.connectors import ConnectorRepo, ConnectorRow
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.paths import PathLayout
from octop.infra.utils.ulid import new_ulid

logger = logging.getLogger(__name__)

_OAUTH_REFRESH_SKEW_SEC = 120


class ConnectorNameTakenError(ValueError):
    """Raised when a connector display name is already owned by the user."""


def list_user_connector_instances(
    repo: ConnectorRepo,
    user_id: int,
    *,
    active_only: bool = False,
    with_credentials: bool = False,
) -> list[ConnectorRow]:
    """List connector instances for *user_id* with optional filters."""
    rows = repo.list_by_user(user_id)
    if active_only:
        rows = [r for r in rows if r.status == "active"]
    if with_credentials:
        rows = [r for r in rows if r.has_credentials]
    return rows


class ConnectorService:
    def __init__(
        self,
        *,
        repo: ConnectorRepo,
        secret_repo: SecretRepo,
        settings_repo: Any,
        config: OctopConfig,
    ) -> None:
        self._repo = repo
        self._secret_repo = secret_repo
        self._settings_repo = settings_repo
        self._config = config

    def list_user_instances(
        self,
        user_id: int,
        *,
        active_only: bool = False,
        with_credentials: bool = False,
    ) -> list[ConnectorRow]:
        return list_user_connector_instances(
            self._repo,
            user_id,
            active_only=active_only,
            with_credentials=with_credentials,
        )

    def decrypt(self, instance_id: str) -> dict[str, Any]:
        row = self._repo.get(instance_id)
        if row is None or not row.credential_blob:
            return {}
        return decrypt_credentials(self._secret_repo, row.credential_blob)

    def prepare_credentials_for_storage(
        self,
        *,
        instance_id: str,
        payload: dict[str, Any],
    ) -> tuple[bytes, int | None]:
        """Encrypt credentials with stable instance-scoped internal auth preserved."""
        stored = dict(payload)
        row = self._repo.get(instance_id)
        if row is not None:
            entry = get_catalog_entry(row.kind)
            if entry is not None and uses_internal_http_mcp(entry):
                existing = (
                    decrypt_credentials(self._secret_repo, row.credential_blob)
                    if row.credential_blob
                    else {}
                )
                stored["internal_token"] = (
                    existing.get("internal_token")
                    or stored.get("internal_token")
                    or new_internal_token()
                )
        stored["instance_id"] = instance_id
        expires_at = stored.get("expires_at")
        exp = int(expires_at) if expires_at is not None else None
        return encrypt_credentials(self._secret_repo, stored), exp

    def encrypt_and_store(
        self,
        *,
        instance_id: str,
        payload: dict[str, Any],
    ) -> None:
        blob, expires_at = self.prepare_credentials_for_storage(
            instance_id=instance_id, payload=payload
        )
        self._repo.upsert_credentials(instance_id=instance_id, blob=blob, expires_at=expires_at)

    async def ensure_fresh_credentials(
        self,
        instance_id: str,
        kind: str,
    ) -> dict[str, Any]:
        if kind == "qcc":
            row = self._repo.get(instance_id)
            if row is None or row.kind != "qcc" or row.status != "active":
                return {}
            return self.decrypt(instance_id)
        row = self._repo.get(instance_id)
        if row is None or not row.credential_blob:
            return {}
        original_blob = row.credential_blob
        creds = decrypt_credentials(self._secret_repo, original_blob)
        entry = get_catalog_entry(kind)
        if entry is None or entry.auth_kind != "oauth2":
            return creds
        expires_at = creds.get("expires_at")
        if expires_at and int(expires_at) > int(time.time()) + 120:
            return creds
        refresh = str(creds.get("refresh_token") or "")
        if not refresh:
            return creds
        try:
            refreshed = await refresh_oauth_credentials(
                kind=kind,
                creds=creds,
                settings_repo=self._settings_repo,
            )
        except Exception:
            return self.decrypt(instance_id)
        creds.update(refreshed)
        stored = dict(creds)
        stored["instance_id"] = instance_id
        expires = stored.get("expires_at")
        exp = int(expires) if expires is not None else None
        blob = encrypt_credentials(self._secret_repo, stored)
        if self._repo.replace_credentials_if_unchanged(
            instance_id=instance_id,
            expected_blob=original_blob,
            blob=blob,
            expires_at=exp,
        ):
            return creds
        # Reauthorization, credential replacement, or deletion won while the
        # provider refresh was in flight; never return or persist the stale identity.
        return self.decrypt(instance_id)

    async def cleanup_cli_dirs_after_change(
        self,
        kind: str,
        old_creds: dict[str, Any],
        new_creds: dict[str, Any] | None = None,
        *,
        exclude_instance_id: str,
    ) -> None:
        """Best-effort remove rotated CLI state only when no instance owns it."""
        if kind not in ("feishu-cli", "wecom-cli"):
            return
        try:
            retained: set[str] = set()
            for row in self._repo.list_by_kind(kind):
                if row.instance_id == exclude_instance_id:
                    continue
                # Unreadable credentials make ownership uncertain: keep all state.
                creds = self.decrypt(row.instance_id)
                retained.update(
                    cleanup_keys_for_creds(kind, {**creds, "instance_id": row.instance_id})
                )
            if new_creds is not None:
                retained.update(
                    cleanup_keys_for_creds(kind, {**new_creds, "instance_id": exclude_instance_id})
                )
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: cleanup_creds_cli_dirs(
                    kind,
                    {**old_creds, "instance_id": exclude_instance_id},
                    keep=retained,
                ),
            )
        except Exception:
            logger.warning("failed to clean connector CLI state for %s", exclude_instance_id)

    async def _qcc_request(
        self, instance_id: str, resource: str, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        creds = await self.ensure_fresh_credentials(instance_id, "qcc")
        token = qcc.bearer_token(creds)
        if not token:
            raise ValueError("QCC connector is disconnected")
        return await qcc.request_resource(resource, token, method, params)

    async def handle_qcc_request(self, instance_id: str, body: dict[str, Any]) -> dict[str, Any]:
        from octop.infra.connectors.gateway.protocol import handle_mcp_request

        method = body.get("method")
        if method not in ("tools/list", "tools/call"):
            return handle_mcp_request(kind="qcc", creds={}, body=body)
        try:
            if method == "tools/list":
                listed = await asyncio.gather(
                    *[
                        self._qcc_request(instance_id, resource, "tools/list", {})
                        for resource in qcc.RESOURCES
                    ],
                    return_exceptions=True,
                )
                tools: list[dict[str, Any]] = []
                for resource, item in zip(qcc.RESOURCES, listed, strict=True):
                    if isinstance(item, BaseException):
                        continue
                    tools.extend(qcc.namespace_tools(resource, item))
                if not tools:
                    raise ValueError("QCC MCP request failed; check API Key or try again")
                result: dict[str, Any] = {"tools": tools}
            else:
                params = dict(body.get("params") or {})
                resource, sep, name = str(params.get("name") or "").partition("__")
                if not sep or resource not in qcc.RESOURCES or not name:
                    raise ValueError("Unknown QCC tool")
                result = await self._qcc_request(
                    instance_id, resource, "tools/call", {**params, "name": name}
                )
            return {"jsonrpc": "2.0", "id": body.get("id"), "result": result}
        except Exception:
            # Avoid returning transport exceptions that may contain credentials.
            return {
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "error": {
                    "code": -32603,
                    "message": "QCC MCP request failed; check API Key or try again",
                },
            }

    def reserved_builtin_mcp_names(self, user_id: int) -> set[str]:
        names: set[str] = set()
        for inst in self._repo.list_by_user(user_id):
            if is_custom_mcp_kind(inst.kind):
                continue
            names.add(inst.mcp_server_name)
        return names

    def get_custom_servers(self, user_id: int) -> dict[str, Any]:
        row = self._repo.get_by_user_kind(user_id, CUSTOM_MCP_KIND)
        if row is None or not row.has_credentials:
            return {}
        return extract_servers(self.decrypt(row.instance_id))

    def shared_custom_harness_aliases(self, owner_user_id: int) -> dict[str, str]:
        """Map viewer collision-safe names to the owner's short harness names."""
        parent = self._repo.get_by_user_kind(owner_user_id, CUSTOM_MCP_KIND)
        if parent is None or not parent.has_credentials:
            return {}
        aliases: dict[str, str] = {}
        for name, spec in extract_servers(self.decrypt(parent.instance_id)).items():
            if isinstance(spec, dict) and spec.get("shared") is True and server_enabled(spec):
                aliases[shared_mcp_server_name(parent.instance_id, name)] = name
        return aliases

    def present_mcp_names_for_viewer(
        self,
        *,
        owner_user_id: int | None,
        viewer_user_id: int,
        names: list[str],
    ) -> list[str]:
        """Translate owner harness names to visible viewer catalog names."""
        if owner_user_id is None or owner_user_id == viewer_user_id:
            return list(names)
        aliases = self.shared_custom_harness_aliases(owner_user_id)
        reverse = {short: long for long, short in aliases.items()}
        owner_custom = self.get_custom_servers(owner_user_id)
        allowed = set(self.list_active_mcp_server_names(viewer_user_id))
        result: list[str] = []
        for name in names:
            if name in owner_custom and name not in reverse:
                continue
            shown = reverse.get(name, name)
            if shown in allowed and shown not in result:
                result.append(shown)
        return result

    def get_custom_servers_for_api(self, user_id: int) -> dict[str, Any]:
        return redact_servers_for_api(self.get_custom_servers(user_id))

    def put_custom_servers(self, user_id: int, servers: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_custom_servers(user_id)
        merged = merge_preserved_oauth(servers, existing)
        return self._save_custom_servers(user_id, merged)

    def _save_custom_servers(self, user_id: int, servers: dict[str, Any]) -> dict[str, Any]:
        normalized = validate_servers_map(
            servers,
            reserved_names=self.reserved_builtin_mcp_names(user_id),
        )
        display_names: set[str] = set()
        for name, spec in normalized.items():
            display_name = (
                str(spec.get("display_name") or "").strip() if isinstance(spec, dict) else ""
            ) or name
            if display_name in display_names or self._repo.name_exists(user_id, display_name):
                raise ConnectorNameTakenError(display_name)
            display_names.add(display_name)
        row = self._repo.get_by_user_kind(user_id, CUSTOM_MCP_KIND)
        if not normalized:
            if row is not None:
                self._repo.delete(row.instance_id)
            return {}
        if row is None:
            instance_id = new_ulid()
            self._repo.create(
                instance_id=instance_id,
                user_id=user_id,
                kind=CUSTOM_MCP_KIND,
                display_name=CUSTOM_MCP_DISPLAY_NAME,
                mcp_server_name=mcp_server_name(CUSTOM_MCP_KIND, instance_id),
            )
        else:
            instance_id = row.instance_id
        self.encrypt_and_store(
            instance_id=instance_id,
            payload=wrap_servers(normalized),
        )
        return normalized

    def apply_custom_server_oauth(
        self,
        user_id: int,
        server_name: str,
        tokens: dict[str, Any],
        *,
        issuer: str,
        resource: str | None,
    ) -> dict[str, Any]:
        servers = dict(self.get_custom_servers(user_id))
        if server_name not in servers:
            raise KeyError(server_name)
        access = str(tokens.get("access_token") or "").strip()
        if not access:
            raise ValueError("missing access_token")
        spec = dict(servers[server_name])
        spec["oauth"] = build_oauth_storage(tokens, issuer=issuer, resource=resource)
        spec = set_oauth_required_in_spec(spec, required=False)
        servers[server_name] = spec
        return self._save_custom_servers(user_id, servers)

    async def ensure_fresh_custom_servers(self, user_id: int) -> dict[str, Any]:
        row = self._repo.get_by_user_kind(user_id, CUSTOM_MCP_KIND)
        if row is None or not row.credential_blob:
            return {}
        original_blob = row.credential_blob
        stored = decrypt_credentials(self._secret_repo, original_blob)
        servers = dict(extract_servers(stored))
        changed = False
        now = int(time.time())
        for name, raw in list(servers.items()):
            if not isinstance(raw, dict):
                continue
            oauth = oauth_tokens_from_spec(raw)
            if not oauth_configured(raw):
                continue
            expires_at_raw = oauth.get("expires_at")
            expires_at = int(expires_at_raw) if expires_at_raw is not None else None
            refresh = str(oauth.get("refresh_token") or "").strip()
            if expires_at is not None and expires_at <= now and not refresh:
                servers[name] = mark_oauth_reauth_required(dict(raw))
                changed = True
                continue
            if not refresh:
                continue
            if expires_at is not None and expires_at > now + _OAUTH_REFRESH_SKEW_SEC:
                continue
            try:
                refreshed = await refresh_custom_mcp_oauth(oauth)
            except Exception:
                logger.warning(
                    "custom MCP oauth refresh failed for %r (user_id=%s)",
                    name,
                    user_id,
                    exc_info=True,
                )
                if expires_at is not None and expires_at <= now:
                    servers[name] = mark_oauth_reauth_required(dict(raw))
                    changed = True
                continue
            spec = dict(raw)
            spec["oauth"] = refreshed
            servers[name] = spec
            changed = True
        if changed:
            normalized = validate_servers_map(
                servers, reserved_names=self.reserved_builtin_mcp_names(user_id)
            )
            stored.update(wrap_servers(normalized))
            stored["instance_id"] = row.instance_id
            self._repo.replace_credentials_if_unchanged(
                instance_id=row.instance_id,
                expected_blob=original_blob,
                blob=encrypt_credentials(self._secret_repo, stored),
            )
        # A newer configuration or reauthorization always wins over this refresh,
        # including when the provider failed or the connector was removed.
        return self.get_custom_servers(user_id)

    def patch_custom_server_enabled(
        self,
        user_id: int,
        server_name: str,
        *,
        enabled: bool,
    ) -> dict[str, Any]:
        return self.patch_custom_server(user_id, server_name, enabled=enabled)

    def patch_custom_server(
        self,
        user_id: int,
        server_name: str,
        *,
        enabled: bool | None = None,
        default_open: bool | None = None,
        shared: bool | None = None,
    ) -> dict[str, Any]:
        servers = dict(self.get_custom_servers(user_id))
        if server_name not in servers:
            raise KeyError(server_name)
        spec = dict(servers[server_name])
        if enabled is not None:
            spec["enabled"] = enabled
            if not enabled:
                spec.pop("default_open", None)
        if default_open is not None:
            if default_open:
                spec["default_open"] = True
            else:
                spec.pop("default_open", None)
        if shared is not None:
            if shared:
                spec["shared"] = True
            else:
                spec.pop("shared", None)
        servers[server_name] = spec
        return self.put_custom_servers(user_id, servers)

    def note_custom_server_oauth_required(
        self,
        user_id: int,
        server_name: str,
        *,
        required: bool,
    ) -> dict[str, Any]:
        servers = dict(self.get_custom_servers(user_id))
        if server_name not in servers:
            raise KeyError(server_name)
        spec = dict(servers[server_name])
        servers[server_name] = set_oauth_required_in_spec(spec, required=required)
        return self._save_custom_servers(user_id, servers)

    def patch_custom_server_default_open(
        self,
        user_id: int,
        server_name: str,
        *,
        default_open: bool,
    ) -> dict[str, Any]:
        return self.patch_custom_server(user_id, server_name, default_open=default_open)

    def list_instances_for_api(self, user_id: int) -> list[dict[str, Any]]:
        """Built-in rows + expanded custom servers (hide parent custom-mcp row)."""
        out: list[dict[str, Any]] = []
        custom_row = self._repo.get_by_user_kind(user_id, CUSTOM_MCP_KIND)
        # Visibility lives in ``resource_acl`` (schema v21 dropped the legacy
        # ``connectors.shared`` column), so the display flag is read from there.
        public_ids = self._repo.public_instance_ids()
        for inst in self._repo.list_visible(user_id):
            if is_custom_mcp_kind(inst.kind):
                continue
            config = ConnectorRepo.parse_config_json(inst)
            out.append(
                {
                    "instance_id": inst.instance_id,
                    "kind": inst.kind,
                    "display_name": inst.display_name,
                    "description": config.get("description"),
                    "status": inst.status,
                    "mcp_server_name": inst.mcp_server_name,
                    "has_credentials": inst.has_credentials,
                    "default_open": read_default_open(config),
                    "shared": inst.instance_id in public_ids,
                    "owner_user_id": inst.user_id,
                    "created_at": inst.created_at,
                    "updated_at": inst.updated_at,
                }
            )
        if custom_row is not None and custom_row.has_credentials:
            out.extend(
                expand_custom_instances(
                    parent=custom_row,
                    servers=extract_servers(self.decrypt(custom_row.instance_id)),
                )
            )
        for parent in self._repo.list_by_kind(CUSTOM_MCP_KIND):
            if parent.user_id == user_id or not parent.has_credentials:
                continue
            out.extend(
                expand_custom_instances(
                    parent=parent,
                    servers=extract_servers(self.decrypt(parent.instance_id)),
                    shared_view=True,
                )
            )
        for item in out:
            item.setdefault("owner_user_id", user_id)
            item.setdefault("shared", False)
        return out

    def list_active_mcp_server_names(self, user_id: int) -> list[str]:
        names: list[str] = []
        for inst in self._repo.list_visible(user_id):
            if is_custom_mcp_kind(inst.kind):
                continue
            if inst.status != "active" or not inst.has_credentials:
                continue
            names.append(inst.mcp_server_name)
        for name, spec in self.get_custom_servers(user_id).items():
            if isinstance(spec, dict) and server_enabled(spec):
                names.append(name)
        for parent in self._repo.list_by_kind(CUSTOM_MCP_KIND):
            if parent.user_id == user_id or not parent.has_credentials:
                continue
            for name, spec in extract_servers(self.decrypt(parent.instance_id)).items():
                if isinstance(spec, dict) and spec.get("shared") is True and server_enabled(spec):
                    names.append(shared_mcp_server_name(parent.instance_id, name))
        return sorted(names)

    def list_default_open_mcp_server_names(self, user_id: int) -> list[str]:
        """Active connectors marked default_open (dashboard + all IM channels)."""
        names: list[str] = []
        for inst in self._repo.list_by_user(user_id):
            if is_custom_mcp_kind(inst.kind):
                continue
            if inst.status != "active" or not inst.has_credentials:
                continue
            if read_default_open(ConnectorRepo.parse_config_json(inst)):
                names.append(inst.mcp_server_name)
        for name, spec in self.get_custom_servers(user_id).items():
            if not isinstance(spec, dict) or not server_enabled(spec):
                continue
            if spec.get("default_open") is True:
                names.append(name)
        return names

    def merge_turn_mcp_servers(
        self,
        user_id: int,
        explicit: list[str] | None,
        *,
        apply_defaults: bool | None = None,
        extra_defaults: list[str] | None = None,
        agent_owner_id: int | None = None,
    ) -> list[str] | None:
        """Resolve turn MCP servers vs the user's default_open set.

        Dashboard passes an explicit list (``apply_defaults=False``) so users can
        opt out for a turn. IM uses ``explicit is None`` + defaults. Cron follows
        ``extra_defaults`` (expert composer picks) are unioned with default_open
        when defaults apply. Shared owner short names are translated to this
        user's collision-safe catalog names before selection.
        """
        aliases = (
            self.shared_custom_harness_aliases(agent_owner_id)
            if agent_owner_id is not None and agent_owner_id != user_id
            else {}
        )
        owner_custom = (
            self.get_custom_servers(agent_owner_id)
            if agent_owner_id is not None and agent_owner_id != user_id
            else {}
        )
        defaults = list(self.list_default_open_mcp_server_names(user_id))
        if extra_defaults:
            allowed = set(self.list_active_mcp_server_names(user_id))
            short_to_long = {short: long for long, short in aliases.items()}
            for name in extra_defaults:
                text = str(name).strip()
                if not text:
                    continue
                viewer_name = short_to_long.get(text)
                if viewer_name is not None and viewer_name in allowed:
                    if viewer_name not in defaults:
                        defaults.append(viewer_name)
                elif text not in owner_custom and text in allowed and text not in defaults:
                    defaults.append(text)
        return merge_mcp_servers_with_defaults(
            explicit,
            defaults,
            apply_defaults=apply_defaults,
        )

    def validate_mcp_servers_for_user(self, user_id: int, names: list[str]) -> list[str]:
        allowed = set(self.list_active_mcp_server_names(user_id))
        unknown = sorted(set(names) - allowed)
        if unknown:
            raise ValueError(f"mcp_servers not available for user: {unknown}")
        return list(names)

    def custom_harness_configs(self, user_id: int) -> dict[str, Any]:
        configs = enabled_harness_configs(self.get_custom_servers(user_id))
        for parent in self._repo.list_by_kind(CUSTOM_MCP_KIND):
            if parent.user_id == user_id or not parent.has_credentials:
                continue
            servers = extract_servers(self.decrypt(parent.instance_id))
            for name, spec in servers.items():
                if (
                    not isinstance(spec, dict)
                    or spec.get("shared") is not True
                    or not server_enabled(spec)
                ):
                    continue
                built = enabled_harness_configs({name: spec}).get(name)
                if built is not None:
                    configs[shared_mcp_server_name(parent.instance_id, name)] = built
        return configs

    async def mcp_configs_for_user(self, user_id: int) -> dict[str, Any]:
        configs: dict[str, Any] = {}
        for inst in self._repo.list_visible(user_id):
            if inst.status != "active":
                continue
            if is_custom_mcp_kind(inst.kind):
                continue
            entry = get_catalog_entry(inst.kind)
            if entry is None:
                continue
            creds = await self.ensure_fresh_credentials(inst.instance_id, inst.kind)
            if not creds:
                continue
            configs[inst.mcp_server_name] = build_http_mcp_spec(
                entry=entry,
                instance_id=inst.instance_id,
                creds=creds,
                config=self._config,
            )
        await self.ensure_fresh_custom_servers(user_id)
        for parent in self._repo.list_by_kind(CUSTOM_MCP_KIND):
            if parent.user_id == user_id or not parent.has_credentials:
                continue
            servers = extract_servers(self.decrypt(parent.instance_id))
            if any(
                isinstance(spec, dict) and spec.get("shared") is True for spec in servers.values()
            ):
                await self.ensure_fresh_custom_servers(parent.user_id)
        configs.update(self.custom_harness_configs(user_id))
        return configs

    def verify_internal_token(self, instance_id: str, token: str) -> dict[str, Any] | None:
        creds = self.decrypt(instance_id)
        expected = str(creds.get("internal_token") or "")
        if not expected or expected != token:
            return None
        return creds

    # --- Feishu CLI device-code user auth (domain orchestration) ---

    @staticmethod
    def feishu_cli_config_dir(cli_config_key: str) -> Path:
        return PathLayout.from_env().ensure_connector_cli_instance_dir("feishu-cli", cli_config_key)

    async def start_feishu_user_auth(
        self,
        *,
        app_id: str,
        app_secret: str,
        cli_config_key: str | None = None,
        domains: list[str] | None = None,
    ) -> dict[str, Any]:
        """Begin OAuth device-code login (anonymous / pre-save credentials)."""
        app_id = str(app_id or "").strip()
        app_secret = str(app_secret or "").strip()
        if not app_id or not app_secret:
            raise ValueError("app_id and app_secret are required")
        cli_key = str(cli_config_key or "").strip() or new_ulid()
        config_dir = self.feishu_cli_config_dir(cli_key)
        started = await asyncio.to_thread(
            start_user_device_login,
            config_dir=config_dir,
            app_id=app_id,
            app_secret=app_secret,
            domains=domains,
        )
        return {**started, "cli_config_key": cli_key}

    async def complete_feishu_user_auth(
        self,
        *,
        app_id: str,
        app_secret: str,
        device_code: str,
        cli_config_key: str,
    ) -> dict[str, Any]:
        """Finish device-code login for pre-save credentials (no instance write)."""
        app_id = str(app_id or "").strip()
        app_secret = str(app_secret or "").strip()
        device_code = str(device_code or "").strip()
        cli_key = str(cli_config_key or "").strip()
        if not app_id or not app_secret or not device_code or not cli_key:
            raise ValueError("app_id, app_secret, device_code and cli_config_key are required")
        config_dir = self.feishu_cli_config_dir(cli_key)
        result = await asyncio.to_thread(
            complete_user_device_login,
            config_dir=config_dir,
            app_id=app_id,
            app_secret=app_secret,
            device_code=device_code,
        )
        return {**result, "cli_config_key": cli_key, "default_as": "user"}

    def _require_feishu_cli_instance(
        self, instance_id: str, user_id: int
    ) -> tuple[ConnectorRow, dict[str, Any]]:
        inst = self._repo.get(instance_id)
        if inst is None:
            raise OctopError(ErrorCode.CONNECTOR_NOT_FOUND, f"instance {instance_id!r} not found")
        if inst.user_id != user_id:
            raise OctopError(ErrorCode.FORBIDDEN, "not your connector instance")
        if inst.kind != "feishu-cli":
            raise OctopError(
                ErrorCode.CONNECTOR_KIND_UNSUPPORTED,
                "only feishu-cli supports user device login",
            )
        if not inst.has_credentials:
            raise OctopError(ErrorCode.CONNECTOR_INVALID_CREDENTIALS, "missing credentials")
        return inst, dict(self.decrypt(instance_id))

    async def start_feishu_user_auth_for_instance(
        self, instance_id: str, user_id: int
    ) -> dict[str, Any]:
        """Start device login using App Secret stored on the instance."""
        _inst, creds = self._require_feishu_cli_instance(instance_id, user_id)
        app_id = str(creds.get("app_id") or "").strip()
        app_secret = str(creds.get("app_secret") or "").strip()
        if not app_id or not app_secret:
            raise OctopError(
                ErrorCode.CONNECTOR_INVALID_CREDENTIALS,
                "stored Feishu app credentials incomplete",
                details={"reason": "stored Feishu app credentials incomplete"},
            )
        if not str(creds.get("cli_config_key") or "").strip():
            creds["cli_config_key"] = instance_id
        cli_key = resolve_cli_config_key(creds)
        config_dir = self.feishu_cli_config_dir(cli_key)
        started = await asyncio.to_thread(
            start_user_device_login,
            config_dir=config_dir,
            app_id=app_id,
            app_secret=app_secret,
            domains=None,
        )
        return {**started, "cli_config_key": cli_key}

    async def complete_feishu_user_auth_for_instance(
        self,
        instance_id: str,
        user_id: int,
        *,
        device_code: str,
        cli_config_key: str | None = None,
    ) -> dict[str, Any]:
        """Finish device login and persist ``default_as=user`` on the instance."""
        _inst, creds = self._require_feishu_cli_instance(instance_id, user_id)
        old_creds = dict(creds)
        app_id = str(creds.get("app_id") or "").strip()
        app_secret = str(creds.get("app_secret") or "").strip()
        device_code = str(device_code or "").strip()
        if not str(creds.get("cli_config_key") or "").strip():
            creds["cli_config_key"] = instance_id
        override = str(cli_config_key or "").strip()
        if override:
            creds["cli_config_key"] = override
        cli_key = resolve_cli_config_key(creds)
        if not app_id or not app_secret or not device_code:
            raise OctopError(
                ErrorCode.CONNECTOR_INVALID_CREDENTIALS,
                "incomplete Feishu user auth payload",
                details={"reason": "incomplete Feishu user auth payload"},
            )
        config_dir = self.feishu_cli_config_dir(cli_key)
        result = await asyncio.to_thread(
            complete_user_device_login,
            config_dir=config_dir,
            app_id=app_id,
            app_secret=app_secret,
            device_code=device_code,
        )
        creds["default_as"] = "user"
        creds["cli_config_key"] = cli_key
        self.encrypt_and_store(instance_id=instance_id, payload=creds)
        await self.cleanup_cli_dirs_after_change(
            "feishu-cli", old_creds, creds, exclude_instance_id=instance_id
        )
        return {**result, "cli_config_key": cli_key, "default_as": "user"}
