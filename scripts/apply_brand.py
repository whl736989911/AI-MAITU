"""Apply brand.config.json to the source tree.

Idempotent and re-runnable: change the brand in ``brand.config.json`` and run
this again — it remembers what it wrote last time (``.brand.applied.json``),
reverts those strings back to tokens, then applies the new values.

Pipeline per file:

    previous brand values  --detokenise-->  %BRAND%  --apply-->  new values

A one-time `tokenise` pass also maps the original upstream name ("Octop")
to ``%BRAND%`` so the very first run works on a pristine checkout.

Textual replacement (not a JSON round-trip) so existing formatting and
Prettier layout survive and the diff stays reviewable. The i18n bundles are the
one place where that needs care: they mix keys and values, which look identical
to a text substitution but are not. A key is a stable identifier shared by all
locales and takes a language-independent slug (``%BRAND_KEY%``); only a value
takes the locale's brand name. :func:`brand_json` splits the two, edits the
strings that change in place, and checks its own work against the parsed
document before returning.

Legal: this script MUST NOT rewrite ``LICENSE``. The software is MIT, which
requires the upstream copyright notice to be included in all copies and
substantial portions. The upstream name in that file is attribution, not
branding; :func:`write` enforces this.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "brand.config.json").read_text(encoding="utf-8"))

TOKEN = CFG["$tokens"]["brand"]  # %BRAND%
TOKEN_FULL = CFG["$tokens"]["full_name"]  # %BRAND_FULL%
TOKEN_KEY = CFG["$tokens"]["key"]  # %BRAND_KEY%
TOKEN_ID = CFG["$tokens"]["id"]  # %BRAND_ID%
TOKEN_ENV = CFG["$tokens"]["env_prefix"]  # %BRAND_ENV%
KEEP = tuple(CFG["$exceptions"]["keep_literal"])

# i18n keys are identifiers: every locale shares them, so a key must never carry
# a per-locale name — that is what tore the bundles apart (`askOctopHint` came
# out as `askMAITU Smart ManufacturingHint` in en.json and `ask麦途智造Hint` in
# zh.json, and the English one grew spaces on top). A key token, or any brand
# string a previous run leaked into a key, resolves to this slug instead.
# Derived from the English short name so the config keeps one source of truth.
KEY_SLUG = re.sub(r"[^0-9A-Za-z]+", "", CFG["short_name"]["en"])
if not KEY_SLUG:
    raise SystemExit("brand.config.json: short_name.en must yield an ASCII slug")
# Lowercase identifier used for commands, directories and file names; the
# display casing lives in name/short_name and must not leak into identifiers.
ID_SLUG = re.sub(r"[^0-9a-z]+", "", CFG["short_name"]["en"].lower())
ENV_PREFIX = re.sub(r"[^0-9A-Z_]+", "", CFG["short_name"]["en"].upper())
if not ID_SLUG or not ENV_PREFIX:
    raise SystemExit("brand.config.json: short_name.en must yield ASCII id/env names")

# Remembers what the last run wrote, as token -> value maps per locale.
MARKER = ROOT / ".brand.applied.json"

# Per-locale replacements, longest token last is irrelevant — exact tokens.
# ID/env are language-independent; they still go through the same table so a
# brand change can detokenise the previous values before applying new ones.
BY_LOCALE = {
    "zh": [
        (TOKEN_FULL, CFG["full_name"]["zh"]),
        (TOKEN, CFG["name"]["zh"]),
        (TOKEN_ID, ID_SLUG),
        (TOKEN_ENV, ENV_PREFIX),
    ],
    "en": [
        (TOKEN_FULL, CFG["full_name"]["en"]),
        (TOKEN, CFG["name"]["en"]),
        (TOKEN_ID, ID_SLUG),
        (TOKEN_ENV, ENV_PREFIX),
    ],
}

TEXT_FILES = {
    "dashboard/src/locales/zh.json": "zh",
    "dashboard/src/locales/en.json": "en",
    "src/octop/i18n/zh.json": "zh",
    "src/octop/i18n/en.json": "en",
}

# "Octop" not followed by "Bot" — OctopBot is an external platform name.
TOKENISE_RE = re.compile(r"Octop(?!Bot)")

CJK = r"\u3400-\u9fff\u3000-\u303f\uff00-\uffef"

# MIT requires the upstream copyright notice to survive in every copy and
# substantial portion of the software. These files carry that notice; the
# upstream name inside them is attribution, not branding.
PROTECTED = frozenset({"LICENSE", "NOTICE", "COPYING", "COPYRIGHT", "AUTHORS"})


def write(p: Path, text: str) -> None:
    """Write a branded file, refusing anything that carries upstream attribution."""
    if p.name.upper() in PROTECTED:
        raise SystemExit(f"refusing to rewrite {p}: it carries the upstream copyright notice")
    p.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------
# token helpers


def load_previous() -> dict[str, dict[str, str]]:
    """token -> previously written value, per locale."""
    if not MARKER.is_file():
        return {}
    try:
        raw = json.loads(MARKER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict[str, str]] = {}
    for loc, mapping in raw.items():
        if isinstance(mapping, dict):
            out[loc] = {str(k): str(v) for k, v in mapping.items() if v}
    return out


def _prose_tokens(previous: dict[str, str]) -> dict[str, str]:
    """One locale's marker entry, minus identifier tokens — those are not prose.

    The id/env values are lowercase technical identifiers: treating them as
    prose would let a rerun rewrite ``maitu.db`` into the display name.
    """
    return {
        token: value
        for token, value in previous.items()
        if token not in {TOKEN_KEY, TOKEN_ID, TOKEN_ENV}
    }


def detokenise(text: str, previous: dict[str, str]) -> str:
    """Revert previously applied values back to their tokens.

    Longest value first, so "苏州麦途" is handled before "麦途".
    """
    for token, value in sorted(previous.items(), key=lambda kv: len(kv[1]), reverse=True):
        if value and value != token:
            text = text.replace(value, token)
    return text


def tokenise(text: str) -> tuple[str, int]:
    """One-time: literal upstream name -> %BRAND%."""
    n = 0

    def sub(_m: re.Match[str]) -> str:
        nonlocal n
        n += 1
        return TOKEN

    return TOKENISE_RE.sub(sub, text), n


def _substitute(text: str, token: str, value: str) -> str:
    """Replace *token* with *value*, fixing CJK/Latin word spacing.

    Source strings were written for a Latin brand ("欢迎使用 Octop"), so a space
    sits where the Latin word began. With a Chinese replacement that space must
    go — but only where it would end up between two CJK runs, never where it
    separates CJK from Latin (e.g. "麦途 session_key").
    """
    if not re.match(rf"[{CJK}]", value):
        return text.replace(token, value)
    t = re.escape(token)
    text = re.sub(rf"(?<=[{CJK}]) {t}", token, text)
    text = re.sub(rf"{t} (?=[{CJK}])", token, text)
    return text.replace(token, value)


def apply_locale(text: str, locale: str) -> tuple[str, int]:
    n = 0
    for token, value in BY_LOCALE[locale]:
        n += text.count(token)
        text = _substitute(text, token, value)
    return text, n


def brand_text(text: str, locale: str, previous: dict[str, str]) -> tuple[str, int, int]:
    """Brand one piece of rendered prose in *locale*.

    Returns ``(text, tokenised, applied)``. This is the whole pipeline for
    key-less files (``index.html``) and per-string for the values of a bundle.
    """
    text = detokenise(text, _prose_tokens(previous))
    text, n1 = tokenise(text)
    text, n2 = apply_locale(text, locale)
    return text, n1, n2


# --------------------------------------------------------------------------
# Technical identifiers that users can still see in prose, scripts and paths
# --------------------------------------------------------------------------
# Capital "Octop" is the display name (handled above). These are the lowercase
# forms users encounter on disk / in a terminal: home directory, database,
# logs, login file, backups, auth directory, CLI commands and env vars. The
# Python package/import name and wire identifiers are intentionally left alone.

TECH_PATH_TOKENS = [
    ("~/.octop", f"~/.{TOKEN_ID}"),
    ("$HOME/.octop", f"$HOME/.{TOKEN_ID}"),
    (".octop-auth", f".{TOKEN_ID}-auth"),
    ("octop-login.txt", f"{TOKEN_ID}-login.txt"),
    ("octop-auto-backup", f"{TOKEN_ID}-auto-backup"),
    ("octop-backup", f"{TOKEN_ID}-backup"),
    ("octop.db", f"{TOKEN_ID}.db"),
    ("octop.log", f"{TOKEN_ID}.log"),
    ("octop_sandbox", f"{TOKEN_ID}_sandbox"),
    ("octop.example.com", f"{TOKEN_ID}.example.com"),
    (".octop", f".{TOKEN_ID}"),
]

# Lines that describe the *Python distribution* keep its real name: a doc that
# says `pip install maitu` would be wrong while the wheel is still `octop`.
TECH_PACKAGE_HINTS = (
    "pip install octop",
    "pip3 install octop",
    "uv pip install octop",
    "import octop",
    "from octop",
    "python -m octop",
    "pypi.org/project/octop",
    "github.com/TencentCloud/Octop",
    "octop[",
    "octop==",
)

# Standalone command word, not part of `octop.infra`, `/octop/`, `X-Octop-*`.
OCTOP_COMMAND_RE = re.compile(r"(?<![A-Za-z0-9_./\\-])octop(?![A-Za-z0-9_/.-])")
OCTOP_ENV_RE = re.compile(r"(?<![A-Za-z0-9_])OCTOP_")

# Files whose text is user/operator visible but which apply_brand previously
# did not touch. Package/import lines inside them stay untouched.
EXTRA_TEXT_FILES = {
    "dashboard/public/offline.html": "zh",
    "dashboard/vite.config.ts": "en",
    "README.md": "en",
    "README_CN.md": "zh",
    "SECURITY.md": "en",
    "CONTRIBUTING.md": "en",
    ".env.example": "en",
    "scripts/README.md": "en",
    "scripts/install-maitu.sh": "en",
    "scripts/build-fpk.sh": "zh",
    "scripts/fnos/common.sh": "zh",
    "fnos/README.md": "zh",
    "fnos/docker/wizard/install": "zh",
}

# Globs for user/operator-visible prose and packaging files that the first
# white-label pass missed (docs, plugin descriptions, FnOS app-center files).
EXTRA_TEXT_GLOBS = [
    ("docs/*.md", "zh"),
    ("docs/*.html", "zh"),
    ("plugins/**/*.md", "en"),
    ("fnos/**/*", "zh"),
    ("scripts/*.sh", "en"),
]
_SKIP_BRAND_GLOBS = ("docs/superpowers/**", "docs/adr/**", "**/node_modules/**")


def tech_tokenise_text(text: str, *, package_sensitive: bool = False) -> tuple[str, int]:
    """Tokenise lowercase technical brand names in user/operator-visible text."""
    n = 0
    for old, new in TECH_PATH_TOKENS:
        hits = text.count(old)
        if hits:
            text = text.replace(old, new)
            n += hits
    # A value that names the Python distribution (`octop[browser]`, `pip install
    # octop`, an import path) keeps the distribution name; only path tokens are
    # safe there.
    if package_sensitive and any(hint in text for hint in TECH_PACKAGE_HINTS):
        return text, n
    out_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        if any(hint in line for hint in TECH_PACKAGE_HINTS):
            out_lines.append(line)
            continue
        line, c1 = OCTOP_COMMAND_RE.subn(TOKEN_ID, line)
        line, c2 = OCTOP_ENV_RE.subn(f"{TOKEN_ENV}_", line)
        n += c1 + c2
        out_lines.append(line)
    return "".join(out_lines), n


def brand_value(text: str, locale: str, prev: dict[str, str]) -> tuple[str, int, int, int]:
    """Brand one i18n value: display name plus lowercase paths/commands."""
    text = detokenise(text, _prose_tokens(prev))
    text, n1 = tokenise(text)
    text, n2 = tech_tokenise_text(text, package_sensitive=True)
    text, n3 = apply_locale(text, locale)
    return text, n1, n2, n3


def tokenise_text_file(text: str, locale: str, prev: dict[str, str]) -> tuple[str, int, int, int]:
    """Brand one non-JSON text file (docs/scripts/offline page)."""
    text = detokenise(text, _prose_tokens(prev))
    text, n1 = tokenise(text)
    text, n2 = tech_tokenise_text(text)
    text, n3 = apply_locale(text, locale)
    return text, n1, n2, n3


# --------------------------------------------------------------------------
# JSON bundles
#
# Keys and values look alike to a text substitution but are not: a key is a
# stable identifier every locale shares, a value is prose that follows the
# locale. Branding both is what produced `askMAITU Smart ManufacturingHint` in
# en.json next to `ask麦途智造Hint` in zh.json — one key, two names, no parity.
#
# So the bundles are split: the document is parsed to learn which literal is a
# key, the raw text is scanned to locate it, and only the literals that actually
# change are spliced back in. Editing in place rather than re-serialising keeps
# indentation, key order and blank lines byte-identical — a run shows up as the
# branded strings and nothing else.

JSON_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"')
JSON_SPACE = " \t\r\n"


def _string_spans(text: str) -> list[tuple[int, int, bool]]:
    """Raw span of every JSON string literal, flagged key (``True``) or value.

    A literal followed by ``:`` is a key — in valid JSON nothing else can come
    before a colon.
    """
    spans: list[tuple[int, int, bool]] = []
    for match in JSON_STRING_RE.finditer(text):
        i = match.end()
        while i < len(text) and text[i] in JSON_SPACE:
            i += 1
        spans.append((match.start(), match.end(), i < len(text) and text[i] == ":"))
    return spans


def _document_strings(node: object) -> list[tuple[bool, str]]:
    """(is_key, text) for every string in a parsed document, in document order."""
    if isinstance(node, dict):
        found: list[tuple[bool, str]] = []
        for key, value in node.items():
            found.append((True, key))
            found.extend(_document_strings(value))
        return found
    if isinstance(node, list):
        found = []
        for value in node:
            found.extend(_document_strings(value))
        return found
    if isinstance(node, str):
        return [(False, node)]
    return []


def brand_strings(prev: dict[str, dict[str, str]]) -> list[str]:
    """Brand strings a key may carry, longest first.

    The current config's names plus whatever a previous run wrote, so a key is
    repaired to the slug whether the brand changed or not. The slug itself is
    left out: a key already sitting on it needs no repair.
    """
    values = {
        CFG[field][locale]
        for field in ("name", "full_name", "short_name")
        for locale in ("zh", "en")
    }
    values.update(_candidate_values(prev))
    values.update(m[TOKEN_KEY] for m in prev.values() if m.get(TOKEN_KEY))
    values.discard(KEY_SLUG)
    return sorted(values, key=len, reverse=True)


def brand_key(key: str, brand_values: list[str]) -> tuple[str, int]:
    """Rewrite one key to the language-independent slug.

    ``%BRAND_KEY%``, a stray ``%BRAND%``, and any brand string a previous run
    leaked into a key all collapse to :data:`KEY_SLUG` — the same bytes in every
    locale, and no space for the English name to drag in. The upstream name is
    left alone: renaming an established key means renaming its call sites, which
    is not this script's job.
    """
    n = 0
    for value in brand_values:  # longest first, prefixes lose
        n += key.count(value)
        key = key.replace(value, KEY_SLUG)
    for token in (TOKEN_FULL, TOKEN_KEY, TOKEN_ID, TOKEN_ENV, TOKEN):
        n += key.count(token)
        key = key.replace(token, KEY_SLUG)
    return key, n


def brand_json(
    text: str, locale: str, previous: dict[str, str], brand_values: list[str]
) -> tuple[str, int, int, int]:
    """Brand one JSON bundle: values by locale, keys by slug.

    Returns ``(text, tokenised, applied, keys)``. Raises :class:`ValueError` if
    the document cannot be split cleanly, so the caller can leave it untouched
    rather than write a half-branded bundle.
    """
    document = json.loads(text)
    expected = _document_strings(document)
    spans = _string_spans(text)
    if len(spans) != len(expected):
        raise ValueError(f"scanned {len(spans)} literals, document holds {len(expected)}")

    edits: list[tuple[int, int, str]] = []
    tokenised = applied = keys = 0
    for (start, end, is_key), (doc_is_key, literal) in zip(spans, expected, strict=True):
        current = json.loads(text[start:end])
        if current != literal or is_key != doc_is_key:
            raise ValueError("string literals do not line up with the parsed document")
        if is_key:
            new, n = brand_key(current, brand_values)
            keys += n
        else:
            new, n1, n2, n3 = brand_value(current, locale, previous)
            tokenised += n1 + n2
            applied += n3
        if new != current:
            edits.append((start, end, json.dumps(new, ensure_ascii=False)))

    if not edits:
        return text, tokenised, applied, keys
    out = text
    for start, end, body in reversed(edits):
        out = out[:start] + body + out[end:]
    json.loads(out)  # never emit a broken bundle
    return out, tokenised, applied, keys


# --------------------------------------------------------------------------
# Python sources
#
# The backend hardcodes the product name in a few user-visible places (CLI help
# and prompts, the OpenAPI title, log lines). Those must follow the brand — but
# the same files also carry identifiers (`OctopError`, `OctopServer`), wire
# header names (`X-Octop-Agent-Id`) and the import package, which must not move.
#
# So: rewrite string literals only (never code), and inside them match only the
# standalone word — not `Octop` followed by a letter, and not behind `X-`.

PY_ROOT = ROOT / "src/octop"
PY_TOKEN_RE = re.compile(r"(?<!X-)Octop(?![A-Za-z])")
CJK_RE = re.compile(f"[{CJK}]")

_DEF_NODES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _docstring_nodes(tree: ast.AST) -> set[ast.AST]:
    """Constant nodes that are docstrings — internal prose, not rendered text."""
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    found: set[ast.AST] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        holder = parents.get(node)
        if (
            isinstance(holder, ast.Expr)
            and holder.value is node
            and isinstance(parents.get(holder), _DEF_NODES)
        ):
            found.add(node)
    return found


def _candidate_values(prev: dict[str, dict[str, str]]) -> list[str]:
    """Previously applied brand strings, longest first so prefixes lose.

    The key/id/env tokens are not prose: they belong to identifiers, and
    reverting them inside a Python literal would rewrite code-ish strings such
    as ``"maitu.db"`` as a product name. Only display names are reverted here.
    """
    skip = {TOKEN_KEY, TOKEN_ID, TOKEN_ENV}
    seen = {
        value
        for per_locale in prev.values()
        for token, value in per_locale.items()
        if value and token not in skip
    }
    return sorted(seen, key=len, reverse=True)


def rewrite_python(path: Path, prev_values: list[str]) -> tuple[str, int]:
    """Brand the string literals of one Python file, leaving code untouched."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source, 0

    # `ast` reports col_offset in UTF-8 *bytes*, not characters, so splice the
    # byte view — otherwise any line holding non-ASCII text lands off by the
    # width difference and the rewrite corrupts the file.
    data = source.encode("utf-8")
    line_bytes = [0]
    for line in source.splitlines(keepends=True):
        line_bytes.append(line_bytes[-1] + len(line.encode("utf-8")))

    docstrings = _docstring_nodes(tree)
    # Constants nested inside an f-string carry offsets that do not map back to
    # absolute source positions, so splice the JoinedStr as a whole instead.
    fstring_parts: set[ast.AST] = set()
    for joined in (n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)):
        fstring_parts.update(ast.walk(joined))

    edits: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        is_fstring = isinstance(node, ast.JoinedStr)
        if not is_fstring:
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if node in docstrings or node in fstring_parts:
                continue
        raw = ast.get_source_segment(source, node)
        if raw is None:
            continue
        if not PY_TOKEN_RE.search(raw) and not any(v in raw for v in prev_values):
            continue

        # Chinese literals take the Chinese brand, everything else the English one.
        locale = "zh" if CJK_RE.search(raw) else "en"
        body = raw
        for value in prev_values:
            if PY_TOKEN_RE.search(value):
                continue
            body = body.replace(value, TOKEN)
        body = PY_TOKEN_RE.sub(TOKEN, body)
        body, _ = apply_locale(body, locale)
        if body == raw:
            continue

        start = line_bytes[node.lineno - 1] + node.col_offset
        end = line_bytes[node.end_lineno - 1] + node.end_col_offset
        # Guard against offset drift: a splice must land exactly on the segment.
        try:
            if data[start:end].decode("utf-8") != raw:
                continue
        except UnicodeDecodeError:
            continue
        edits.append((start, end, body.encode("utf-8")))

    if not edits:
        return source, 0
    out = data
    for start, end, body in sorted(edits, reverse=True):
        out = out[:start] + body + out[end:]
    # Never emit a file that no longer parses; the tree is the safety net.
    try:
        text = out.decode("utf-8")
        ast.parse(text)
    except (UnicodeDecodeError, SyntaxError):
        return source, 0
    return text, len(edits)


