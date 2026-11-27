"""The playbook compiler: parsing, rewrites, counts, and determinism."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pact_harness import paths
from pact_harness.playbook.compile import (
    EXCLUDED_COMMANDS,
    compile_playbook,
    rewrite_text,
    split_frontmatter,
)


def plugin_root() -> Path:
    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    candidate = paths.resolve_plugin_root(env={}, harness_root=root)
    if candidate is None or not candidate.is_dir():
        pytest.skip("vendored submodule not checked out")
    return candidate


@pytest.fixture(scope="module")
def playbook(pin):
    return compile_playbook(plugin_root(), pact_version=pin.pact_version, commit=pin.commit)


def test_frontmatter_scalars_and_quotes() -> None:
    front, body = split_frontmatter('---\nname: x\ncolor: "#FFD700"\n---\nthe body\n')
    assert front == {"name": "x", "color": "#FFD700"}
    assert body == "the body\n"


def test_frontmatter_block_scalar() -> None:
    front, _ = split_frontmatter("---\ndescription: |\n  one\n  two\n---\nbody")
    assert front["description"] == "one\ntwo"


def test_frontmatter_list_and_map() -> None:
    front, _ = split_frontmatter("---\nskills:\n  - a\n  - b\nmeta:\n  k: v\n---\nx")
    assert front["skills"] == ["a", "b"]
    assert front["meta"] == {"k": "v"}


def test_no_frontmatter_is_returned_whole() -> None:
    front, body = split_frontmatter("# just a heading\n")
    assert front == {}
    assert body == "# just a heading\n"


def test_rewrites_arguments_and_paths(tmp_path: Path) -> None:
    text = 'Run $ARGUMENTS. Read(file_path="../protocols/pact-variety.md").${CLAUDE_PLUGIN_ROOT}/x'
    rewritten, stats = rewrite_text(text, tmp_path)
    assert "{arguments}" in rewritten
    assert "$ARGUMENTS" not in rewritten
    assert f"{tmp_path}/protocols/pact-variety.md" in rewritten
    assert "../protocols" not in rewritten
    assert f"{tmp_path}/x" in rewritten
    assert stats["arguments"] == 1
    assert stats["relative_refs"] == 1
    assert stats["plugin_root_tokens"] == 1


def test_rewrite_counts_toolsearch(tmp_path: Path) -> None:
    _, stats = rewrite_text("run ToolSearch twice: ToolSearch", tmp_path)
    assert stats["toolsearch"] == 2


def test_the_compiled_counts(playbook) -> None:
    assert len(playbook.personas) == 13
    assert len(playbook.commands) == 13
    assert len(playbook.skills) == 21
    assert len(playbook.protocols) == 22
    assert len(playbook.references) == 3


def test_telegram_is_excluded(playbook) -> None:
    sources = {command.source for command in playbook.commands}
    assert not any("telegram-setup" in source for source in sources)
    assert any("telegram" in warning for warning in playbook.warnings)
    assert EXCLUDED_COMMANDS == {"telegram-setup.md"}


def test_command_names_are_platform_safe(playbook) -> None:
    import re

    names = [command.name for command in playbook.commands]
    assert "pact-orchestrate" in names
    assert "pact-compact" in names
    assert "pact-repact" in names
    assert "pact-impact" in names
    for name in names:
        assert re.match(r"^[a-z][a-z0-9_-]*$", name), name


def test_persona_references_are_absolute(playbook) -> None:
    orchestrator = next(p for p in playbook.personas if p.name == "pact-orchestrator")
    assert "../protocols" not in orchestrator.body
    assert f"{playbook.plugin_root}/protocols/" in orchestrator.body


def test_command_bodies_use_the_platform_placeholder(playbook) -> None:
    orchestrate = next(c for c in playbook.commands if c.name == "pact-orchestrate")
    assert "{arguments}" in orchestrate.body
    assert "$ARGUMENTS" not in orchestrate.body


def test_compilation_is_deterministic(pin) -> None:
    first = compile_playbook(plugin_root(), pact_version=pin.pact_version, commit=pin.commit)
    second = compile_playbook(plugin_root(), pact_version=pin.pact_version, commit=pin.commit)
    assert first.to_dict() == second.to_dict()


def test_platform_notes_explain_the_substitutions(playbook) -> None:
    assert "ToolSearch does not exist" in playbook.platform_notes
    assert "{arguments}" in playbook.platform_notes


def test_the_document_serialises(playbook, pin) -> None:
    payload = playbook.to_dict()
    assert payload["v"] == 1
    assert payload["platform"] == "dsh"
    assert payload["pact"] == {"version": pin.pact_version, "commit": pin.commit}
    assert payload["counts"]["skills"] == 21
    json.dumps(payload)
