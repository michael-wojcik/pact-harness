"""The planned CLI verbs that are not part of a milestone slice of their own."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from pact_harness import cli, paths

FORCE_PUSH = {
    "hook_event_name": "PreToolUse",
    "session_id": "sess-cli",
    "cwd": ".",
    "tool_name": "Bash",
    "tool_input": {"command": "git push --force origin main"},
}


def pin_plugin_root() -> Path:
    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    candidate = paths.resolve_plugin_root(env={}, harness_root=root)
    if candidate is None or not candidate.is_dir():
        pytest.skip("vendored submodule not checked out")
    return candidate


def run_cli(monkeypatch: pytest.MonkeyPatch, capsys, argv, stdin: str = ""):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_hook_runs_one_registration(monkeypatch, capsys) -> None:
    plugin = pin_plugin_root()
    frame = dict(FORCE_PUSH)
    frame["cwd"] = str(plugin.parents[1])
    code, out, err = run_cli(
        monkeypatch, capsys, ["hook", "merge_guard_pre", "--plugin-root", str(plugin)], json.dumps(frame)
    )
    assert code == 0, err
    payload = json.loads(out)
    assert payload["registration"]["hook"] == "merge_guard_pre"
    assert payload["problem"] is None
    assert any(item["kind"] == "deny" for item in payload["directives"]), payload["directives"]


def test_hook_raw_reports_the_process_result(monkeypatch, capsys) -> None:
    plugin = pin_plugin_root()
    frame = dict(FORCE_PUSH)
    frame["cwd"] = str(plugin.parents[1])
    code, out, err = run_cli(
        monkeypatch,
        capsys,
        ["hook", "merge_guard_pre", "--plugin-root", str(plugin), "--raw"],
        json.dumps(frame),
    )
    assert code == 0, err
    payload = json.loads(out)
    assert set(payload) == {"hook", "event", "exit", "duration_ms", "stdout", "stderr"}


def test_hook_rejects_an_unknown_name(monkeypatch, capsys) -> None:
    plugin = pin_plugin_root()
    code, out, err = run_cli(
        monkeypatch, capsys, ["hook", "no_such_gate", "--plugin-root", str(plugin)], json.dumps(FORCE_PUSH)
    )
    assert code != 0
    assert "no registration named" in err


def test_hook_rejects_unreadable_stdin(monkeypatch, capsys) -> None:
    plugin = pin_plugin_root()
    code, out, err = run_cli(
        monkeypatch, capsys, ["hook", "merge_guard_pre", "--plugin-root", str(plugin)], "{not json"
    )
    assert code != 0
    assert "not JSON" in err


def test_install_claude_code_reports_native_mode(monkeypatch, capsys) -> None:
    code, out, err = run_cli(monkeypatch, capsys, ["install", "claude-code"])
    assert code == 0, err
    assert "nothing to install" in out
    assert "34 registrations" in out


def test_bridged_mode_requires_its_own_directory(monkeypatch, capsys) -> None:
    code, out, err = run_cli(monkeypatch, capsys, ["install", "claude-code", "--mode", "bridged"])
    assert code != 0
    assert "pass --target" in err


def test_doctor_reports_modules_and_the_last_activation(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from pact_harness.paths import StateRoot

    state = tmp_path / "state"
    record = state / "pact-harness" / "dsh-activation.json"
    record.parent.mkdir(parents=True)
    record.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "mode": "shadow",
                "activatedAt": "2026-11-24T19:48:36.085Z",
                "enforceGates": ["merge_guard_pre", "git_commit_check"],
            }
        )
    )
    monkeypatch.setattr(
        "pact_harness.cli.resolve_state_root",
        lambda **kwargs: StateRoot(path=state, source="test"),
    )
    code, out, err = run_cli(monkeypatch, capsys, ["doctor", "--adapter", "dsh", "--json"])
    payload = json.loads(out)
    assert payload["modules"] is not None
    assert len(payload["modules"]) == 28
    async_modules = {module["hook"] for module in payload["modules"] if module["async"]}
    assert async_modules == {"file_tracker", "pin_marker_writer", "session_end"}
    assert payload["activation"]["mode"] == "shadow"
    assert payload["activation_error"] is None


def test_migrate_state_moves_a_root(tmp_path: Path, monkeypatch, capsys) -> None:
    source = tmp_path / "old"
    (source / "sessions").mkdir(parents=True)
    (source / "sessions" / "one.json").write_text("{}")
    target = tmp_path / "new"
    code, out, err = run_cli(
        monkeypatch, capsys, ["migrate-state", "--from", str(source), "--to", str(target)]
    )
    assert code == 0, err
    assert (target / "sessions" / "one.json").is_file()
    assert not source.exists()


def test_migrate_state_dry_run_moves_nothing(tmp_path: Path, monkeypatch, capsys) -> None:
    source = tmp_path / "old"
    source.mkdir()
    (source / "file.txt").write_text("x")
    target = tmp_path / "new"
    code, out, err = run_cli(
        monkeypatch,
        capsys,
        ["migrate-state", "--from", str(source), "--to", str(target), "--dry-run"],
    )
    assert code == 0
    assert "would move" in out
    assert (source / "file.txt").is_file()
    assert not target.exists()


def test_migrate_state_refuses_a_partial_move(tmp_path: Path, monkeypatch, capsys) -> None:
    source = tmp_path / "old"
    (source / "sessions").mkdir(parents=True)
    target = tmp_path / "new"
    (target / "sessions").mkdir(parents=True)
    code, out, err = run_cli(
        monkeypatch, capsys, ["migrate-state", "--from", str(source), "--to", str(target)]
    )
    assert code != 0
    assert "refusing a partial move" in err
    assert (source / "sessions").is_dir()


def test_migrate_state_rejects_nested_targets(tmp_path: Path, monkeypatch, capsys) -> None:
    source = tmp_path / "old"
    source.mkdir()
    code, out, err = run_cli(
        monkeypatch, capsys, ["migrate-state", "--from", str(source), "--to", str(source / "inner")]
    )
    assert code != 0
    assert "must not contain" in err
