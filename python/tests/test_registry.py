"""The hook registry, derived from upstream's own manifest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pact_harness import paths
from pact_harness.runtime.registry import HookRegistry, RegistryError, matcher_matches


def plugin_root() -> Path:
    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    candidate = paths.resolve_plugin_root(env={}, harness_root=root)
    if candidate is None or not candidate.is_dir():
        pytest.skip("vendored submodule not checked out")
    return candidate


@pytest.fixture(scope="module")
def registry() -> HookRegistry:
    return HookRegistry.from_plugin_root(plugin_root())


def test_manifest_shape_is_what_we_measured(registry: HookRegistry) -> None:
    # The manifest registers on 13 event names. PACT's own model has 12 because
    # PostToolUseFailure is folded into PostToolUse there; the manifest keeps
    # them apart, and the registry must report what the manifest actually says.
    assert len(registry) == 34
    assert len(registry.events) == 13
    assert len(registry.modules()) == 28


def test_event_filter_narrows_by_tool(registry: HookRegistry) -> None:
    edit = registry.for_event("PreToolUse", tool_name="Edit")
    bash = registry.for_event("PreToolUse", tool_name="Bash")
    assert {registration.hook for registration in edit} == {
        "bootstrap_gate",
        "worktree_guard",
        "pin_staleness_gate",
        "pin_caps_gate",
        "task_claim_gate",
    }
    assert "git_commit_check" in {registration.hook for registration in bash}
    assert "git_commit_check" not in {registration.hook for registration in edit}


def test_every_registration_resolves_to_a_real_script(registry: HookRegistry) -> None:
    for registration in registry.registrations:
        assert registration.script.is_file(), registration.script


def test_async_flags_match_the_manifest(registry: HookRegistry) -> None:
    # Three modules are registered async: the two writers that must not block a
    # prompt, and the edit tracker. roster_writer is not async, which an earlier
    # count of the manifest got wrong.
    asynchronous = {
        registration.hook for registration in registry.registrations if registration.async_
    }
    assert asynchronous == {"pin_marker_writer", "session_end", "file_tracker"}


def test_timeouts_are_carried(registry: HookRegistry) -> None:
    pin_caps = [
        registration
        for registration in registry.registrations
        if registration.hook == "pin_caps_gate"
    ]
    assert pin_caps and pin_caps[0].timeout_s == 600.0


def test_matcher_absent_admits_everything() -> None:
    assert matcher_matches(None, "Edit")
    assert matcher_matches("", "Edit")


def test_matcher_is_exact_not_a_pattern() -> None:
    assert matcher_matches("Edit|Write", "Edit")
    assert matcher_matches("Edit|Write", "Write")
    assert not matcher_matches("Edit|Write", "Bash")
    assert not matcher_matches("Edit", "EditNotebook")
    assert not matcher_matches("Edit", "edit")


def test_missing_registry_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="hook registry missing"):
        HookRegistry.from_plugin_root(tmp_path)


def test_malformed_registry_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "hooks" / "hooks.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"hooks": {"PreToolUse": [{"hooks": [{"type": "command"}]}]}}))
    with pytest.raises(RegistryError, match="no command string"):
        HookRegistry.from_plugin_root(tmp_path)
