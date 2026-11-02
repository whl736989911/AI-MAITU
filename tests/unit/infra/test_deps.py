from __future__ import annotations

from collections.abc import Iterator
from types import ModuleType

import pytest

from octop.infra import deps


@pytest.fixture(autouse=True)
def clear_import_cache() -> Iterator[None]:
    deps._IMPORT_CACHE.clear()
    yield
    deps._IMPORT_CACHE.clear()


def test_base_is_required_and_extras_follow_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deps.importlib, "import_module", lambda _name: ModuleType("dummy"))
    monkeypatch.setattr(deps.shutil, "which", lambda _name: "/available")

    base_only = deps.check(())
    assert [result.required for result in base_only] == [
        not component.extra for component in deps.COMPONENTS
    ]
    assert sum(result.required for result in base_only) == 1

    for extra in deps.EXTRAS:
        results = deps.check((extra,))
        assert [result.required for result in results] == [
            not component.extra or component.extra == extra for component in deps.COMPONENTS
        ]


def test_missing_import_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = deps.COMPONENTS[0].imports[0]

    def import_module(name: str) -> ModuleType:
        if name == module_name:
            raise ModuleNotFoundError(f"No module named {name!r}")
        return ModuleType(name)

    monkeypatch.setattr(deps.importlib, "import_module", import_module)
    results = deps.check(())

    missing = next(item for item in results[0].missing if item.name == module_name)
    assert missing.kind == "import"
    assert missing.name == module_name
    assert missing.detail


def test_system_library_import_error_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = deps.COMPONENTS[0].imports[0]
    detail = "shared library unavailable"

    def import_module(name: str) -> ModuleType:
        if name == module_name:
            raise OSError(detail)
        return ModuleType(name)

    monkeypatch.setattr(deps.importlib, "import_module", import_module)
    results = deps.check(())

    missing = next(item for item in results[0].missing if item.name == module_name)
    assert missing.kind == "import"
    assert missing.name == module_name
    assert detail in missing.detail


def test_present_import_has_no_missing_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deps.importlib, "import_module", lambda name: ModuleType(name))
    monkeypatch.setattr(deps.shutil, "which", lambda _name: "/available")

    result = deps.check(())[0]

    assert result.ok
    assert result.missing == ()


def test_missing_binary_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    component = next(component for component in deps.COMPONENTS if component.binaries)
    monkeypatch.setattr(deps.importlib, "import_module", lambda name: ModuleType(name))
    monkeypatch.setattr(deps.shutil, "which", lambda _name: None)

    result = next(
        result for result in deps.check((component.extra,)) if result.component == component
    )

    assert result.required
    assert [(item.kind, item.name) for item in result.missing] == [
        ("binary", binary) for binary in component.binaries
    ]


def test_available_binary_is_not_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    component = next(component for component in deps.COMPONENTS if component.binaries)
    monkeypatch.setattr(deps.importlib, "import_module", lambda name: ModuleType(name))
    monkeypatch.setattr(deps.shutil, "which", lambda _name: "/available")

    result = next(
        result for result in deps.check((component.extra,)) if result.component == component
    )

    assert all(item.kind != "binary" for item in result.missing)


def test_missing_apt_packages_use_required_failures_in_component_order() -> None:
    first = deps.Component(key="first", extra="", imports=(), apt=("shared", "first-only"))
    optional = deps.Component(
        key="optional", extra="optional", imports=(), apt=("optional-only", "shared")
    )
    last = deps.Component(key="last", extra="", imports=(), apt=("last-only", "shared"))
    passed = deps.Component(key="passed", extra="", imports=(), apt=("not-needed",))
    failure = (deps.Missing("import", "missing", "not installed"),)
    results = [
        deps.CheckResult(first, True, failure),
        deps.CheckResult(optional, False, failure),
        deps.CheckResult(last, True, failure),
        deps.CheckResult(passed, True, ()),
    ]

    assert deps.missing_apt_packages(results) == ["shared", "first-only", "last-only"]


def test_health_and_failed_components_ignore_optional_failures() -> None:
    required_failure = deps.Component(key="required", extra="", imports=())
    optional_failure = deps.Component(key="optional", extra="optional", imports=())
    missing = (deps.Missing("import", "dependency", "not installed"),)
    only_optional_failed = [
        deps.CheckResult(required_failure, True, ()),
        deps.CheckResult(optional_failure, False, missing),
    ]
    required_failed = [
        deps.CheckResult(required_failure, True, missing),
        deps.CheckResult(optional_failure, False, ()),
    ]

    assert deps.healthy(only_optional_failed)
    assert deps.failed_components(only_optional_failed) == []
    assert not deps.healthy(required_failed)
    assert deps.failed_components(required_failed) == [required_failed[0]]


def test_component_extras_and_extra_inventory_stay_in_sync() -> None:
    component_extras = {component.extra for component in deps.COMPONENTS if component.extra}

    assert all(
        not component.extra or component.extra in deps.EXTRAS for component in deps.COMPONENTS
    )
    assert all(extra in component_extras for extra in deps.EXTRAS)
