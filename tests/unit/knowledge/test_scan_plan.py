"""Unit tests for the scan planner.

The planner is where design §8.1's comparison and §8.3's two safety rules live,
and it is pure — so these are the tests that pin the rules themselves rather
than a transport's behaviour.
"""

from __future__ import annotations

from octop.infra.knowledge.sources.base import SourceEntry
from octop.infra.knowledge.sources.scan import (
    DEBOUNCE_SECONDS,
    DELETE_CONFIRM_SECONDS,
    IndexedFile,
    filter_entries,
    plan_scan,
)

_NOW = 1_000_000


def _file(path: str, size: int = 10, modified_at: int | None = 5_000) -> SourceEntry:
    return SourceEntry(path=path, size=size, modified_at=modified_at, is_dir=False)


def _folder(path: str) -> SourceEntry:
    return SourceEntry(path=path, size=0, modified_at=None, is_dir=True)


def _indexed(
    document_id: str = "doc-1",
    source_path: str = "a.md",
    *,
    size: int = 10,
    modified_at: int | None = 5_000,
    observed_size: int | None = None,
    observed_modified_at: int | None = None,
    observed_at: int | None = None,
    delete_pending_since: int | None = None,
    processed: bool = True,
) -> IndexedFile:
    return IndexedFile(
        document_id=document_id,
        source_path=source_path,
        size=size,
        modified_at=modified_at,
        observed_size=observed_size,
        observed_modified_at=observed_modified_at,
        observed_at=observed_at,
        delete_pending_since=delete_pending_since,
        processed=processed,
    )


def test_first_sighting_is_observed_not_processed() -> None:
    """A file nobody has seen before is the most likely one to be mid-copy."""
    plan = plan_scan([_file("a.md")], [], now=_NOW)

    assert plan.added == []
    assert [entry.path for entry in plan.deferred] == ["a.md"]
    assert plan.observations == {"a.md": (10, 5_000)}


def test_a_settled_new_file_is_added() -> None:
    """A row that has only been *seen* is not an unchanged file."""
    plan = plan_scan(
        [_file("a.md")],
        [
            _indexed(
                source_path="a.md",
                observed_size=10,
                observed_modified_at=5_000,
                observed_at=_NOW - 600,
                processed=False,
            )
        ],
        now=_NOW,
        debounce_seconds=DEBOUNCE_SECONDS,
    )

    assert [entry.path for entry in plan.added] == ["a.md"]
    assert plan.deferred == []


def test_a_file_that_changes_between_scans_is_not_processed() -> None:
    """The debounce: a transfer in progress keeps resetting the clock."""
    plan = plan_scan(
        [_file("a.md", size=99)],
        [_indexed(observed_size=10, observed_modified_at=5_000, observed_at=_NOW - 600)],
        now=_NOW,
    )

    assert plan.updated == []
    assert [entry.path for entry in plan.deferred] == ["a.md"]
    assert plan.observations == {"a.md": (99, 5_000)}


def test_an_observation_inside_the_window_is_not_refreshed() -> None:
    """Refreshing the clock every scan would mean a file is never old enough."""
    plan = plan_scan(
        [_file("a.md", size=40)],
        [
            _indexed(
                observed_size=40,
                observed_modified_at=5_000,
                observed_at=_NOW - 5,
            )
        ],
        now=_NOW,
        debounce_seconds=DEBOUNCE_SECONDS,
    )

    assert [entry.path for entry in plan.deferred] == ["a.md"]
    assert plan.observations == {}


def test_an_unchanged_file_needs_no_work() -> None:
    plan = plan_scan([_file("a.md")], [_indexed()], now=_NOW)

    assert [entry.path for entry in plan.unchanged] == ["a.md"]
    assert plan.added == [] and plan.updated == [] and plan.deferred == []


def test_a_changed_file_that_settled_is_updated() -> None:
    plan = plan_scan(
        [_file("a.md", size=40)],
        [_indexed(observed_size=40, observed_modified_at=5_000, observed_at=_NOW - 600)],
        now=_NOW,
    )

    assert [entry.path for entry in plan.updated] == ["a.md"]


def test_a_missing_file_starts_its_window_and_is_not_removed_yet() -> None:
    plan = plan_scan([], [_indexed()], now=_NOW)

    assert [row.document_id for row in plan.absent] == ["doc-1"]
    assert plan.removed == []


