"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Modules whose tests boot OctopServer, real harness, browser tooling, or bwrap.
# Tagged ``slow`` so ``make test-fast`` can skip them during local iteration.
_SLOW_TEST_MODULES = frozenset(
    {
        "tests/unit/browser/test_browser_setup.py",
        "tests/unit/infra/utils/test_bwrap.py",
        "tests/unit/agents/test_skills_hub_raw.py",
        "tests/unit/agents/test_skillhub_market.py",
        "tests/unit/gateway/test_attachment_hints.py",
        "tests/unit/agents/test_agent_manager.py",
        "tests/unit/agents/test_agent_registry.py",
        "tests/unit/api/test_jwt_auth_middleware.py",
        "tests/unit/api/test_openapi_meta.py",
        "tests/unit/api/test_exception_handlers.py",
    },
)


def _module_path(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        module = _module_path(item.nodeid)
        if module in _SLOW_TEST_MODULES or module.startswith("tests/integration/"):
            item.add_marker(pytest.mark.slow)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return _REPO_ROOT


# Creating an agent now starts a multi-hour asyncio.sleep (proactive care
# defaults to on). pytest-asyncio waits for leftover tasks before fixture
# teardown, so any test that boots OctopServer without going through
# ``octop_client`` would hang the suite. Scheduler unit tests opt out.
_PROACTIVE_SCHEDULER_TESTS = "tests/unit/proactive/test_scheduler.py"


@pytest.fixture(autouse=True)
def _suspend_proactive_care_loops(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if _module_path(request.node.nodeid) == _PROACTIVE_SCHEDULER_TESTS:
        return
    from octop.infra.proactive.scheduler import ProactiveCareScheduler

    monkeypatch.setattr(ProactiveCareScheduler, "ensure_scheduled", lambda self, _id: None)

    async def _start_all(self: Any) -> None:
        return None

    monkeypatch.setattr(ProactiveCareScheduler, "start_all", _start_all)
    monkeypatch.setattr(ProactiveCareScheduler, "_schedule", lambda self, _id: None)


@pytest.fixture(autouse=True)
def _isolated_user_home(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Per-test HOME outside the test's ``tmp_path``.

    Keeping HOME off ``tmp_path`` avoids polluting directory listings and
    colliding with tests that create ``tmp_path / \"home\"`` themselves.
    Required for xdist so workers never share ``~/.octop`` / CLI state.
    """
    home = tmp_path_factory.mktemp("user-home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for key in (
        "OCTOP_CAPTCHA_PROVIDER",
        "OCTOP_CAPTCHA_SITE_KEY",
        "OCTOP_CAPTCHA_SECRET",
        "OCTOP_CAPTCHA_V3_MIN_SCORE",
        "OCTOP_LICENSE_SERVER_URL",
        "OCTOP_LICENSE_TRUST_FILE",
        "OCTOP_LICENSE_CA_FILE",
        "OCTOP_LICENSE_CONTACT",
    ):
        monkeypatch.delenv(key, raising=False)
    return home


@pytest.fixture
def tmp_octop_home(_isolated_user_home: Path) -> Iterator[Path]:
    """Redirect ``~/.octop`` under the autouse isolated HOME."""
    octop = _isolated_user_home / ".octop"
    octop.mkdir(exist_ok=True)
    yield octop


@pytest.fixture
def signed_active_license(_isolated_user_home: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Use real signed credentials for business tests, never a fake allow gate."""
    import json

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    import octop.infra.licensing.service as license_module
    import octop.infra.server as server_module
    from tests.support.licensing import provision_license, public_key_pem

    signer = Ed25519PrivateKey.generate()
    trust_file = _isolated_user_home / "test-license-public.json"
    trust_file.write_text(json.dumps({"test": public_key_pem(signer)}), encoding="utf-8")
    monkeypatch.setenv("OCTOP_LICENSE_TRUST_FILE", str(trust_file))
    service = provision_license(_isolated_user_home / "test-license-state", signer=signer)
    monkeypatch.setattr(license_module, "_CURRENT", service)

    def licensed_service(home: Path, **kwargs: Any) -> Any:
        return provision_license(home, signer=signer, server_url=kwargs.get("server_url"))

    monkeypatch.setattr(server_module, "LicenseService", licensed_service)
    return service


@pytest.fixture(autouse=True)
def _signed_business_authorization(request: pytest.FixtureRequest) -> None:
    if "/licensing/" in request.node.nodeid or request.node.get_closest_marker("license_isolation"):
        return
    request.getfixturevalue("signed_active_license")