# --------------------------------------------------------------------------


def main() -> int:
    prev = load_previous()
    total_tokenised = total_applied = 0
    changed: list[str] = []
    key_values = brand_strings(prev)

    # ---- locale / i18n bundles -------------------------------------------
    for rel, locale in TEXT_FILES.items():
        p = ROOT / rel
        if not p.is_file():
            print(f"  ! missing: {rel}")
            continue
        before = p.read_text(encoding="utf-8")
        try:
            text, n1, n2, n3 = brand_json(before, locale, prev.get(locale, {}), key_values)
        except ValueError as exc:
            print(f"  ! {rel}: {exc} — left untouched")
            continue
        if text != before:
            write(p, text)
            changed.append(f"{rel}  (tokenised {n1}, applied {n2}, keys {n3})")
            total_tokenised += n1
            total_applied += n2

    # ---- index.html ------------------------------------------------------
    p = ROOT / "dashboard/index.html"
    before = p.read_text(encoding="utf-8")
    src, n1, n2 = brand_text(before, "zh", prev.get("zh", {}))
    lg = CFG["logo"]
    src = re.sub(r'href="/favico\.svg"', f'href="{lg["favicon"]}"', src)
    src = re.sub(r'href="/apple-touch-icon\.png"', f'href="{lg["apple_touch_icon"]}"', src)
    src = re.sub(r'src="/logo\.svg"', f'src="{lg["mark"]}"', src)
    if src != before:
        write(p, src)
        changed.append(f"dashboard/index.html  (tokenised {n1}, applied {n2})")
        total_tokenised += n1
        total_applied += n2

    # ---- docs / scripts / offline page / installer text ------------------
    for rel, locale in EXTRA_TEXT_FILES.items():
        fp = ROOT / rel
        if not fp.is_file():
            continue
        before = fp.read_text(encoding="utf-8")
        text, n1, n2, n3 = tokenise_text_file(before, locale, prev.get(locale, {}))
        if rel == "dashboard/public/offline.html":
            text = re.sub(
                r'src="(?:/?brand/[^"]+|logo\.svg)"',
                f'src="{lg["mark"].lstrip("/")}"',
                text,
            )
        if text != before:
            write(fp, text)
            changed.append(f"{rel}  (tokenised {n1 + n2}, applied {n3})")
            total_tokenised += n1 + n2
            total_applied += n3

    # ---- docs / plugin descriptions / FnOS packaging prose ---------------
    processed = set(EXTRA_TEXT_FILES)
    for pattern, locale in EXTRA_TEXT_GLOBS:
        for fp in sorted(ROOT.glob(pattern)):
            if not fp.is_file():
                continue
            rel = fp.relative_to(ROOT).as_posix()
            if rel in processed:
                continue
            if fp.name.upper() in PROTECTED:
                continue
            if any(rel.startswith(prefix.removesuffix("/**")) for prefix in _SKIP_BRAND_GLOBS):
                continue
            processed.add(rel)
            try:
                before = fp.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if not any(marker in before for marker in ("Octop", "octop", f"{TOKEN_ENV}_")):
                continue
            text, n1, n2, n3 = tokenise_text_file(before, locale, prev.get(locale, {}))
            if text != before:
                write(fp, text)
                changed.append(f"{rel}  (tokenised {n1 + n2}, applied {n3})")
                total_tokenised += n1 + n2
                total_applied += n3

    # ---- PWA manifest (fully derived, no history needed) -----------------
    p = ROOT / "dashboard/public/manifest.json"
    man = json.loads(p.read_text(encoding="utf-8"))
    man_before = json.dumps(man, ensure_ascii=False, sort_keys=True)
    man.update(
        {
            "id": "./",
            "start_url": "./",
            "scope": "./",
            "name": CFG["name"]["zh"],
            "short_name": CFG["short_name"]["zh"],
            "description": CFG["description"]["zh"],
            "theme_color": CFG["pwa"]["theme_color"],
            "background_color": CFG["pwa"]["background_color"],
            "icons": [
                {
                    "src": lg["pwa_192"].lstrip("/"),
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any",
                },
                {
                    "src": lg["pwa_512"].lstrip("/"),
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any",
                },
                {
                    "src": lg["pwa_512"].lstrip("/"),
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
                {
                    "src": lg["apple_touch_icon"].lstrip("/"),
                    "sizes": "180x180",
                    "type": "image/png",
                    "purpose": "any",
                },
            ],
        }
    )
    if json.dumps(man, ensure_ascii=False, sort_keys=True) != man_before:
        write(p, json.dumps(man, ensure_ascii=False, indent=2) + "\n")
        changed.append("dashboard/public/manifest.json")

    ts = ROOT / "dashboard/src/brand.generated.ts"
    nm = CFG["name"]
    fl = CFG["full_name"]
    sh = CFG["short_name"]
    ds = CFG["description"]
    col = CFG["color"]
    lnk = CFG["links"]["project_repo"]
    lnk_help = CFG["links"]["help"]
    body = f'''// AUTO-GENERATED by scripts/apply_brand.py — do not edit by hand.
// Source of truth: brand.config.json

import {{ publicUrl }} from "./utils/publicUrl";

export const BRAND = {{
  name: {{ zh: "{nm["zh"]}", en: "{nm["en"]}" }},
  fullName: {{ zh: "{fl["zh"]}", en: "{fl["en"]}" }},
  shortName: {{ zh: "{sh["zh"]}", en: "{sh["en"]}" }},
  id: "{ID_SLUG}",
  envPrefix: "{ENV_PREFIX}",
  description: {{
    zh: "{ds["zh"]}",
    en: "{ds["en"]}",
  }},
  logo: {{
    mark: publicUrl("{lg["mark"]}"),
    wordmarkLight: publicUrl("{lg["wordmark_light"]}"),
    wordmarkDark: publicUrl("{lg["wordmark_dark"]}"),
    favicon: publicUrl("{lg["favicon"]}"),
    appleTouchIcon: publicUrl("{lg["apple_touch_icon"]}"),
    pwa192: publicUrl("{lg["pwa_192"]}"),
    pwa512: publicUrl("{lg["pwa_512"]}"),
  }},
  color: {{
    brand: "{col["brand"]}",
    accent: "{col["accent"]}",
  }},
  links: {{
    // Empty string hides the "project URL" entry in the account menu.
    projectRepo: "{lnk}",
    // Empty string falls back to the in-app API docs page.
    help: "{lnk_help}",
  }},
}} as const;

/** Pick the brand name for the active UI language. */
export function brandName(lang: string | undefined): string {{
  return lang?.toLowerCase().startsWith("zh") ? BRAND.name.zh : BRAND.name.en;
}}

/** Wordmark for the current theme. */
export function wordmark(isDark: boolean): string {{
  return isDark ? BRAND.logo.wordmarkDark : BRAND.logo.wordmarkLight;
}}
'''
    if not ts.is_file() or ts.read_text(encoding="utf-8") != body:
        ts.parent.mkdir(parents=True, exist_ok=True)
        write(ts, body)
        changed.append("dashboard/src/brand.generated.ts")
    # ---- backend runtime defaults (derived from the same config) ----------
    py_defaults = ROOT / "src/octop/branding_defaults.py"
    runtime_defaults = {
        "name": CFG["name"],
        "full_name": CFG["full_name"],
        "short_name": CFG["short_name"],
        "description": CFG["description"],
        "colors": {key: CFG["color"][key] for key in ("brand", "accent")},
        "pwa": CFG["pwa"],
        "logos": CFG["logo"],
    }
    py_body = (
        '"""Generated runtime defaults from brand.config.json; do not edit by hand."""\n\n'
        f"DEFAULTS = {runtime_defaults!r}\n"
    )
    if not py_defaults.is_file() or py_defaults.read_text(encoding="utf-8") != py_body:
        write(py_defaults, py_body)
        changed.append("src/octop/branding_defaults.py")

    # ---- backend Python string literals ----------------------------------
    prev_values = _candidate_values(prev)
    py_hits = py_files = 0
    for py in sorted(PY_ROOT.rglob("*.py")):
        if py == py_defaults:
            continue
        new_src, n = rewrite_python(py, prev_values)
        if n:
            write(py, new_src)
            py_hits += n
            py_files += 1
    if py_hits:
        changed.append(f"backend Python literals ({py_hits} in {py_files} files)")

    # ---- remember, so the next run can revert ----------------------------
    # The key slug is one language-independent value; it is recorded per locale
    # to keep the shape uniform, and it is what lets a re-brand move keys off the
    # slug the last run wrote.
    MARKER.write_text(
        json.dumps(
            {
                locale: {
                    TOKEN: CFG["name"][locale],
                    TOKEN_FULL: CFG["full_name"][locale],
                    TOKEN_KEY: KEY_SLUG,
                    TOKEN_ID: ID_SLUG,
                    TOKEN_ENV: ENV_PREFIX,
                }
                for locale in ("zh", "en")
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # ---- report ----------------------------------------------------------
    print(f"brand : {CFG['name']['zh']}  /  {CFG['name']['en']}")
    print(f"full  : {CFG['full_name']['zh']}  /  {CFG['full_name']['en']}")
    print(f"tokenised {total_tokenised}, applied {total_applied}\n")
    for c in changed:
        print(f"  ✓ {c}")
    if not changed:
        print("  (nothing to do — already applied)")

    print("\nremaining literal 'Octop' in brand-carrying files:")
    leftovers = 0
    for rel in [*TEXT_FILES, "dashboard/index.html", "dashboard/public/manifest.json"]:
        raw = (ROOT / rel).read_text(encoding="utf-8")
        if rel in TEXT_FILES:
            # Keys are identifiers, not branding: the upstream name inside one
            # (`askOctopHint`) is the key's identity and stays put.
            hits = sum(
                len(TOKENISE_RE.findall(value))
                for is_key, value in _document_strings(json.loads(raw))
                if not is_key
            )
        else:
            hits = len(TOKENISE_RE.findall(raw))
        leftovers += hits
        if hits:
            print(f"  {rel}: {hits}")
    if not leftovers:
        print("  none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
