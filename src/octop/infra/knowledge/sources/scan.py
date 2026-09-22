"""What a scan decided to do, and why — pure, so it can be reasoned about.

Design §8.1 compares path, size, modification time and content hash; §8.3 adds
the two rules that make a scan safe to run against a network share:

* **Debounce.** A file being copied changes while it is being copied. A file is
  only processed once its size and modification time have held still for the
  debounce window, which is why the previous observation is kept. Without this,
  a scan landing mid-copy would index half a document and never notice.
* **Confirm before deleting.** A file that vanished is not removed on sight.
  A share that blinked, or a NAS that dropped its connection for one listing,
  would otherwise read as "the folder was emptied".

This module holds no I/O and no database: it takes what the source reported plus
the rows already indexed, and returns a plan. That is the part worth testing,
and testing it through a live share would hide the rules behind a transport.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from octop.infra.knowledge.sources.base import SourceEntry

DEBOUNCE_SECONDS = 30
"""How long a file's size and modification time must hold still before indexing.

Expressed as elapsed time rather than "two consecutive scans" so that a manual
sync of a settled folder processes it on the spot — the rule design §14 wants
("手动同步可以立即触发处理") — while a file still being copied keeps resetting
the clock.
"""

DELETE_CONFIRM_SECONDS = 300
"""How long a missing file is held before it is removed.