def test_a_missing_file_is_removed_only_after_the_window() -> None:
    still_waiting = plan_scan(
        [],
        [_indexed(delete_pending_since=_NOW - DELETE_CONFIRM_SECONDS + 1)],
        now=_NOW,
    )
    assert still_waiting.removed == []

    confirmed = plan_scan(
        [],
        [_indexed(delete_pending_since=_NOW - DELETE_CONFIRM_SECONDS)],
        now=_NOW,
    )
    assert confirmed.removed == ["doc-1"]


def test_a_file_that_reappears_is_not_scheduled_for_removal() -> None:
    """A share that reconnects leaves the file exactly as it was."""
    plan = plan_scan([_file("a.md")], [_indexed(delete_pending_since=_NOW - 9_999)], now=_NOW)

    assert plan.removed == []
    assert [entry.path for entry in plan.unchanged] == ["a.md"]


def test_folders_are_listed_but_never_planned() -> None:
    plan = plan_scan([_folder("reports"), _file("reports/q1.md")], [], now=_NOW)

    assert [row.source_path for row in plan.absent] == []
    assert [entry.path for entry in plan.deferred] == ["reports/q1.md"]


def test_a_source_without_timestamps_settles_on_size_alone() -> None:
    """An SMB server may report no modification time; size still catches a copy."""
    plan = plan_scan(
        [_file("a.md", size=10, modified_at=None)],
        [
            _indexed(
                source_path="a.md",
                size=0,
                modified_at=None,
                observed_size=10,
                observed_modified_at=None,
                observed_at=_NOW - 600,
                processed=False,
            )
        ],
        now=_NOW,
        debounce_seconds=DEBOUNCE_SECONDS,
    )

    assert [entry.path for entry in plan.added] == ["a.md"]


def test_counts_describe_the_whole_scan() -> None:
    plan = plan_scan(
        [_file("a.md"), _file("b.md", size=1), _folder("dirs")],
        [
            _indexed(document_id="doc-a"),
            _indexed(document_id="doc-gone", source_path="gone.md"),
        ],
        now=_NOW,
        debounce_seconds=0,
    )

    counts = plan.counts
    assert counts["scanned"] == 3  # a unchanged, b deferred, gone absent
    assert counts["added"] == 0  # b has no observation yet
    assert counts["deferred"] == 1
    assert counts["removed"] == 0


def test_filters_keep_everything_when_unset() -> None:
    entries = [_file("a.md"), _file("b.pdf")]

    assert filter_entries(entries) == entries


def test_include_globs_match_a_name_at_any_depth() -> None:
    entries = [_file("a.md"), _file("reports/q1.pdf"), _folder("reports")]

    kept = filter_entries(entries, include_globs="*.pdf")

    assert [entry.path for entry in kept] == ["reports/q1.pdf"]


def test_include_globs_match_a_bare_name() -> None:
    kept = filter_entries([_file("docs/policy.md")], include_globs="policy.md")

    assert [entry.path for entry in kept] == ["docs/policy.md"]


def test_exclude_globs_win_over_includes() -> None:
    entries = [_file("a.md"), _file("draft.md")]

    kept = filter_entries(entries, include_globs="*.md", exclude_globs="draft.*")

    assert [entry.path for entry in kept] == ["a.md"]


def test_a_prefix_pattern_confines_to_a_folder() -> None:
    entries = [_file("docs/a.md"), _file("legal/b.md")]

    kept = filter_entries(entries, include_globs="docs/*")

    assert [entry.path for entry in kept] == ["docs/a.md"]


def test_excluding_a_folder_excludes_its_contents() -> None:
    entries = [_file("drafts/a.md"), _file("docs/b.md")]

    kept = filter_entries(entries, exclude_globs="drafts")

    assert [entry.path for entry in kept] == ["docs/b.md"]


def test_including_a_folder_keeps_its_contents() -> None:
    entries = [_file("docs/a.md"), _file("docs/deep/b.md"), _file("legal/c.md")]

    kept = filter_entries(entries, include_globs="docs")

    assert [entry.path for entry in kept] == ["docs/a.md", "docs/deep/b.md"]


def test_patterns_may_be_newline_or_comma_separated() -> None:
    entries = [_file("a.md"), _file("b.pdf"), _file("c.txt")]

    kept = filter_entries(entries, include_globs="*.md,\n *.pdf")

    assert [entry.path for entry in kept] == ["a.md", "b.pdf"]
