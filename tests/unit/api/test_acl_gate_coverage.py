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
}


def _route_functions(rel: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function in ``rel`` decorated as an HTTP route."""
    tree = ast.parse((API_ROOT / rel).read_text(encoding="utf-8"), filename=rel)
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr in ("get", "post", "put", "patch", "delete")
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