Long enough that a share reconnecting, or a NAS finishing a remount, does not
delete anything; short enough that a genuinely deleted file leaves search within
a few scans. Design §8.3 asks for "短暂确认期" and names no number, so it is a
constant here rather than a literal spread through the logic.
"""


@dataclass(frozen=True)
class IndexedFile:
    """The parts of an indexed row a scan compares against.

    Its own shape so the planner does not depend on the row type, and so a test
    can describe an index without building one.

    ``processed`` is what separates "this file is indexed and unchanged" from
    "this file has only been seen": a discovered row records the identity it was
    seen with, and without this flag that identity would look like an unchanged
    file forever.
    """

    document_id: str
    source_path: str
    size: int
    modified_at: int | None
    observed_size: int | None
    observed_modified_at: int | None
    observed_at: int | None
    delete_pending_since: int | None
    processed: bool = True


@dataclass(frozen=True)
class ScanPlan:
    """The change set one scan produced."""

    added: list[SourceEntry] = field(default_factory=list)
    """Files with no index row that are ready to be indexed."""
    updated: list[SourceEntry] = field(default_factory=list)
    """Files whose index row exists and is now out of date."""
    removed: list[str] = field(default_factory=list)
    """Document ids to delete: missing for longer than the confirm window."""
    absent: list[IndexedFile] = field(default_factory=list)
    """Rows whose file was not listed, which now start their confirm window."""
    deferred: list[SourceEntry] = field(default_factory=list)
    """Files seen but still changing, so deliberately not processed yet."""
    observations: dict[str, tuple[int, int | None]] = field(default_factory=dict)
    """``path -> (size, modified_at)`` to remember for the next scan."""
    unchanged: list[SourceEntry] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        """The counters a finished run records."""
        return {
            "scanned": (
                len(self.added)
                + len(self.updated)
                + len(self.unchanged)
                + len(self.absent)
                + len(self.removed)
                + len(self.deferred)
            ),
            "added": len(self.added),
            "updated": len(self.updated),
            "removed": len(self.removed),
            "deferred": len(self.deferred),
        }


def filter_entries(
    entries: list[SourceEntry], *, include_globs: str = "", exclude_globs: str = ""
) -> list[SourceEntry]:
    """The entries a source's include/exclude rules keep.

    A setting an administrator typed has to do something, so this is applied to
    every scan rather than stored and ignored (design §3.2 asks for both rules).

    Patterns are newline- or comma-separated and match with ``fnmatch`` against
    the path, the bare file name, and every ancestor folder — so ``drafts``
    excludes that folder and everything under it, ``*.pdf`` finds a PDF at any
    depth, and ``docs/*`` keeps one level of a folder. ``*`` crosses ``/``,
    which is what makes the first two examples behave the way they read.
    """
    includes = _patterns(include_globs)
    excludes = _patterns(exclude_globs)
    if not includes and not excludes:
        return entries
    kept: list[SourceEntry] = []
    for entry in entries:
        candidates = _candidates(entry.path)
        if any(fnmatchcase(candidate, pattern) for pattern in excludes for candidate in candidates):
            continue
        if includes and not any(
            fnmatchcase(candidate, pattern) for pattern in includes for candidate in candidates
        ):
            continue
        kept.append(entry)
    return kept


def _patterns(text: str) -> list[str]:
    return [part.strip() for part in text.replace(",", "\n").splitlines() if part.strip()]


def _candidates(path: str) -> list[str]:
    """Every prefix of *path*, deepest last, plus its bare name.

    The ancestor prefixes are what let a rule name a folder and have it apply to
    the folder's contents; the bare name is what lets ``report.docx`` match a
    file at any depth.
    """
    parts = path.split("/")
    return ["/".join(parts[: index + 1]) for index in range(len(parts))] + [parts[-1]]


def plan_scan(
    entries: list[SourceEntry],
    indexed: list[IndexedFile],
    *,
    now: int,
    debounce_seconds: int = DEBOUNCE_SECONDS,
    confirm_seconds: int = DELETE_CONFIRM_SECONDS,
) -> ScanPlan:
    """Compare what the source reported against what is indexed.

    ``entries`` is everything the source listed, folders included; only files
    are planned, because a folder has nothing to parse. A file the index has not
    seen yet is still an :attr:`ScanPlan.added` candidate once it settles: the
    index row is created from the plan, so a first sighting only records what
    was seen and the next scan decides.
    """
    by_path = {row.source_path: row for row in indexed}
    plan = ScanPlan()
    seen: set[str] = set()

    for entry in entries:
        path = entry.path
        seen.add(path)
        if entry.is_dir:
            continue
        row = by_path.get(path)
        settled, rewrite = _classify(
            entry,
            None if row is None else _observation(row),
            now=now,
            debounce_seconds=debounce_seconds,
        )
        if rewrite:
            plan.observations[path] = (entry.size, entry.modified_at)
        indexed_and_same = (
            row is not None
            and row.processed
            and (row.size, row.modified_at) == (entry.size, entry.modified_at)
        )
        if indexed_and_same:
            plan.unchanged.append(entry)
            continue
        if settled:
            # A file that was never indexed — or was seen but not processed — is
            # an addition; only a previously indexed file can be an update.
            (plan.updated if row is not None and row.processed else plan.added).append(entry)
        else:
            plan.deferred.append(entry)

    for row in indexed:
        if row.source_path in seen:
            continue
        _plan_missing(row, plan, now=now, confirm_seconds=confirm_seconds)

    return plan


def _observation(row: IndexedFile) -> tuple[int | None, int | None, int | None]:
    return (row.observed_size, row.observed_modified_at, row.observed_at)


def _classify(
    entry: SourceEntry,
    observed: tuple[int | None, int | None, int | None] | None,
    *,
    now: int,
    debounce_seconds: int,
) -> tuple[bool, bool]:
    """``(settled, rewrite_observation)`` for one file.

    A file is settled when a previous scan saw this same identity and that was
    at least ``debounce_seconds`` ago. The observation is rewritten only when
    the identity moved or none was recorded: refreshing it on every scan would
    reset the clock and a file would never be old enough to process.
    """
    if observed is None or observed[0] is None:
        return False, True
    size, modified_at, observed_at = observed
    if (size, modified_at) != (entry.size, entry.modified_at):
        return False, True
    if observed_at is None or now - observed_at < debounce_seconds:
        return False, False
    return True, False


def _plan_missing(row: IndexedFile, plan: ScanPlan, *, now: int, confirm_seconds: int) -> None:
    """An indexed file the source did not list.

    Removing it is a two-step decision: the first scan that misses it starts the
    window and changes nothing, and only a later scan past the window acts. A
    share that reconnects in between therefore leaves the file exactly as it was.
    """
    if row.delete_pending_since is None:
        plan.absent.append(row)
        return
    if now - row.delete_pending_since >= confirm_seconds:
        plan.removed.append(row.document_id)
