"""tests/unit/i18n/test_brand_keys.py

``scripts/apply_brand.py`` brands a bundle's *values* per locale. A key is a
shared identifier, so it must come out identical in every locale — and free of
the spaces an English brand name drags in.

The regression this pins: the script substituted over the whole file, so a key
holding the upstream name was branded too, and one key became two —
``askMAITU Smart ManufacturingHint`` in en.json against ``ask麦途智造Hint`` in
zh.json.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "apply_brand.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("apply_brand", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


apply_brand = _load_script()

# The brand text itself is config-owned (brand.config.json); what is contractual
# here is where each variant may land — a value takes the locale's name, a key
# takes the one language-independent slug.
CONFIG = json.loads((REPO / "brand.config.json").read_text(encoding="utf-8"))
ZH_NAME = CONFIG["name"]["zh"]
EN_NAME = CONFIG["name"]["en"]
ZH_FULL = CONFIG["full_name"]["zh"]
EN_FULL = CONFIG["full_name"]["en"]
SLUG = apply_brand.KEY_SLUG

# The bundles as they sit in the repo before a run: keys carry the upstream name
# (identity), the key token, and — from the bug — value tokens that leaked into
# keys. Values carry both the token and the upstream name in prose.
SOURCE = {
    "common": {
        "askOctopHint": "%BRAND% · %BRAND_FULL%",
        "label%BRAND_KEY%": "Octop",
        "section%BRAND%": "3. Install",
        "welcome": "欢迎使用 Octop，这是 %BRAND% 的提示。",
        "keep_octopbot": "OctopBot 平台",
    },
    "storage": {"dockerEnv": {"section%BRAND_KEY%Hint": "%BRAND_FULL%"}},
}


def keys_of(bundle: dict) -> set[str]:
    """Every key in *bundle*, dotted the way the bundles address them."""
    found: set[str] = set()

    def walk(node: object, prefix: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                found.add(prefix + key)
                walk(value, prefix + key + ".")
        elif isinstance(node, list):
            for value in node:
                walk(value, prefix)

    walk(bundle, "")
    return found


def _checkout(tmp_path: Path) -> Path:
    """A miniature checkout: the script, the real config, empty bundles."""
    tree = tmp_path / "checkout"
    (tree / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, tree / "scripts" / "apply_brand.py")
    shutil.copy2(REPO / "brand.config.json", tree / "brand.config.json")
    for relative in apply_brand.TEXT_FILES:
        path = tree / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(SOURCE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (tree / "dashboard" / "index.html").write_text("<html></html>\n", encoding="utf-8")
    manifest = tree / "dashboard" / "public" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("{}\n", encoding="utf-8")
    return tree


def _run(tree: Path) -> str:
    result = subprocess.run(
        [sys.executable, str(tree / "scripts" / "apply_brand.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=True,
    )
    return result.stdout


def _bundle(tree: Path, relative: str) -> dict:
    return json.loads((tree / relative).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "directory", ["dashboard/src/locales", "src/octop/i18n"], ids=["dashboard", "backend"]
)
def test_a_run_keeps_keys_identical_across_locales(tmp_path: Path, directory: str) -> None:
    tree = _checkout(tmp_path)
    _run(tree)
    zh = _bundle(tree, f"{directory}/zh.json")
    en = _bundle(tree, f"{directory}/en.json")
    zh_keys, en_keys = keys_of(zh), keys_of(en)

    assert zh_keys == en_keys
    # The upstream name is part of a key's identity, not branding to chase.
    assert "common.askOctopHint" in en_keys
    # Tokens in a key resolve to the language-independent slug, with no space.
    assert {f"common.label{SLUG}", f"common.section{SLUG}"} <= en_keys
    assert f"storage.dockerEnv.section{SLUG}Hint" in en_keys
    assert not [key for key in en_keys if " " in key]


def test_values_follow_their_own_locale(tmp_path: Path) -> None:
    tree = _checkout(tmp_path)
    _run(tree)
    zh = _bundle(tree, "dashboard/src/locales/zh.json")
    en = _bundle(tree, "dashboard/src/locales/en.json")

    assert zh["common"][f"label{SLUG}"] == ZH_NAME
    assert en["common"][f"label{SLUG}"] == EN_NAME
    assert ZH_NAME in zh["common"]["welcome"]
    assert EN_NAME in en["common"]["welcome"]
    assert zh["storage"]["dockerEnv"][f"section{SLUG}Hint"] == ZH_FULL
    assert en["storage"]["dockerEnv"][f"section{SLUG}Hint"] == EN_FULL
    # OctopBot names an external platform: neither locale may brand it.
    assert zh["common"]["keep_octopbot"] == en["common"]["keep_octopbot"] == "OctopBot 平台"


def test_rerunning_the_script_changes_nothing(tmp_path: Path) -> None:
    tree = _checkout(tmp_path)
    _run(tree)
    branded = {path: path.read_bytes() for path in sorted(tree.rglob("*")) if path.is_file()}

    assert "(nothing to do — already applied)" in _run(tree)

    assert {path: path.read_bytes() for path in branded} == branded


def test_a_key_branded_by_an_earlier_run_is_repaired_to_the_slug() -> None:
    """The reported damage: one key, two locale names, one of them with spaces."""
    brand_values = apply_brand.brand_strings({})
    broken = {
        "en": {"storage": {"dockerEnv": {f"section{EN_NAME}": "3."}}},
        "zh": {"storage": {"dockerEnv": {f"section{ZH_NAME}": "3."}}},
    }
    repaired = {}
    for locale, bundle in broken.items():
        text, *_ = apply_brand.brand_json(
            json.dumps(bundle, ensure_ascii=False), locale, {}, brand_values
        )
        repaired[locale] = keys_of(json.loads(text))

    assert repaired["en"] == repaired["zh"]
    assert f"storage.dockerEnv.section{SLUG}" in repaired["en"]
    assert not [key for key in repaired["en"] if " " in key]


def test_rebranding_moves_keys_to_the_new_slug(tmp_path: Path) -> None:
    """Keys follow the slug: the marker remembers the one the last run wrote."""
    tree = _checkout(tmp_path)
    _run(tree)

    config = tree / "brand.config.json"
    settings = json.loads(config.read_text(encoding="utf-8"))
    for field in ("name", "full_name", "short_name"):
        settings[field] = {"zh": "新品牌", "en": "NewBrand"}
    config.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _run(tree)

    zh = _bundle(tree, "dashboard/src/locales/zh.json")
    en = _bundle(tree, "dashboard/src/locales/en.json")
    assert keys_of(zh) == keys_of(en)
    assert {"common.labelNewBrand", "common.sectionNewBrand"} <= keys_of(en)
    assert "common.askOctopHint" in keys_of(en)
    assert not [key for key in keys_of(en) if " " in key]
    assert en["common"]["labelNewBrand"] == "NewBrand"
    assert zh["common"]["labelNewBrand"] == "新品牌"
