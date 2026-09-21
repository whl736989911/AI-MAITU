"""Tests for the Not-Applicable STUB helper."""

from __future__ import annotations

import pytest

from octop.cli.support.stub import EXIT_NOT_APPLICABLE, not_applicable


def test_not_applicable_exits_with_code_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        not_applicable("no embedding subsystem")
    assert exc.value.code == EXIT_NOT_APPLICABLE
    err = capsys.readouterr().err
    # The brand in the prefix is owned by brand.config.json; what is contractual
    # is that the caller's message reaches stderr behind the standard prefix.
    assert "Not applicable for" in err
    assert "no embedding subsystem" in err


def test_not_applicable_includes_suggestion(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        not_applicable("reason here", suggestion="run X instead")
    err = capsys.readouterr().err
    assert "Suggestion: run X instead" in err


def test_not_applicable_includes_docs_url(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        not_applicable("reason", docs_url="https://example.com/docs")
    err = capsys.readouterr().err
    assert "https://example.com/docs" in err
