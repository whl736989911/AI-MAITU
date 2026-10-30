"""Encoding and leading-comment compatibility for skill metadata."""

from __future__ import annotations

import pytest

from octop.infra.utils.frontmatter import is_agent_file, parse_frontmatter

_MANIFEST = "---\nname: sample-skill\ndescription: Example metadata\n---\nSkill body\n"


def test_utf8_bom_preserves_skill_metadata_and_body() -> None:
    text = "\ufeff" + _MANIFEST
    metadata, body = parse_frontmatter(text)
    assert metadata == {"name": "sample-skill", "description": "Example metadata"}
    assert body == "Skill body\n"
    assert is_agent_file(text)


@pytest.mark.parametrize("comment", ["<!-- generated -->\n", "<!-- generated\ncomment -->\n"])
def test_leading_html_comment_preserves_skill_metadata(comment: str) -> None:
    metadata, body = parse_frontmatter(comment + _MANIFEST)
    assert metadata["name"] == "sample-skill"
    assert body == "Skill body\n"


def test_unclosed_comment_does_not_expose_a_hidden_manifest() -> None:
    text = "<!-- hidden\n" + _MANIFEST
    assert parse_frontmatter(text) == ({}, text)
    assert not is_agent_file(text)
