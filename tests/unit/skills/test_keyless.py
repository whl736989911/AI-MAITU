from __future__ import annotations

import pytest
from packaging.requirements import Requirement

from octop.infra.skills.keyless import inspect_keyless_package, package_fingerprint
from octop.infra.skills.skill_packages import resolve_skill_package


def _package(*files: tuple[str, bytes]):
    return resolve_skill_package(
        slug="local-helper",
        files=[("SKILL.md", b"# Local helper\n"), *files],
        source="test",
    )


def test_local_document_skill_with_explicit_no_key_statement_is_eligible() -> None:
    package = _package(
        (
            "guides/usage.md",
            b"This local workflow does not require an API key. It works without a token.\n",
        )
    )

    result = inspect_keyless_package(package)

    assert result.credential_free is True
    assert result.issues == ()
    assert result.binaries == ()
    assert result.python_modules == ()
    assert result.node_modules == ()
    assert result.has_runnable_code is False


def test_frontmatter_description_with_no_key_requirement_is_eligible() -> None:
    package = _package(
        (
            "guides/usage.md",
            b"---\ndescription: No API key is required.\n---\nLocal-only usage.\n",
        )
    )

    result = inspect_keyless_package(package)

    assert result.credential_free is True
    assert result.issues == ()


def test_documented_required_credential_makes_package_ineligible() -> None:
    package = _package(
        (
            "README.md",
            b"Before running, set OPENAI_API_KEY in your environment.\n",
        )
    )

    result = inspect_keyless_package(package)

    assert result.credential_free is False
    assert any(issue.startswith("credential.") for issue in result.issues)


def test_named_environment_credential_is_detected_in_prose() -> None:
    package = _package(("README.md", b"Set GITHUB_TOKEN before running this skill.\n"))

    result = inspect_keyless_package(package)

    assert result.credential_free is False
    assert any(issue.startswith("credential.declared_requirement:") for issue in result.issues)


def test_static_python_analysis_finds_hidden_key_and_real_dependencies() -> None:
    package = _package(
        (
            "run.py",
            b"import os\nimport requests\n\n"
            b"def run():\n    return requests.get(os.getenv('OPENAI_API_KEY'))\n",
        ),
        ("requirements.txt", b"requests>=2\n"),
    )

    result = inspect_keyless_package(package)

    assert result.credential_free is False
    assert result.has_runnable_code is True
    assert "requests" in result.python_modules
    assert any(issue.startswith("credential.") for issue in result.issues)


def test_dynamic_credential_lookup_fails_closed_but_stdlib_is_not_dependency() -> None:
    package = _package(
        (
            "run.py",
            b"import json\nimport os\n\ndef run(name):\n    return json.loads(os.environ[name])\n",
        )
    )

    result = inspect_keyless_package(package)

    assert result.credential_free is False
    assert result.python_modules == ()
    assert any("ambiguous" in issue for issue in result.issues)


def test_known_third_party_import_without_manifest_is_reported_for_runtime_probe() -> None:
    package = _package(("run.py", b"import requests\n"))

    result = inspect_keyless_package(package)

    assert result.credential_free is True
    assert result.issues == ()
    assert result.python_modules == ("requests",)


def test_dynamic_module_import_and_runtime_code_are_unknown() -> None:
    package = _package(
        ("run.py", b"import importlib\nmodule = importlib.import_module(name)\nexec(source)\n")
    )

    result = inspect_keyless_package(package)

    assert result.credential_free is False
    assert any(issue.startswith("dependency.ambiguous_python:") for issue in result.issues)
    assert any(issue.startswith("analysis.dynamic_execution:") for issue in result.issues)


def test_dynamic_setup_dependency_declaration_fails_closed() -> None:
    package = _package(
        (
            "setup.py",
            b"from setuptools import setup\nsetup(install_requires=load_requirements())\n",
        )
    )

    result = inspect_keyless_package(package)

    assert result.credential_free is False
    assert any(issue.startswith("dependency.ambiguous_python:") for issue in result.issues)


def test_oversized_script_is_rejected_before_static_parsing() -> None:
    package = _package(("run.py", b"#" + b"x" * (1024 * 1024)))

    result = inspect_keyless_package(package)

    assert result.credential_free is False
    assert result.has_runnable_code is True
    assert "analysis.file_too_large:run.py" in result.issues


def test_external_node_dependency_and_command_are_reported() -> None:
    package = _package(
        (
            "package.json",
            b'{"dependencies":{"cheerio":"^1.0.0"},"scripts":{"start":"node index.js"}}',
        ),
        ("index.js", b"import cheerio from 'cheerio';\n"),
    )

    result = inspect_keyless_package(package)

    assert result.credential_free is True
    assert result.has_runnable_code is True
    assert "cheerio" in result.node_modules
    assert "node" in result.binaries


def test_static_vite_credential_access_makes_package_ineligible() -> None:
    package = _package(("src/index.js", b"const key = import.meta.env.OPENAI_API_KEY;\n"))

    result = inspect_keyless_package(package)

    assert result.credential_free is False
    assert any(issue.startswith("credential.code_access:") for issue in result.issues)


