"""ACL gate coverage: no current_admin; known permission keys only."""

from __future__ import annotations

import ast
import pathlib

from octop.infra.users.permissions import ALL_PERMISSION_KEYS, channel_permission_key

API_ROOT = pathlib.Path("src/octop/api")

GATED_FILES = [
    "routers/users.py",
    "routers/admin.py",
    "routers/backup.py",
    "routers/security.py",
    "routers/providers.py",
    "routers/voice.py",
    "routers/storage_backends.py",
    "routers/envs.py",
    "routers/settings.py",
    "routers/observability.py",
    "routers/tls.py",
    "routers/auth_oidc.py",
    "routers/update.py",
    "routers/search.py",
    "routers/ollama_models.py",
    "routers/onnx_models.py",
    "routers/connectors.py",
    "routers/knowledge_bases.py",
    "routers/data_sources.py",
    "routers/browser/uninstall.py",
    "routers/browser/env.py",
    "routers/desktop/install.py",
    "routers/desktop/uninstall.py",
    "routers/desktop/status.py",
    "routers/desktop/settings.py",
    "routers/plugins.py",
    "routers/agents.py",
    "routers/channels.py",
    "routers/skill_packages.py",
    "routers/mbti.py",
    "routers/experts.py",
    "routers/features.py",
    "routers/terminal.py",
    "routers/acp.py",
    "routers/filesystem.py",
    "routers/org_units.py",
    "routers/sharing.py",
]


def test_no_current_admin_symbol_remains() -> None:
    hits: list[str] = []
    for path in API_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "current_admin":
                hits.append(str(path))
    assert not hits, f"current_admin still referenced in: {hits}"


def test_gated_files_call_require_permission_with_known_keys() -> None:
    for rel in GATED_FILES:
        src = (API_ROOT / rel).read_text(encoding="utf-8")
        assert "current_admin" not in src
        assert (
            "require_permission(" in src or "user_has_permission(" in src or "require_admin(" in src
        )
        tree = ast.parse(src, filename=rel)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "require_permission"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                assert node.args[0].value in ALL_PERMISSION_KEYS, (
                    f"{rel}: unknown key {node.args[0].value!r}"
                )


def test_as_user_still_requires_admin() -> None:
    """Cross-user impersonation must not become a module permission."""
    agent = (API_ROOT / "common/agent.py").read_text(encoding="utf-8")
    usage = (API_ROOT / "routers/usage.py").read_text(encoding="utf-8")
    assert "as_user requires admin" in agent or "is_admin" in agent
    assert "as_user" in usage


#: WebSocket routes authenticate using ``?token=<JWT>`` because the browser
#: cannot set an ``Authorization`` header on the upgrade. Their bodies must check
#: the same permission as the corresponding HTTP surface.
WS_GATED_FILES: dict[str, str] = {
    "routers/terminal.py": "terminal",
    "routers/browser/stream.py": "browser",
    "routers/desktop/stream.py": "desktop",
    "routers/mobile/stream.py": "mobile",
    "routers/mobile/shell_ws.py": "mobile",
}

_HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def _route_functions(
    rel: str, methods: tuple[str, ...] = _HTTP_METHODS
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function in ``rel`` decorated as a route of one of ``methods``."""
    tree = ast.parse((API_ROOT / rel).read_text(encoding="utf-8"), filename=rel)
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr in methods
            for dec in node.decorator_list
        )
    ]


def _gate_defaults(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Source of every parameter default that could be a ``Depends`` gate."""
    args = node.args
    defaults = [*args.defaults, *(d for d in args.kw_defaults if d is not None)]
    return [ast.unparse(default) for default in defaults]


def _keys_checked_in_body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Module keys the route checks itself, via ``user_has_permission(user, key)``."""
    keys: set[str] = set()
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Name)
            and sub.func.id == "user_has_permission"
            and len(sub.args) >= 2
            and isinstance(sub.args[1], ast.Constant)
            and isinstance(sub.args[1].value, str)
        ):
            keys.add(sub.args[1].value)
    return keys


def test_every_websocket_on_a_gated_surface_checks_its_key() -> None:
    for rel, key in WS_GATED_FILES.items():
        assert key in ALL_PERMISSION_KEYS, f"{rel}: {key!r} is not a catalog key"
        nodes = _route_functions(rel, methods=("websocket",))
        assert nodes, f"{rel}: no websocket route found — the file moved, update this list"
        for node in nodes:
            checked = _keys_checked_in_body(node)
            assert key in checked, (
                f"{rel}: websocket {node.name!r} does not check {key!r} "
                f"(checks: {sorted(checked)}) — a socket must run the same module gate "
                "as the HTTP routes of its surface (design §2.4)"
            )


_CHANNEL_ROUTER = "routers/channels.py"

#: Channel-surface routes that carry no type gate of their own, and why. A route
#: is either gated on the kind it touches or explained here.
CHANNEL_TYPE_FILTERED_ROUTES: dict[str, str] = {
    # The list answers for *every* kind the actor may use at once, so it filters
    # the rows by ``channel_<kind>`` instead of refusing outright — a 403 would
    # hide the channels the actor is entitled to because of one it is not
    # (design §2.3: 不能…查看该类型通道).
    "list_channels": "filters its rows by kind instead of refusing (§2.3)",
}


def _path_of(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    for dec in node.decorator_list:
        if (
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr in _HTTP_METHODS
            and dec.args
            and isinstance(dec.args[0], ast.Constant)
            and isinstance(dec.args[0].value, str)
        ):
            return dec.args[0].value
    return None


def _kind_in_path(path: str) -> str | None:
    """The channel kind ``path`` names, if any — read off the catalog itself.

    A segment counts only when it is a kind the gateway supports, so the
    ``probe`` of ``/channels/probe`` is not mistaken for one and neither is a
    ``{channel_id}`` placeholder.
    """
    for segment in path.strip("/").split("/"):
        if channel_permission_key(segment) in ALL_PERMISSION_KEYS:
            return segment
    return None


def test_every_channel_route_reads_its_kind_from_somewhere() -> None:
    """A channel route is gated on the type key of the kind the request touches.

    Design §2.3 gates channel types, not the channel module: ``channels`` alone
    must never be enough to create, edit, delete, test, bind or view a channel.
    Which kind a request touches is decided by the path (``.../dingtalk/...``),
    by its body, or by the stored row — and the three shapes are the three this
    test accepts. A new route that names none of them fails here, and so does one
    that names the wrong kind's key: the key is read out of the path rather than
    from a table kept next to it.
    """
    seen: set[str] = set()
    for node in _route_functions(_CHANNEL_ROUTER):
        path = _path_of(node)
        assert path is not None, f"{_CHANNEL_ROUTER}: {node.name!r} has no path"
        seen.add(node.name)
        defaults = _gate_defaults(node)
        kind = _kind_in_path(path)
        if kind is not None:
            # ``ast.unparse`` renders string constants with ``repr`` (single
            # quotes), so the expectation is spelled the same way.
            expected = f"require_permission(channel_permission_key('{kind}'))"
            assert any(expected in default for default in defaults), (
                f"{_CHANNEL_ROUTER}: {node.name!r} names kind {kind!r} in its path but "
                f"does not ask for {channel_permission_key(kind)!r} "
                f"(gates: {defaults})"
            )
            continue
        resolved = [
            default
            for default in defaults
            if "require_channel_kind_from_body()" in default
            or "require_channel_kind_from_row()" in default
        ]
        if resolved:
            continue
        assert node.name in CHANNEL_TYPE_FILTERED_ROUTES, (
            f"{_CHANNEL_ROUTER}: {node.name!r} ({path}) is gated on the module key alone — "
            "give it the type key of the kind it touches, or list it in "
            "CHANNEL_TYPE_FILTERED_ROUTES with the reason it has none"
        )
        assert "_channels_of_authorized_types(" in ast.unparse(node), (
            f"{_CHANNEL_ROUTER}: {node.name!r} is exempted as a filtering route but does "
            "not filter anything"
        )
    missing = set(CHANNEL_TYPE_FILTERED_ROUTES) - seen
    assert not missing, f"{_CHANNEL_ROUTER}: exempted routes that no longer exist: {missing}"
