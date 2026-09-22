"""ACL gate coverage: no current_admin; known permission keys only."""

from __future__ import annotations

import ast
import pathlib

from octop.infra.users.permissions import ALL_PERMISSION_KEYS

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


#: Files whose *every* route names a gate of its own — the module surfaces design
#: §4.4 lists, each one gated in full. ``test_every_route_on_a_gated_surface_is_gated``
#: exists because the file-level test above cannot see the gap this list closes:
#: a router that gates one route and leaves the next one open still contains the
#: string ``require_permission(``, so before this test the *open* route was the
#: quiet one (``mbti.py``, ``experts.py``, ``features.py`` and most of
#: ``connectors.py`` were exactly that until design §4.4 was implemented).
ROUTE_GATED_FILES = [
    "routers/mbti.py",
    "routers/experts.py",
    "routers/features.py",
    "routers/plugins.py",
    "routers/connectors.py",
    "routers/skill_packages.py",
    # The workbench / remote surfaces and the user-management module (design §4.4
    # second half: 终端和浏览器分别接入 terminal、browser; 远程桌面接入 desktop;
    # 用户管理接口接入 users 和组织范围判断). ``browser/env.py``,
    # ``browser/harness.py`` and ``browser/record_replay.py`` were signed-in-only
    # until this list was extended — the module key is what a direct API call was
    # skipping.
    "routers/terminal.py",
    "routers/browser/env.py",
    "routers/browser/harness.py",
    "routers/browser/record_replay.py",
    "routers/browser/uninstall.py",
    "routers/desktop/install.py",
    "routers/desktop/settings.py",
    "routers/desktop/status.py",
    "routers/desktop/uninstall.py",
    "routers/mobile/install.py",
    "routers/mobile/status.py",
    "routers/acp.py",
    "routers/users.py",
    "routers/org_units.py",
    "routers/invites.py",
    "routers/sharing.py",
]

#: Routes on those surfaces that deliberately carry no gate, and why. An empty
#: reason is not accepted by the test: a route is either gated or explained.
UNGATED_ROUTES: dict[str, dict[str, str]] = {
    "routers/connectors.py": {
        # The provider redirects the browser here without an Authorization
        # header (the path is JWT-exempt in ``api/deps.py``), so there is no user
        # to resolve a key against. It is reachable only with a ``state`` that
        # ``/connectors/oauth/start`` minted — and that route is gated, so the
        # entry point is the gate.
        "oauth_callback": "JWT-exempt provider redirect; gated at oauth/start by its state",
    },
    "routers/users.py": {
        # The permission catalog the editor's picker is drawn from: key, category,
        # label and ``can_grant`` — definitions, never account data, with
        # ``can_grant`` resolved for whoever asks. The page that renders it is
        # behind the ``users`` key, and reading what keys exist is not a
        # capability of its own.
        "list_permission_catalog": "static permission definitions; the picker's own data",
    },
    "routers/invites.py": {
        # The invitee has no account yet, which is the whole point of an invite:
        # both paths are JWT-exempt (``_JWT_EXEMPT_EXACT``) and are gated by the
        # invite's own single-use code, checked in ``infra/users/invites.py``.
        "validate_invite": "pre-account invite flow, JWT-exempt; the invite code is the gate",
        "redeem_invite": "pre-account invite flow, JWT-exempt; the invite code is the gate",
    },
}

#: Surfaces whose WebSocket routes carry the gate *in their body*, because a
#: browser cannot set an ``Authorization`` header on a WebSocket upgrade: those
#: routes take ``?token=<JWT>`` and resolve the user themselves, so no
#: ``Depends`` sits in the signature for ``_names_a_gate`` to see. The value is
#: the key the route must check. ``GET /api/browser-stream/ws`` checked nothing
#: at all until design §4.4 was implemented, which is the bypass this test exists
#: to keep closed: the socket is a stream of the same capability the HTTP routes
#: gate, and 未授权功能 must not be reachable through it either (§2.4).
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


def _names_a_gate(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when the route's own parameters ask for a gate."""
    args = node.args
    defaults = [*args.defaults, *(d for d in args.kw_defaults if d is not None)]
    return any(
        "require_permission(" in ast.unparse(default) or "require_admin(" in ast.unparse(default)
        for default in defaults
    )


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


def test_every_route_on_a_gated_surface_is_gated() -> None:
    exempt = UNGATED_ROUTES
    for rel in ROUTE_GATED_FILES:
        for reason in exempt.get(rel, {}).values():
            assert reason, f"{rel}: an ungated route must say why"
        for node in _route_functions(rel):
            if node.name in exempt.get(rel, {}):
                continue
            assert _names_a_gate(node), (
                f"{rel}: route {node.name!r} names no require_permission/require_admin "
                "gate — gate it, or list it in UNGATED_ROUTES with the reason it has none"
            )


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
