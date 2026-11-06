"""Pluggable post-parse fact extractors.

A fact is a typed, located value taken from a document's text: a dimension
annotation, a clause number, a date, an identifier. Extraction runs after
parsing and before/alongside indexing, stores facts in
``knowledge_document_facts`` and lets callers search them without a dedicated
business table. New extractors implement :class:`FactExtractor` and register in
:func:`default_extractors`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from octop.infra.knowledge.parse import ParsedBlock


@dataclass(frozen=True)
class Fact:
    fact_type: str
    value: str
    normalized: str
    locator: dict[str, Any]
    extractor: str


class FactExtractor(Protocol):
    name: str

    def extract(self, text: str, locator: dict[str, Any]) -> Iterable[Fact]: ...


_MEASUREMENT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Diameter / radius / angle / tolerance, tolerating spaces after the sign.
    (re.compile(r"[φΦ⌀]\s*\d+(?:\.\d+)?"), "diameter"),
    (re.compile(r"[Rr]\s*\d+(?:\.\d+)?"), "radius"),
    (re.compile(r"±\s*\d+(?:\.\d+)?"), "tolerance"),
    (re.compile(r"\d+(?:\.\d+)?\s*°"), "angle"),
    # Metric threads: M30×2, M 8 x 1.25.
    (re.compile(r"[Mm]\s*\d+(?:\.\d+)?\s*[×xX*]\s*\d+(?:\.\d+)?"), "thread"),
)


def _normalized_measurement(value: str) -> str:
    compact = re.sub(r"\s+", "", value).replace("Φ", "φ").replace("⌀", "φ")
    return re.sub(r"[A-Za-z]", lambda match: match.group(0).upper(), compact)


class MeasurementExtractor:
    """Diameter/radius/tolerance/angle/thread annotations written as text."""

    name = "measurement"

    def extract(self, text: str, locator: dict[str, Any]) -> Iterable[Fact]:
        seen: set[str] = set()
        for pattern, kind in _MEASUREMENT_PATTERNS:
            for match in pattern.finditer(text):
                value = match.group(0).strip()
                normalized = _normalized_measurement(value)
                if normalized in seen:
                    continue
                seen.add(normalized)
                yield Fact(
                    fact_type=kind,
                    value=value,
                    normalized=normalized,
                    locator=locator,
                    extractor=self.name,
                )


_CLAUSE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"第\s*(\d+(?:\.\d+)*)\s*条"),
    re.compile(r"(?m)^\s*(\d+(?:\.\d+){1,3})\s+(?=\S)"),
)


class ClauseExtractor:
    """Clause numbers such as 第 5.3 条 or a 5.3 heading."""

    name = "clause"

    def extract(self, text: str, locator: dict[str, Any]) -> Iterable[Fact]:
        seen: set[str] = set()
        for pattern in _CLAUSE_PATTERNS:
            for match in pattern.finditer(text):
                number = match.group(1).strip()
                if number in seen:
                    continue
                seen.add(number)
                yield Fact(
                    fact_type="clause",
                    value=number,
                    normalized=number,
                    locator=locator,
                    extractor=self.name,
                )


def default_extractors() -> tuple[FactExtractor, ...]:
    return (MeasurementExtractor(), ClauseExtractor())


def extract_facts(
    blocks: Sequence[ParsedBlock],
    *,
    extractors: Sequence[FactExtractor] | None = None,
    limit: int = 5000,
) -> list[Fact]:
    """Extract facts from every block, de-duplicated by type/value/locator."""
    if limit <= 0:
        return []
    selected = tuple(extractors) if extractors is not None else default_extractors()
    facts: list[Fact] = []
    seen: set[tuple[str, str, str]] = set()
    for block in blocks:
        text = block.text or ""
        if not text.strip():
            continue
        locator = deepcopy(dict(block.locator or {"kind": "document"}))
        for extractor in selected:
            try:
                for fact in extractor.extract(text, deepcopy(locator)):
                    fact_locator = deepcopy(fact.locator)
                    key = (fact.fact_type, fact.normalized, repr(sorted(fact_locator.items())))
                    if key in seen:
                        continue
                    seen.add(key)
                    # Freeze each emitted location before a generator reuses
                    # its dictionary, while retaining extractor refinements.
                    facts.append(
                        Fact(
                            fact_type=fact.fact_type,
                            value=fact.value,
                            normalized=fact.normalized,
                            locator=fact_locator,
                            extractor=fact.extractor,
                        )
                    )
                    if len(facts) >= limit:
                        return facts
            except Exception:  # an extractor bug must not fail ingestion
                # ``extract`` is a generator in practice; exceptions raised
                # while iterating it are outside the call itself and must not
                # escape either.
                continue
    return facts


__all__ = [
    "ClauseExtractor",
    "Fact",
    "FactExtractor",
    "MeasurementExtractor",
    "default_extractors",
    "extract_facts",
]
