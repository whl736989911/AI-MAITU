"""The feature-agent id — the one name a definition's personalization can have.

``feat-<feature_id>`` is a convention, so everything here is about where the
convention holds and where it does not. The two id rules involved are **not the same
rule**:

* a definition id (``store._ID_RE``) is ``^[a-z0-9][a-z0-9_-]{0,63}$`` — 64 chars
  max, ending in anything the body may contain, ``-``/``_`` included;
* an agent id (``manager._CUSTOM_AGENT_ID_RE``, applied by
  ``validate_custom_agent_id``) is 3-64 chars that **start and end** with a letter
  or a digit.

So a definition id longer than 59 characters, or one ending in a separator, cannot
carry the prefix into a legal agent id. The boundary is pinned here, and the shipped
library is checked against it: a bundled feature nobody could ever personalize would
be a silent hole in the personalization surface.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from octop.infra.agents.manager import validate_custom_agent_id
from octop.infra.errors import OctopError
from octop.infra.features.catalog import FeatureCatalog
from octop.infra.features.personalization import (
    AGENT_ID_PREFIX,
    feature_agent_id,
    materialized_feature_agent_id,
)
from octop.infra.features.store import _ID_RE

# 59 body characters: ``feat-`` (5) + 59 = 64, the agent rule's own ceiling.
LONGEST_NAMEABLE = "r" * 59


@pytest.mark.parametrize(
    "feature_id",
    ["meeting-notes", "quote-draft", "a", "ab", "report_2024", "api", "admin", LONGEST_NAMEABLE],
)
def test_a_definition_id_carries_into_an_agent_id(feature_id: str) -> None:
    assert feature_agent_id(feature_id) == f"{AGENT_ID_PREFIX}{feature_id}"
    # The prefix is what keeps a feature's agent out of the reserved names.
    assert validate_custom_agent_id(feature_agent_id(feature_id) or "") == (
        f"{AGENT_ID_PREFIX}{feature_id}"
    )


@pytest.mark.parametrize("feature_id", ["trailing_", "trailing-", "r" * 60, "r" * 64])
def test_a_definition_id_that_cannot_name_an_agent_answers_none(feature_id: str) -> None:
    """Both rules accept the id; only the agent rule refuses the prefixed name."""
    assert _ID_RE.fullmatch(feature_id), "the definition id is legal on its own"
    assert feature_agent_id(feature_id) is None
    with pytest.raises(OctopError):
        validate_custom_agent_id(f"{AGENT_ID_PREFIX}{feature_id}")


def test_every_bundled_definition_can_have_an_agent() -> None:
    """The shipped library sits inside the boundary — measure it, do not assume it."""
    bundled = FeatureCatalog().list()

    assert bundled, "the library must ship at least one definition"
    for feature in bundled:
        assert feature_agent_id(feature.id) == f"{AGENT_ID_PREFIX}{feature.id}"


def _server(row: Any) -> Any:
    """A server stub whose registry answers with *row* for any id (``None`` = absent)."""
    registry = SimpleNamespace(get_row=lambda _agent_id: row)
    return SimpleNamespace(app_runtime=SimpleNamespace(agent_registry=registry))


@pytest.mark.parametrize(
    ("owner", "expected"),
    [(None, "feat-meeting-notes"), (7, None)],
    ids=["app-owned", "user-owned"],
)
def test_only_an_app_owned_agent_counts_as_the_features_agent(
    owner: int | None, expected: str | None
) -> None:
    """The predicate is ownership, not the name.

    A user's own agent that happens to carry the derived id — somebody created
    ``feat-meeting-notes`` as a custom id — is not this feature's agent: handing it
    to a run would run the feature on a private agent's configuration.
    """
    feature = FeatureCatalog().get("meeting-notes")
    assert feature is not None
    row = SimpleNamespace(agent_id="feat-meeting-notes", user_id=owner)

    assert materialized_feature_agent_id(_server(row), feature) == expected
    assert materialized_feature_agent_id(_server(None), feature) is None
