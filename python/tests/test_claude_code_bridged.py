"""Bridged Claude Code mode: the parity gate and the rewritten tree."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pact_harness import paths
from pact_harness.conformance.parity import compare_decision, run_parity
from pact_harness.install.claude_code import (
    MANIFEST_NAME,
    ClaudeCodeInstallError,
    bridged_command,
    bridged_hooks,
    install_bridged,
    uninstall_bridged,
)


def pin_plugin_root() -> Path:
    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    candidate = paths.resolve_plugin_root(env={}, harness_root=root)
    if candidate is None or not candidate.is_dir():
        pytest.skip("vendored submodule not checked out")
    return candidate


def hooks_document() -> dict:
    reference = "$" + "{CLAUDE_PLUGIN_ROOT}"
    return {
        "description": "test",
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f'python3 "{reference}/hooks/merge_guard_pre.py"',
                            "timeout": 10,
                        },
                        {"type": "command", "command": "bash unsupported.sh"},
                        {
                            "type": "command",
                            "command": f'python3 "{reference}/hooks/pin_marker_writer.py"',
                            "async": True,
                        },
                    ],
                }
            ]
        },
    }


def test_the_shim_command_names_the_adapter() -> None:
    command = bridged_command("hooks/merge_guard_pre.py")
    assert command.startswith("python3 -m pact_harness hook merge_guard_pre ")
    assert "--adapter claude-code" in command
    assert "--passthrough" in command


def test_rewrite_bridges_hook_commands_and_reports_the_rest() -> None:
    rewritten, unsupported = bridged_hooks(hooks_document())
    entries = rewritten["hooks"]["PreToolUse"][0]["hooks"]
    assert entries[0]["command"].startswith("python3 -m pact_harness hook merge_guard_pre ")
    assert entries[0]["timeout"] == 10
    assert entries[2]["async"] is True
    assert entries[1]["command"] == "bash unsupported.sh"
    assert len(unsupported) == 1
    assert rewritten["description"] == "test"


def test_the_real_tree_rewrites_completely() -> None:
    document = json.loads((pin_plugin_root() / "hooks" / "hooks.json").read_text())
    rewritten, unsupported = bridged_hooks(document)
    commands = [
        entry["command"]
        for groups in rewritten["hooks"].values()
        for group in groups
        for entry in group["hooks"]
    ]
    assert len(commands) == 34
    assert unsupported == []
    assert all(command.startswith("python3 -m pact_harness hook ") for command in commands)


def test_parity_holds_on_the_pinned_tree() -> None:
    report = run_parity(pin_plugin_root())
    if not report.ok:
        # pytest abbreviates long assertion values, and the whole point of the
        # failure is the bytes. Print them verbatim so the CI log is readable.
        for failure in report.failures:
            print("PARITY FAILURE:", failure)
    assert report.ok
    assert len(report.cells) == 48
    assert report.frames == 24


def test_compare_reports_a_differing_decision() -> None:
    ok, detail = compare_decision(b"{}", 0, b"{}", 0)
    assert ok and detail == ""
    ok, detail = compare_decision(b"{}", 0, b'{"x": 1}', 0)
    assert not ok and "sha" in detail
    ok, detail = compare_decision(b"{}", 0, b"{}", 2)
    assert not ok and "exit" in detail


def test_install_refuses_a_non_plugin_tree(tmp_path: Path) -> None:
    with pytest.raises(ClaudeCodeInstallError, match="not a PACT plugin tree"):
        install_bridged(tmp_path / "target", tmp_path, verify_parity=False)


def test_install_refuses_a_non_empty_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep.txt").write_text("mine")
    with pytest.raises(ClaudeCodeInstallError, match="not empty"):
        install_bridged(target, pin_plugin_root(), verify_parity=False)


def test_a_bridged_install_is_reversible(tmp_path: Path) -> None:
    target = tmp_path / "bridged"
    result = install_bridged(target, pin_plugin_root(), verify_parity=False)
    assert result.commands == 34
    rewritten = json.loads((target / "hooks" / "hooks.json").read_text())
    commands = [
        entry["command"]
        for groups in rewritten["hooks"].values()
        for group in groups
        for entry in group["hooks"]
    ]
    assert all(command.startswith("python3 -m pact_harness hook ") for command in commands)
    manifest = json.loads((target / MANIFEST_NAME).read_text())
    assert manifest["commands"] == 34
    assert manifest["sourcePlugin"] == str(pin_plugin_root().resolve())
    # The copy is a real copy, not a reference into the pin.
    assert (target / "hooks" / "session_init.py").is_file()

    removed = uninstall_bridged(target)
    assert removed["commands"] == 34
    assert not target.exists()


def test_uninstall_refuses_a_foreign_directory(tmp_path: Path) -> None:
    target = tmp_path / "not-ours"
    target.mkdir()
    with pytest.raises(ClaudeCodeInstallError, match=MANIFEST_NAME):
        uninstall_bridged(target)


def test_the_install_is_gated_by_parity(tmp_path: Path) -> None:
    """The gate is real: install runs parity instead of trusting a document."""
    result = install_bridged(tmp_path / "bridged", pin_plugin_root(), verify_parity=True)
    assert result.parity_cells == 48