"""The upstream surface: extraction, classification, and verify judgement."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pact_harness import paths
from pact_harness.upstream.inspect import Artifact, EnvRead, Surface, inspect_surface
from pact_harness.upstream.verify import verify_surface


def plugin_root() -> Path:
    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    candidate = paths.resolve_plugin_root(env={}, harness_root=root)
    if candidate is None or not candidate.is_dir():
        pytest.skip("vendored submodule not checked out")
    return candidate


def pin_values() -> tuple[str, str, str]:
    from pact_harness.vendor import load_pin

    root = paths.find_harness_root()
    if root is None:
        return "", "", ""
    pin = load_pin(root)
    return pin.pact_version, pin.commit, pin.tag or ""


@pytest.fixture(scope="module")
def surface() -> Surface:
    version, commit, tag = pin_values()
    return inspect_surface(plugin_root(), pact_version=version, commit=commit, tag=tag)


def minimal_surface(**overrides) -> Surface:
    base = dict(
        plugin_root="/tmp/plugin",
        registrations=(
            {"event": "PreToolUse", "hook": "h", "matcher": "Edit", "script": "hooks/h.py"},
        ),
        matchers=("Edit",),
        tool_sets={"_BLOCKED_TOOLS": ("Edit", "Write")},
        env_reads=(EnvRead(name="CLAUDE_CONFIG_DIR", file="hooks/h.py", line=1),),
        artifacts=(
            Artifact(kind="agents", path="agents/a.md", sha256="0" * 64, bytes=1),
            Artifact(kind="commands", path="commands/c.md", sha256="0" * 64, bytes=1),
            Artifact(kind="skills", path="skills/s/SKILL.md", sha256="0" * 64, bytes=1),
            Artifact(kind="protocols", path="protocols/p.md", sha256="0" * 64, bytes=1),
        ),
        decision_tokens={"permissionDecision": 1, "hookSpecificOutput": 1},
    )
    base.update(overrides)
    return Surface(**base)


def test_the_registry_is_extracted(surface: Surface) -> None:
    assert len(surface.registrations) == 34
    assert len({item["event"] for item in surface.registrations}) == 13
    assert len({item["hook"] for item in surface.registrations}) == 28


def test_the_matcher_vocabulary_is_extracted(surface: Surface) -> None:
    assert "Edit|Write" in surface.matchers
    assert "Bash" in surface.matchers
    assert "Agent" in surface.matchers


def test_the_gate_tool_sets_are_extracted(surface: Surface) -> None:
    assert set(surface.tool_sets) == {"_BLOCKED_TOOLS", "_READ_ONLY_TOOLS", "_DEGRADED_ASK_TOOLS"}
    assert "Edit" in surface.tool_sets["_BLOCKED_TOOLS"]
    assert "Bash" in surface.tool_sets["_READ_ONLY_TOOLS"] or "Read" in surface.tool_sets["_READ_ONLY_TOOLS"]
    assert set(surface.tool_sets["_DEGRADED_ASK_TOOLS"]) == {"WebFetch", "WebSearch"}


def test_env_reads_include_helper_indirected_names(surface: Surface) -> None:
    names = {read.name for read in surface.env_reads}
    assert "CLAUDE_CONFIG_DIR" in names
    assert "CLAUDE_PROJECT_DIR" in names
    # Read through a module constant, which a naive scan misses.
    assert "CLAUDE_CODE_REMOTE" in names


def test_the_artifact_inventory_is_hashed(surface: Surface) -> None:
    counts: dict[str, int] = {}
    for artifact in surface.artifacts:
        counts[artifact.kind] = counts.get(artifact.kind, 0) + 1
    assert counts == {"agents": 13, "commands": 14, "protocols": 22, "reference": 3, "skills": 21}
    assert all(len(artifact.sha256) == 64 for artifact in surface.artifacts)


def test_decision_tokens_are_counted(surface: Surface) -> None:
    for token in ("permissionDecision", "additionalContext", "systemMessage", "hookSpecificOutput"):
        assert surface.decision_tokens[token] > 0, token


def test_extraction_is_deterministic(surface: Surface) -> None:
    version, commit, tag = pin_values()
    again = inspect_surface(plugin_root(), pact_version=version, commit=commit, tag=tag)
    assert surface.to_dict() == again.to_dict()


def test_a_healthy_tree_verifies(surface: Surface) -> None:
    report = verify_surface(surface)
    assert report.ok, report.errors
    assert report.facts["registrations"] == 34
    assert report.facts["env_unclassified"] == []


def test_a_matcher_outside_the_vocabulary_fails() -> None:
    report = verify_surface(minimal_surface(matchers=("Edit|Wibble",)))
    assert not report.ok
    assert any("Wibble" in error for error in report.errors)


def test_a_tool_set_name_outside_the_vocabulary_fails() -> None:
    report = verify_surface(minimal_surface(tool_sets={"_BLOCKED_TOOLS": ("Edit", "Frobnicate")}))
    assert not report.ok
    assert any("Frobnicate" in error for error in report.errors)


def test_an_unclassified_env_read_fails() -> None:
    report = verify_surface(
        minimal_surface(env_reads=(EnvRead(name="SOME_NEW_VAR", file="hooks/h.py", line=3),))
    )
    assert not report.ok
    assert any("SOME_NEW_VAR" in error for error in report.errors)


def test_an_unclassified_env_read_can_be_downgraded() -> None:
    report = verify_surface(
        minimal_surface(env_reads=(EnvRead(name="SOME_NEW_VAR", file="hooks/h.py", line=3),)),
        allow_unknown_env=True,
    )
    assert report.ok
    assert any("SOME_NEW_VAR" in warning for warning in report.warnings)


def test_missing_artifacts_fail() -> None:
    report = verify_surface(minimal_surface(artifacts=()))
    assert not report.ok
    assert any("no agents artifacts" in error for error in report.errors)


def test_an_empty_registry_fails() -> None:
    report = verify_surface(minimal_surface(registrations=(), matchers=()))
    assert not report.ok
    assert any("registry is empty" in error for error in report.errors)


def test_the_report_serialises(surface: Surface) -> None:
    payload = verify_surface(surface).to_dict()
    assert payload["ok"] is True
    assert payload["facts"]["modules"] == 28
    json.dumps(payload)


def test_the_committed_manifest_matches_the_vendored_tree(surface: Surface) -> None:
    """The committed baseline must be regenerated when the vendored tree moves.

    Without this, a pin bump could land with a stale manifest and the first
    person to run a diff would be comparing against a tree that no longer
    exists.
    """
    root = paths.find_harness_root()
    assert root is not None
    committed = json.loads((root / "harness" / "upstream-surface.v1.json").read_text())
    fresh = surface.to_dict()
    for key in ("registry", "matchers", "tool_sets", "env_reads", "artifacts", "decision_tokens", "pact"):
        assert committed[key] == fresh[key], key


def test_the_cli_inspects_and_verifies(tmp_path: Path) -> None:
    from pact_harness import cli

    out = tmp_path / "surface.json"
    assert cli.main(["upstream", "inspect", "--out", str(out)]) == 0
    document = json.loads(out.read_text())
    assert document["registry"]["registrations"] == 34
    assert cli.main(["upstream", "verify", "--json"]) == 0
