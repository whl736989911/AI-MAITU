"""Deterministic, credential-free eligibility analysis for skill packages."""

from __future__ import annotations

import ast
import configparser
import hashlib
import json
import re
import shlex
import struct
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement

from octop.infra.skills.skill_packages import (
    ResolvedSkillPackage,
    SkillPackageError,
    normalize_skill_files,
)
from octop.infra.utils.frontmatter import parse_frontmatter

MAX_ANALYSIS_FILE_BYTES = 1024 * 1024
_PLATFORM_SPECIFIC_STDLIB = frozenset(
    {"msvcrt", "winreg", "fcntl", "pwd", "grp", "termios", "tty", "resource", "nis"}
)
_CHINESE_CREDENTIAL_TERM = re.compile(r"密钥|秘钥|令牌|凭据|密码|登录|登陆|付费|订阅|授权码")
_CHINESE_REQUIRED_ACTION = re.compile(
    r"需要|必须|需先|配置|设置|填写|提供|申请|获取|购买|登录|登陆|付费|订阅"
)
_CHINESE_NO_CREDENTIALS = re.compile(
    r"(?:无需|无须|不需要|不必|不用).{0,24}(?:密钥|秘钥|令牌|凭据|密码|登录|登陆|付费|订阅|API[\s_-]*Key)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SkillRequirements:
    credential_free: bool
    issues: tuple[str, ...]
    binaries: tuple[str, ...]
    python_modules: tuple[str, ...]
    node_modules: tuple[str, ...]
    has_runnable_code: bool
    python_requirements: tuple[str, ...] = ()


_DOCUMENT_SUFFIXES = {
    ".adoc",
    ".cfg",
    ".conf",
    ".env",
    ".html",
    ".htm",
    ".ini",
    ".json",
    ".md",
    ".markdown",
    ".mdx",
    ".toml",
    ".txt",
    ".rst",
    ".yaml",
    ".yml",
}
_PYTHON_SUFFIXES = {".py", ".pyw"}
_NODE_SUFFIXES = {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx"}
_SHELL_SUFFIXES = {".sh", ".bash", ".zsh"}
_UNSUPPORTED_RUNNABLE_SUFFIXES = {
    ".bat",
    ".c",
    ".cpp",
    ".cs",
    ".cmd",
    ".class",
    ".dll",
    ".exe",
    ".go",
    ".ipynb",
    ".jar",
    ".kt",
    ".kts",
    ".lua",
    ".m",
    ".php",
    ".pl",
    ".ps1",
    ".pyc",
    ".r",
    ".rb",
    ".rs",
    ".scala",
    ".so",
    ".swift",
    ".wasm",
    ".whl",
    ".zip",
}
_PASSIVE_IMAGE_SIGNATURES = {
    ".bmp": (b"BM",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".ico": (b"\x00\x00\x01\x00",),
    ".flac": (b"fLaC",),
    ".ogg": (b"OggS",),
    ".otf": (b"OTTO",),
    ".ttf": (b"\x00\x01\x00\x00",),
    ".webm": (b"\x1a\x45\xdf\xa3",),
    ".woff": (b"wOFF",),
    ".woff2": (b"wOF2",),
}
_CREDENTIAL_TERM = re.compile(
    r"\b(?:api[\s_-]*key|access[\s_-]*key(?:[\s_-]*id)?|private[\s_-]*key|"
    r"access[\s_-]*token|refresh[\s_-]*token|personal[\s_-]*access[\s_-]*token|"
    r"authorization|bearer|"
    r"auth(?:entication|orization)?[\s_-]*(?:token|key|credentials?)|"
    r"credentials?|tokens?|oauth|login|logged[\s-]+in|log[\s-]+in|sign[\s-]+in|"
    r"subscription|paid[\s-]+(?:plan|account|tier)|secret|password)\b",
    re.IGNORECASE,
)
_CREDENTIAL_IDENTIFIER = re.compile(
    r"(?:^|_)(?:API_KEY|ACCESS_KEY(?:_ID)?|ACCESS_TOKEN|REFRESH_TOKEN|AUTH_TOKEN|"
    r"CLIENT_SECRET|PRIVATE_KEY|SECRET|PASSWORD|CREDENTIALS?|TOKEN|(?:GITHUB|GH)_PAT)(?:$|_)",
    re.IGNORECASE,
)
_REQUIRED_ACTION = re.compile(
    r"\b(?:requires?|required|needs?|needed|must|have\s+to|has\s+to|"
    r"set|provide|supply|enter|configure|obtain|create|purchase|pay\s+for|"
    r"subscribe|log\s+in|login|sign\s+in|authenticate|authorize|connect)\b",
    re.IGNORECASE,
)
_CREDENTIAL_USE = re.compile(
    r"\b(?:use|uses|using|access|accesses|retrieve|read|send|store|"
    r"authenticate|authorize|connect)\b",
    re.IGNORECASE,
)
_SAFE_NEGATION = re.compile(
    r"\b(?:does\s+not|doesn't|do\s+not|don't|never)\s+"
    r"(?:need|require|use|ask\s+for)\b.{0,100}\b(?:api[\s_-]*key|"
    r"access[\s_-]*key(?:[\s_-]*id)?|private[\s_-]*key|"
    r"access[\s_-]*token|refresh[\s_-]*token|tokens?|credentials?|oauth|"
    r"login|log[\s-]+in|sign[\s-]+in|secret|password)\b"
    r"|\bno\s+(?:api[\s_-]*key|access[\s_-]*key(?:[\s_-]*id)?|private[\s_-]*key|"
    r"access[\s_-]*token|refresh[\s_-]*token|tokens?|"
    r"credentials?|oauth|secret|password)\b.{0,50}\b(?:required|needed|"
    r"necessary|must\s+be\s+provided)\b"
    r"|\b(?:api[\s_-]*key|access[\s_-]*key(?:[\s_-]*id)?|private[\s_-]*key|"
    r"access[\s_-]*token|refresh[\s_-]*token|tokens?|"
    r"credentials?|oauth|secret|password)\b.{0,50}\b(?:not\s+required|"
    r"not\s+needed|optional)\b"
    r"|\bwithout\s+(?:an?\s+)?(?:api[\s_-]*key|access[\s_-]*key(?:[\s_-]*id)?|"
    r"private[\s_-]*key|access[\s_-]*token|tokens?|"
    r"refresh[\s_-]*token|credentials?|oauth|secret|password)\b",
    re.IGNORECASE,
)
_SAFE_SECURITY_ADVICE = re.compile(
    r"\b(?:do\s+not|don't|never)\s+(?:put|include|commit|share|expose|"
    r"store|provide|set)\b.{0,80}\b(?:api[\s_-]*key|token|credentials?|"
    r"secret|password)\b",
    re.IGNORECASE,
)
_ENV_NAME = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")
_ENV_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", re.M)
_NODE_BUILTINS = {
    "assert",
    "async_hooks",
    "buffer",
    "child_process",
    "cluster",
    "console",
    "crypto",
    "dns",
    "events",
    "fs",
    "http",
    "https",
    "module",
    "net",
    "os",
    "path",
    "perf_hooks",
    "process",
    "readline",
    "stream",
    "timers",
    "tls",
    "url",
    "util",
    "worker_threads",
    "zlib",
}
_AUTH_SDK_ROOTS = {
    "anthropic",
    "authlib",
    "boto3",
    "botocore",
    "github",
    "google.auth",
    "google.cloud",
    "huggingface_hub",
    "keyring",
    "msal",
    "openai",
    "requests_oauthlib",
    "slack_sdk",
    "stripe",
    "twilio",
}
_CREDENTIAL_PATH = re.compile(
    r"(?:^|[/\\.])(?:\.env(?:\.[A-Za-z0-9_-]+)?|credentials?\.json|"
    r"credentials|token\.json|id_rsa|id_ed25519)(?:$|[/\\.])",
    re.IGNORECASE,
)
_SHELL_BUILTINS = {
    "!",
    ".",
    ":",
    "alias",
    "bg",
    "break",
    "cd",
    "command",
    "continue",
    "echo",
    "eval",
    "exec",
    "exit",
    "export",
    "false",
    "fg",
    "getopts",
    "hash",
    "jobs",
    "local",
    "printf",
    "pwd",
    "read",
    "return",
    "set",
    "shift",
    "source",
    "test",
    "times",
    "trap",
    "true",
    "type",
    "ulimit",
    "umask",
    "unalias",
    "unset",
    "wait",
}
_SHELL_CONTROL = {"do", "elif", "else", "fi", "if", "then", "while", "until"}
_KNOWN_DOC_BINARIES = {
    "deno",
    "docker",
    "ffmpeg",
    "git",
    "jq",
    "node",
    "npm",
    "npx",
    "ollama",
    "pip",
    "python",
    "python3",
    "uv",
    "yarn",
}


def package_fingerprint(package: ResolvedSkillPackage) -> str:
    """Hash canonical paths and contents, independent of input file order."""
    files = normalize_skill_files(list(package.files), require_manifest=False)
    digest = hashlib.sha256(b"octop-skill-package-v1\0")
    for path, content in sorted(files, key=lambda item: item[0]):
        path_bytes = path.encode("utf-8")
        digest.update(struct.pack(">Q", len(path_bytes)))
        digest.update(path_bytes)
        digest.update(struct.pack(">Q", len(content)))
        digest.update(content)
    return digest.hexdigest()


@dataclass
class _Analysis:
    issues: set[str] = field(default_factory=set)
    binaries: set[str] = field(default_factory=set)
    python_modules: set[str] = field(default_factory=set)
    node_modules: set[str] = field(default_factory=set)
    has_runnable_code: bool = False
    python_files: set[str] = field(default_factory=set)
    python_requirements: set[str] = field(default_factory=set)

    def issue(self, category: str, path: str) -> None:
        self.issues.add(f"{category}:{path}")


def inspect_keyless_package(package: ResolvedSkillPackage) -> SkillRequirements:
    """Inspect package contents without importing or executing any downloaded code."""
    analysis = _Analysis()
    try:
        files = normalize_skill_files(list(package.files))
    except SkillPackageError:
        return SkillRequirements(False, ("analysis.invalid_package",), (), (), (), False)
    analysis.python_files = {
        path for path, _ in files if PurePosixPath(path).suffix.lower() in _PYTHON_SUFFIXES
    }

    text_files: dict[str, str] = {}
    for path, content in files:
        suffix = PurePosixPath(path).suffix.lower()
        if _is_known_passive_image(suffix, content):
            continue
        if len(content) > MAX_ANALYSIS_FILE_BYTES:
            analysis.issue("analysis.file_too_large", path)
            if (
                suffix
                in _PYTHON_SUFFIXES
                | _NODE_SUFFIXES
                | _SHELL_SUFFIXES
                | _UNSUPPORTED_RUNNABLE_SUFFIXES
            ):
                analysis.has_runnable_code = True
            continue
        text = _decode_text(content)
        if text is None:
            analysis.issue("analysis.opaque_payload", path)
            if suffix in _UNSUPPORTED_RUNNABLE_SUFFIXES:
                analysis.issue("analysis.opaque_executable", path)
            continue

        text_files[path] = text
        try:
            metadata, _body = (
                parse_frontmatter(text) if suffix in _DOCUMENT_SUFFIXES else ({}, text)
            )
        except RecursionError:
            analysis.issue("analysis.unparseable_metadata", path)
            continue
        if suffix in _DOCUMENT_SUFFIXES or _is_text_manifest(path):
            _scan_document(text, path, analysis)
            _scan_metadata(metadata, path, analysis)

        if suffix in _PYTHON_SUFFIXES:
            analysis.has_runnable_code = True
            _scan_python(text, path, analysis)
        elif suffix in _NODE_SUFFIXES:
            analysis.has_runnable_code = True
            _scan_node(text, path, analysis)
        elif suffix in _SHELL_SUFFIXES:
            analysis.has_runnable_code = True
            _scan_shell(text, path, analysis)
        elif suffix in _UNSUPPORTED_RUNNABLE_SUFFIXES:
            analysis.has_runnable_code = True
            analysis.issue("analysis.unsupported_executable", path)
        elif text.startswith("#!"):
            analysis.has_runnable_code = True
            _scan_shebang_script(text, path, analysis)

    _scan_manifests(text_files, analysis)
    _scan_documented_binaries(text_files, analysis)

    issues = tuple(sorted(analysis.issues))
    return SkillRequirements(
        credential_free=not issues,
        issues=issues,
        binaries=tuple(sorted(analysis.binaries)),
        python_modules=tuple(sorted(analysis.python_modules)),
        node_modules=tuple(sorted(analysis.node_modules)),
        has_runnable_code=analysis.has_runnable_code,
        python_requirements=tuple(sorted(analysis.python_requirements)),
    )


def _decode_text(content: bytes) -> str | None:
    if b"\x00" in content:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _is_known_passive_image(suffix: str, content: bytes) -> bool:
    if suffix == ".webp":
        return content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    if suffix in {".mp4", ".mov", ".m4v"}:
        return len(content) >= 12 and content[4:8] == b"ftyp"
    if suffix == ".mp3":
        return content.startswith(b"ID3") or (
            len(content) > 1 and content[0] == 0xFF and content[1] & 0xE0 == 0xE0
        )
    if suffix == ".wav":
        return content.startswith(b"RIFF") and content[8:12] == b"WAVE"
    return any(
        content.startswith(signature) for signature in _PASSIVE_IMAGE_SIGNATURES.get(suffix, ())
    )


def _is_text_manifest(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return (
        name
        in {
            "readme",
            "readme.txt",
            "pyproject.toml",
            "setup.cfg",
            "package.json",
            ".env.example",
            ".env.template",
        }
        or name.startswith(".env.")
        or bool(re.fullmatch(r"requirements(?:[-_.][a-z0-9_.-]+)?\.txt", name))
    )


def _scan_document(text: str, path: str, analysis: _Analysis) -> None:
    for line_number, line in enumerate(text.splitlines(), start=1):
        for sentence in re.split(
            r"(?<=[.!?;,。！？；，])\s*|\s{2,}|\b(?:but|however)\b|但是|不过|然而|但",
            line,
            flags=re.IGNORECASE,
        ):
            if not (
                _CREDENTIAL_TERM.search(sentence)
                or _contains_credential_identifier(sentence)
                or _CHINESE_CREDENTIAL_TERM.search(sentence)
            ):
                continue
            if (
                _SAFE_NEGATION.search(sentence)
                or _SAFE_SECURITY_ADVICE.search(sentence)
                or _CHINESE_NO_CREDENTIALS.search(sentence)
            ):
                continue
            location = f"{path}#L{line_number}"
            if _REQUIRED_ACTION.search(sentence) or _CHINESE_REQUIRED_ACTION.search(sentence):
                analysis.issue("credential.declared_requirement", location)
            elif _CREDENTIAL_USE.search(sentence):
                analysis.issue("credential.ambiguous_document_requirement", location)

        for name in _ENV_ASSIGNMENT.findall(line):
            if _is_credential_name(name):
                analysis.issue("credential.declared_requirement", f"{path}#L{line_number}")
        if _CREDENTIAL_IDENTIFIER.search(line) and re.search(r"[:=]", line):
            analysis.issue("credential.declared_requirement", f"{path}#L{line_number}")


def _scan_metadata(metadata: dict[str, Any], path: str, analysis: _Analysis) -> None:
    active: set[int] = set()
    visited: set[tuple[int, str, bool]] = set()

    def visit(value: Any, key: str = "", required: bool = False, depth: int = 0) -> None:
        if depth > 32 or len(visited) >= 4096:
            analysis.issue("analysis.metadata_too_complex", path)
            return
        if isinstance(value, (dict, list, tuple)):
            identity = id(value)
            if identity in active:
                analysis.issue("analysis.recursive_metadata", path)
                return
            marker = (identity, key, required)
            if marker in visited:
                return
            visited.add(marker)
            active.add(identity)
            try:
                if required and key in {"env", "environment", "env_vars"} and value:
                    analysis.issue("credential.declared_requirement", path)
                elif required and key == "bins":
                    if not isinstance(value, (list, tuple)) or not all(
                        isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", item)
                        for item in value
                    ):
                        analysis.issue("dependency.ambiguous_binary", path)
                    else:
                        analysis.binaries.update(value)
                    return
                elif required and key in {"anybins", "config", "configuration"} and value:
                    analysis.issue("dependency.unresolved_configuration", path)
                if isinstance(value, dict):
                    for nested_key, nested_value in value.items():
                        visit(
                            nested_value,
                            str(nested_key).lower(),
                            required or str(nested_key).lower() == "requires",
                            depth + 1,
                        )
                else:
                    for item in value:
                        visit(item, key, required, depth + 1)
            finally:
                active.remove(identity)
        elif isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"", "false", "none", "null", "no", "[]", "{}"}:
                return
            if required and key in {"env", "environment", "env_vars"}:
                analysis.issue("credential.declared_requirement", path)
            elif required and key == "bins":
                analysis.issue("dependency.ambiguous_binary", path)
            elif required and key in {"anybins", "config", "configuration"}:
                analysis.issue("dependency.unresolved_configuration", path)
            elif _is_credential_name(key) or (
                _is_credential_name(value)
                and not _SAFE_NEGATION.search(value)
                and not _CHINESE_NO_CREDENTIALS.search(value)
                and not _SAFE_SECURITY_ADVICE.search(value)
            ):
                analysis.issue("credential.declared_requirement", path)
        elif value and required and key in {"env", "environment", "env_vars"}:
            analysis.issue("credential.declared_requirement", path)
        elif value and required and key == "bins":
            analysis.issue("dependency.ambiguous_binary", path)
        elif value and required and key in {"anybins", "config", "configuration"}:
            analysis.issue("dependency.unresolved_configuration", path)
        elif isinstance(value, bool) and value and _is_credential_name(key):
            analysis.issue("credential.declared_requirement", path)

    visit(metadata)


def _is_credential_name(name: str) -> bool:
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).replace("-", "_")
    return bool(
        _CREDENTIAL_IDENTIFIER.search(normalized)
        or _CREDENTIAL_TERM.search(name)
        or _CHINESE_CREDENTIAL_TERM.search(name)
    )


def _is_stdlib(module: str) -> bool:
    root = module.split(".", 1)[0]
    return root in sys.stdlib_module_names and root not in _PLATFORM_SPECIFIC_STDLIB


def _record_python_import(module: str, path: str, analysis: _Analysis) -> None:
    if any(module == root or module.startswith(f"{root}.") for root in _AUTH_SDK_ROOTS):
        analysis.issue("credential.ambiguous_sdk_auth", path)
    if _is_stdlib(module):
        return
    relative = module.replace(".", "/")
    for base in (PurePosixPath(path).parent, PurePosixPath(".")):
        if (base / f"{relative}.py").as_posix() in analysis.python_files or (
            base / relative / "__init__.py"
        ).as_posix() in analysis.python_files:
            return
    analysis.python_modules.add(module)


def _scan_python(text: str, path: str, analysis: _Analysis) -> None:
    try:
        tree = ast.parse(text, filename=path)
    except (SyntaxError, ValueError, RecursionError):
        analysis.issue("analysis.unparseable_code", path)
        return

    parents: dict[ast.AST, ast.AST] = {}
    environment_names = {"os.environ"}
    getenv_names = {"os.getenv", "getenv"}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
        if isinstance(parent, ast.Import):
            for imported in parent.names:
                if imported.name == "os":
                    alias = imported.asname or "os"
                    environment_names.add(f"{alias}.environ")
                    getenv_names.add(f"{alias}.getenv")
        elif isinstance(parent, ast.ImportFrom) and parent.module == "os":
            for imported in parent.names:
                alias = imported.asname or imported.name
                if imported.name == "environ":
                    environment_names.add(alias)
                elif imported.name == "getenv":
                    getenv_names.add(alias)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                _record_python_import(imported.name, path, analysis)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            _record_python_import(node.module, path, analysis)

        if (
            isinstance(node, ast.Name)
            and _is_credential_name(node.id)
            or isinstance(node, ast.Attribute)
            and _is_credential_name(node.attr)
        ):
            analysis.issue("credential.code_access", path)

        if isinstance(node, ast.Call):
            function_name = _python_dotted_name(node.func)
            is_env_get = function_name in getenv_names or any(
                function_name == f"{environment_name}.get" for environment_name in environment_names
            )
            is_env_lookup = any(
                function_name
                in {
                    f"{environment_name}.get",
                    f"{environment_name}.pop",
                    f"{environment_name}.__getitem__",
                    f"{environment_name}.setdefault",
                }
                for environment_name in environment_names
            )
            if is_env_lookup or is_env_get:
                _scan_python_env_lookup(node, path, analysis)
            if function_name in {"eval", "exec", "compile"} or function_name.endswith(
                (".eval", ".exec")
            ):
                analysis.issue("analysis.dynamic_execution", path)
            if function_name in {"__import__", "importlib.import_module"}:
                module = _constant_string(node.args[0]) if node.args else None
                if module is None:
                    analysis.issue("dependency.ambiguous_python", path)
                else:
                    _record_python_import(module, path, analysis)
            if (
                PurePosixPath(path).name.lower() == "setup.py"
                and function_name.split(".")[-1] == "setup"
            ):
                _scan_setup_call(node, path, analysis)
            if function_name == "getattr" and (
                len(node.args) < 2
                or _constant_string(node.args[1]) is None
                or _constant_string(node.args[1]) in {"environ", "getenv"}
            ):
                analysis.issue("credential.ambiguous_dynamic_requirement", path)
            if function_name.split(".")[-1] in {"input", "getpass", "prompt"} and any(
                isinstance(argument, ast.Constant)
                and isinstance(argument.value, str)
                and _CREDENTIAL_TERM.search(argument.value)
                for argument in node.args
            ):
                analysis.issue("credential.code_access", path)
            if function_name.endswith((".system", ".popen")):
                if (
                    node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    _scan_shell(node.args[0].value, path, analysis)
                else:
                    analysis.issue("analysis.dynamic_execution", path)
            if node.args and function_name.endswith(
                (".run", ".Popen", ".call", ".check_call", ".check_output")
            ):
                command = _literal_command(node.args[0])
                if command:
                    _record_binary(command, analysis)
                    if command in {"python", "python3", "python.exe"}:
                        _scan_python_m_option(node.args[0], analysis, path)
                else:
                    analysis.issue("dependency.ambiguous_binary", path)
            if function_name.endswith(".which") and node.args:
                command = _constant_string(node.args[0])
                if command:
                    _record_binary(command, analysis)
            if function_name.split(".")[-1].lower() in {
                "login",
                "authenticate",
                "authorize",
                "oauth",
            }:
                analysis.issue("credential.ambiguous_code_requirement", path)

        if isinstance(node, ast.Subscript) and _python_dotted_name(node.value) in environment_names:
            key = _constant_string(node.slice)
            if key is None:
                analysis.issue("credential.ambiguous_dynamic_requirement", path)
            elif _is_credential_name(key):
                analysis.issue("credential.code_access", path)

        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and (
                node.value == ".env"
                or node.value.startswith((".env/", ".env."))
                or _CREDENTIAL_PATH.search(node.value)
                or re.search(
                    r"\b(?:authorization|proxy-authorization)\b", node.value, re.IGNORECASE
                )
            )
        ):
            analysis.issue("credential.code_access", path)

        if isinstance(node, ast.Attribute) and node.attr == "environ":
            environment_parent = parents.get(node)
            if not (
                isinstance(environment_parent, ast.Subscript) and environment_parent.value is node
            ) and not (
                isinstance(environment_parent, ast.Attribute) and environment_parent.attr == "get"
            ):
                analysis.issue("credential.ambiguous_dynamic_requirement", path)
        elif isinstance(node, ast.Name) and node.id in environment_names:
            environment_parent = parents.get(node)
            if not isinstance(environment_parent, (ast.Subscript, ast.Attribute)):
                analysis.issue("credential.ambiguous_dynamic_requirement", path)


def _python_dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_python_dotted_name(node.value)}.{node.attr}"
    return ""


def _constant_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_command(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.split(maxsplit=1)[0] if node.value.split() else None
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        return _constant_string(node.elts[0])
    return None


def _scan_python_env_lookup(node: ast.Call, path: str, analysis: _Analysis) -> None:
    if not node.args:
        analysis.issue("credential.ambiguous_dynamic_requirement", path)
        return
    name = _constant_string(node.args[0])
    if name is None:
        analysis.issue("credential.ambiguous_dynamic_requirement", path)
    elif _is_credential_name(name):
        analysis.issue("credential.code_access", path)


def _scan_setup_call(node: ast.Call, path: str, analysis: _Analysis) -> None:
    if any(keyword.arg is None for keyword in node.keywords):
        analysis.issue("dependency.ambiguous_python", path)
    for keyword in node.keywords:
        if keyword.arg not in {"install_requires", "extras_require"}:
            continue
        value = keyword.value
        if keyword.arg == "install_requires":
            if isinstance(value, (ast.List, ast.Tuple)):
                for requirement in value.elts:
                    _add_python_requirement(_constant_string(requirement), path, analysis)
            else:
                analysis.issue("dependency.ambiguous_python", path)
        elif isinstance(value, ast.Dict):
            for requirements in value.values:
                if isinstance(requirements, (ast.List, ast.Tuple)):
                    for requirement in requirements.elts:
                        _add_python_requirement(_constant_string(requirement), path, analysis)
                else:
                    analysis.issue("dependency.ambiguous_python", path)
        else:
            analysis.issue("dependency.ambiguous_python", path)


def _scan_python_m_option(node: ast.AST, analysis: _Analysis, path: str) -> None:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return
    values = [_constant_string(item) for item in node.elts]
    for index, value in enumerate(values[:-1]):
        module = values[index + 1]
        if value == "-m" and module:
            _record_python_import(module, path, analysis)
            break
    else:
        if any(value == "-m" for value in values):
            analysis.issue("dependency.ambiguous_python", path)


def _scan_node(text: str, path: str, analysis: _Analysis) -> None:
    if re.search(r"\bprocess\s*\.\s*env\s*\[\s*[^'\"\]]", text):
        analysis.issue("credential.ambiguous_dynamic_requirement", path)
    if re.search(r"\bprocess\s*\[\s*['\"]env['\"]\s*\]", text):
        analysis.issue("credential.ambiguous_dynamic_requirement", path)
    if re.search(r"\b(?:import\s*\.\s*meta\s*\.\s*env|Deno\s*\.\s*env)\s*\[\s*[^'\"\]]", text):
        analysis.issue("credential.ambiguous_dynamic_requirement", path)
    for match in re.finditer(
        r"\b(?:import\s*\.\s*meta\s*\.\s*env|Deno\s*\.\s*env\s*\.\s*get)\s*"
        r"(?:\.\s*([A-Za-z_$][\w$]*)|\[\s*['\"]([^'\"]+)['\"]\s*\]|"
        r"\(\s*['\"]([^'\"]+)['\"]\s*\))",
        text,
    ):
        name = next((group for group in match.groups() if group is not None), "")
        if _is_credential_name(name):
            analysis.issue("credential.code_access", path)
    if re.search(r"['\"]\.env(?:\.[A-Za-z0-9_-]+)?['\"]", text):
        analysis.issue("credential.code_access", path)
    for match in re.finditer(
        r"\bprocess\s*\.\s*env\s*(?:\.\s*([A-Za-z_$][\w$]*)|\[\s*['\"]([^'\"]+)['\"]\s*\])",
        text,
    ):
        name = match.group(1) or match.group(2) or ""
        if _is_credential_name(name):
            analysis.issue("credential.code_access", path)
    for match in re.finditer(
        r"\b(?:import\s+(?:[^;]*?\s+from\s+)?|export\s+[^;]*?\s+from\s+|"
        r"require\s*\(\s*|import\s*\(\s*)['\"]([^'\"]+)['\"]",
        text,
    ):
        module = match.group(1)
        if module.startswith((".", "/", "node:")):
            continue
        package_name = module.split("/", 1)[0]
        if package_name.startswith("@"):
            package_name = "/".join(module.split("/")[:2])
        if package_name not in _NODE_BUILTINS:
            analysis.node_modules.add(package_name)
            if package_name in {"openai", "@anthropic-ai/sdk"} or package_name.startswith(
                ("@aws-sdk/", "@google-cloud/")
            ):
                analysis.issue("credential.ambiguous_sdk_auth", path)

    for match in re.finditer(r"\b(?:execFile|spawn|exec|fork)\s*\(\s*['\"]([^'\"]+)['\"]", text):
        _record_binary(match.group(1), analysis)
    if re.search(r"\b(?:execFile|spawn|exec|fork)\s*\(\s*(?!['\"])", text):
        analysis.issue("dependency.ambiguous_binary", path)
    if re.search(r"\b(?:require|import)\s*\(\s*(?!['\"])", text):
        analysis.issue("dependency.ambiguous_node", path)
    if re.search(r"\b(?:eval|new\s+Function)\s*(?:\(|\b)", text):
        analysis.issue("analysis.dynamic_execution", path)
    if re.search(
        r"\b(?:prompt|question)\s*\([^)]*(?:api[\s_-]*key|token|credentials?)", text, re.I
    ):
        analysis.issue("credential.code_access", path)
    if re.search(r"\b(?:authorization|proxy-authorization|bearer)\b", text, re.I):
        analysis.issue("credential.code_access", path)
    if re.search(r"\b(?:login|authenticate|authorize|oauth)\s*\(", text, re.IGNORECASE):
        analysis.issue("credential.ambiguous_code_requirement", path)
    if _contains_credential_identifier(text):
        analysis.issue("credential.code_access", path)


def _scan_shell(text: str, path: str, analysis: _Analysis) -> None:
    for line in text.splitlines():
        for name in _ENV_ASSIGNMENT.findall(line):
            if _is_credential_name(name):
                analysis.issue("credential.code_access", path)
        if re.search(r"\b(?:eval|source|exec)\s+[^\s]", line):
            analysis.issue("analysis.dynamic_execution", path)
        if re.search(r"\$\{!\w+\}", line):
            analysis.issue("credential.ambiguous_dynamic_requirement", path)
        if re.search(r"['\"]?\.env(?:\.[A-Za-z0-9_-]+)?['\"]?", line):
            analysis.issue("credential.code_access", path)
        try:
            lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|")
            lexer.whitespace_split = True
            lexer.commenters = "#"
            tokens = list(lexer)
        except ValueError:
            analysis.issue("analysis.ambiguous_shell", path)
            continue

        command_start = True
        expect_module = False
        python_runtime = False
        for token in tokens:
            if token and all(character in ";&|" for character in token):
                command_start = True
                continue
            if not command_start:
                if token == "-m" and python_runtime:
                    expect_module = True
                    continue
                if expect_module:
                    if token.startswith("$") or "*" in token:
                        analysis.issue("dependency.ambiguous_python", path)
                    elif not _is_stdlib(token):
                        analysis.python_modules.add(token)
                    expect_module = False
                continue
            if token in _SHELL_CONTROL or token.startswith(("#", "{")):
                continue
            if "=" in token and not token.startswith(("=", "$")):
                key, _value = token.split("=", 1)
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                    if _is_credential_name(key):
                        analysis.issue("credential.code_access", path)
                    continue
            command = token.rsplit("/", 1)[-1]
            if command.startswith(("$", "`")):
                analysis.issue("dependency.ambiguous_binary", path)
            else:
                _record_binary(command, analysis)
            command_start = False
            python_runtime = command.lower() in {"python", "python3", "python.exe"}
            if command.lower() in {"python", "python3", "python.exe"}:
                continue

        if _contains_credential_identifier(line):
            analysis.issue("credential.code_access", path)


def _scan_shebang_script(text: str, path: str, analysis: _Analysis) -> None:
    first_line = text.splitlines()[0].lower()
    if "python" in first_line:
        _scan_python(text, path, analysis)
    elif any(runtime in first_line for runtime in ("sh", "bash", "zsh")):
        _scan_shell(text, path, analysis)
    elif "node" in first_line:
        _scan_node(text, path, analysis)
    else:
        analysis.issue("analysis.unsupported_executable", path)


def _record_binary(command: str, analysis: _Analysis) -> None:
    normalized = command.strip().strip("'\"`").rsplit("/", 1)[-1]
    if normalized:
        analysis.binaries.add(normalized)


def _scan_manifests(text_files: dict[str, str], analysis: _Analysis) -> None:
    for path, text in text_files.items():
        name = PurePosixPath(path).name.lower()
        if re.fullmatch(r"requirements(?:[-_.][a-z0-9_.-]+)?\.txt", name):
            _scan_requirements_file(text, path, analysis)
        elif name == "pyproject.toml":
            _scan_pyproject(text, path, analysis)
        elif name == "setup.cfg":
            _scan_setup_cfg(text, path, analysis)
        elif name == "package.json":
            _scan_package_json(text, path, analysis)


def _scan_requirements_file(text: str, path: str, analysis: _Analysis) -> None:
    for line in text.splitlines():
        requirement = line.split("#", 1)[0].strip()
        if not requirement or requirement.startswith("-"):
            if requirement.startswith(
                ("-r ", "--requirement ", "-c ", "--constraint ", "-e ", "--editable ")
            ):
                analysis.issue("dependency.ambiguous_python", path)
            continue
        _add_python_requirement(requirement, path, analysis)


def _add_python_requirement(value: Any, path: str, analysis: _Analysis) -> None:
    if not isinstance(value, str):
        analysis.issue("dependency.ambiguous_python", path)
        return
    try:
        requirement = Requirement(value)
    except InvalidRequirement:
        analysis.issue("dependency.ambiguous_python", path)
        return
    if requirement.url is not None:
        analysis.issue("dependency.ambiguous_python", path)
        return
    analysis.python_requirements.add(str(requirement))


def _scan_pyproject(text: str, path: str, analysis: _Analysis) -> None:
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, RecursionError):
        analysis.issue("analysis.malformed_manifest", path)
        return

    project = data.get("project", {})
    if isinstance(project, dict):
        dependencies = project.get("dependencies", [])
        if isinstance(dependencies, list):
            for requirement in dependencies:
                _add_python_requirement(requirement, path, analysis)
        elif dependencies:
            analysis.issue("dependency.ambiguous_python", path)
        optional = project.get("optional-dependencies", {})
        if isinstance(optional, dict):
            for requirements in optional.values():
                if isinstance(requirements, list):
                    for requirement in requirements:
                        _add_python_requirement(requirement, path, analysis)
                else:
                    analysis.issue("dependency.ambiguous_python", path)
        elif optional:
            analysis.issue("dependency.ambiguous_python", path)
        dynamic = project.get("dynamic", [])
        if (
            isinstance(dynamic, list)
            and "dependencies" in dynamic
            or dynamic
            and not isinstance(dynamic, list)
        ):
            analysis.issue("dependency.ambiguous_python", path)
    elif project:
        analysis.issue("dependency.ambiguous_python", path)

    poetry = (
        data.get("tool", {}).get("poetry", {}) if isinstance(data.get("tool", {}), dict) else {}
    )
    poetry_dependencies = poetry.get("dependencies", {}) if isinstance(poetry, dict) else {}
    if isinstance(poetry_dependencies, dict):
        for name in poetry_dependencies:
            if name.lower() != "python":
                normalized_name = name.lower().replace("-", "_")
                analysis.python_modules.add(normalized_name)
    elif poetry_dependencies:
        analysis.issue("dependency.ambiguous_python", path)


def _scan_setup_cfg(text: str, path: str, analysis: _Analysis) -> None:
    parser = configparser.ConfigParser()
    try:
        parser.read_string(text)
    except configparser.Error:
        analysis.issue("analysis.malformed_manifest", path)
        return
    if parser.has_option("options", "install_requires"):
        for line in parser.get("options", "install_requires").splitlines():
            _add_python_requirement(line.strip(), path, analysis)
    if parser.has_option("options", "extras_require"):
        for line in parser.get("options", "extras_require").splitlines():
            _add_python_requirement(line.strip(), path, analysis)


def _scan_package_json(text: str, path: str, analysis: _Analysis) -> None:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        analysis.issue("analysis.malformed_manifest", path)
        return
    if not isinstance(data, dict):
        analysis.issue("analysis.malformed_manifest", path)
        return

    for section in ("dependencies", "optionalDependencies", "devDependencies", "peerDependencies"):
        dependencies = data.get(section, {})
        if isinstance(dependencies, dict):
            names = {str(name) for name in dependencies}
            analysis.node_modules.update(names)
        elif dependencies:
            analysis.issue("dependency.ambiguous_node", path)

    scripts = data.get("scripts", {})
    if isinstance(scripts, dict) and scripts:
        analysis.has_runnable_code = True
        for command in scripts.values():
            if isinstance(command, str):
                _scan_shell(command, path, analysis)
            else:
                analysis.issue("dependency.ambiguous_binary", path)
    elif scripts:
        analysis.issue("analysis.malformed_manifest", path)


def _scan_documented_binaries(text_files: dict[str, str], analysis: _Analysis) -> None:
    for path, text in text_files.items():
        if PurePosixPath(path).suffix.lower() not in _DOCUMENT_SUFFIXES:
            continue
        for line in text.splitlines():
            if not re.search(r"\b(?:requires?|install|download|ensure|needs?)\b", line, re.I):
                continue
            for binary in _KNOWN_DOC_BINARIES:
                if re.search(
                    rf"(?<![A-Za-z0-9_-]){re.escape(binary)}(?![A-Za-z0-9_-])", line, re.I
                ):
                    analysis.binaries.add(binary)


def _contains_credential_identifier(text: str) -> bool:
    return any(_is_credential_name(match.group(0)) for match in _ENV_NAME.finditer(text))