def test_undecodable_opaque_payload_fails_closed() -> None:
    package = _package(("payload.bin", b"\x00\xff\x00"))

    result = inspect_keyless_package(package)

    assert result.credential_free is False
    assert any(issue.startswith("analysis.") for issue in result.issues)


def test_fingerprint_is_stable_and_tracks_paths_and_content() -> None:
    original = _package(("docs/guide.md", b"First version\n"))
    same_content_different_input_order = resolve_skill_package(
        slug="local-helper",
        files=[("docs/guide.md", b"First version\n"), ("SKILL.md", b"# Local helper\n")],
        source="elsewhere",
    )
    changed_content = _package(("docs/guide.md", b"Second version\n"))
    changed_path = _package(("docs/other.md", b"First version\n"))

    fingerprint = package_fingerprint(original)

    assert fingerprint == package_fingerprint(same_content_different_input_order)
    assert len(fingerprint) == 64
    assert fingerprint != package_fingerprint(changed_content)
    assert fingerprint != package_fingerprint(changed_path)


def _declared_skill(requirements: str):
    return resolve_skill_package(
        slug="local-helper",
        files=[
            (
                "SKILL.md",
                (
                    "---\nname: local-helper\ndescription: Local helper.\n"
                    "metadata:\n  openclaw:\n    requires:\n"
                    f"{requirements}---\n\nRead local documents.\n"
                ).encode(),
            )
        ],
        source="test",
    )


def test_declared_command_is_reported_even_when_only_named_in_metadata() -> None:
    result = inspect_keyless_package(_declared_skill("      bins: [rsvg-convert]\n"))
    assert result.credential_free is True
    assert result.binaries == ("rsvg-convert",)


def test_required_environment_cannot_be_assumed_to_be_ready_from_its_name() -> None:
    result = inspect_keyless_package(_declared_skill("      env: [FAL_KEY]\n"))
    assert result.credential_free is False
    assert any(issue.startswith("credential.") for issue in result.issues)


def test_platform_specific_stdlib_imports_require_runtime_probe() -> None:
    result = inspect_keyless_package(_package(("run.py", b"import msvcrt\nimport fcntl\n")))
    assert {"msvcrt", "fcntl"} <= set(result.python_modules)


def test_bundled_python_sibling_is_not_an_external_runtime_dependency() -> None:
    result = inspect_keyless_package(
        _package(
            ("scripts/main.py", b"from helper import format_document\n"),
            ("scripts/helper.py", b"def format_document(text):\n    return text.strip()\n"),
        )
    )
    assert result.credential_free is True
    assert "helper" not in result.python_modules


@pytest.mark.parametrize(
    "description",
    ["必须先配置密钥才能使用。", "请先登录网站，再处理文件。", "需要购买订阅后运行。"],
)
def test_chinese_credential_or_login_requirements_are_not_keyless(description: str) -> None:
    result = inspect_keyless_package(_package(("README.md", description.encode())))
    assert result.credential_free is False


def test_chinese_statement_that_no_key_is_needed_remains_eligible() -> None:
    result = inspect_keyless_package(_package(("README.md", "无需密钥，可离线整理文件。".encode())))
    assert result.credential_free is True


def test_no_api_key_statement_does_not_hide_a_separate_login_requirement() -> None:
    result = inspect_keyless_package(
        _package(("README.md", b"No API key is required but you must log in first.\n"))
    )
    assert result.credential_free is False


def test_distribution_requirements_are_not_confused_with_import_names() -> None:
    result = inspect_keyless_package(
        _package(
            ("run.py", b"from docx import Document\nfrom PIL import Image\n"),
            ("requirements.txt", b"python-docx>=1.2\nPillow>=10; python_version >= '3.10'\n"),
        )
    )
    assert result.credential_free is True
    assert set(result.python_modules) == {"docx", "PIL"}
    requirements = {
        Requirement(value).name: Requirement(value) for value in result.python_requirements
    }
    assert "1.2" in requirements["python-docx"].specifier
    assert "1.1" not in requirements["python-docx"].specifier
    assert requirements["Pillow"].marker is not None


def test_recursive_metadata_is_rejected_instead_of_recursing_indefinitely() -> None:
    package = resolve_skill_package(
        slug="local-helper",
        files=[
            (
                "SKILL.md",
                b"---\nname: local-helper\ndescription: Local helper.\nmetadata: &loop\n  openclaw: *loop\n---\nLocal documents.\n",
            )
        ],
        source="test",
    )
    result = inspect_keyless_package(package)
    assert result.credential_free is False
    assert any(issue.startswith("analysis.recursive_metadata:") for issue in result.issues)


def test_python_module_command_reports_the_invoked_module_requirement() -> None:
    result = inspect_keyless_package(
        _package(
            (
                "run.py",
                b"import subprocess\nsubprocess.run(['python', '-m', 'document_converter'])\n",
            )
        )
    )
    assert result.credential_free is True
    assert "python" in result.binaries
    assert "document_converter" in result.python_modules
