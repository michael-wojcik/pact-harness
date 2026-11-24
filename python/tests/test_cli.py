"""CLI behaviour: dispatch, honest reporting, and exit codes."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pact_harness import cli, paths
from pact_harness.contract import ActorRef, EventV1, SessionRef, ToolRef


def test_bare_invocation_is_a_passthrough() -> None:
    assert cli._should_passthrough([])


def test_flag_only_invocation_is_a_passthrough() -> None:
    assert cli._should_passthrough(["--resume", "abc"])
    assert cli._should_passthrough(["--model", "x"])


def test_help_and_version_stay_with_the_harness() -> None:
    assert not cli._should_passthrough(["--help"])
    assert not cli._should_passthrough(["-h"])
    assert not cli._should_passthrough(["--version"])
    assert not cli._should_passthrough(["-V"])


def test_subcommands_are_not_passthrough() -> None:
    assert not cli._should_passthrough(["doctor"])


def test_passthrough_without_claude_reports_clearly(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli._launch_claude([], env={}, which=lambda name: None)
    assert code == 127
    captured = capsys.readouterr()
    assert "no claude executable on PATH" in captured.err


def test_version_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["version", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["harness"] == "0.1.0"
    assert payload["contract"] == 1
    assert "pact" in payload


def test_undetected_adapter_claims_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["capabilities", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    if payload["implemented"]:
        pytest.skip("the detected adapter is implemented in this build")
    assert payload["tier"] == "T0"
    assert payload["capabilities"] == []


def test_claude_code_declares_the_reference_tier(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["capabilities", "--adapter", "claude-code", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["implemented"] is True
    assert payload["tier"] == "T3"
    assert "control.tool.veto" in payload["capabilities"]


def test_doctor_reports_the_live_hook_registry(capsys: pytest.CaptureFixture[str]) -> None:
    if paths.find_harness_root() is None:
        pytest.skip("not running inside the harness checkout")
    cli.main(["doctor", "--adapter", "claude-code", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["registry"] == {"registrations": 34, "events": 13, "modules": 28}
    assert payload["adapter_implemented"] is True
    assert payload["tier"] == "T3"


def test_unimplemented_adapter_is_a_note_not_a_claim(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["doctor", "--adapter", "cursor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["adapter_implemented"] is False
    assert payload["tier"] == "T0"
    assert any("not implemented" in note for note in payload["notes"])
    assert code == (1 if payload["errors"] else 0)


def test_dsh_declares_the_full_tier(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["capabilities", "--adapter", "dsh", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["implemented"] is True
    assert payload["tier"] == "T3"


def test_paths_json_reports_separate_memory(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["paths", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state_root"].endswith(".pact") or "CLAUDE_CONFIG_DIR" in payload["state_root_source"]
    assert payload["memory_dir"].startswith(str(paths.Path.home()))
    assert "pact-memory" in payload["memory_dir"]


def test_doctor_exit_code_matches_its_own_errors(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["doctor", "--adapter", "cursor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["tier"] == "T0"
    assert payload["contract"] == 1
    assert code == (1 if payload["errors"] else 0)


def test_doctor_fails_a_tier_requirement_it_cannot_meet(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["doctor", "--adapter", "cursor", "--require-tier", "T2", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert any("below the required T2" in item for item in payload["errors"])


def test_a_shipped_adapter_can_meet_its_tier_requirement(capsys: pytest.CaptureFixture[str]) -> None:
    if paths.find_harness_root() is None:
        pytest.skip("not running inside the harness checkout")
    code = cli.main(["doctor", "--adapter", "dsh", "--require-tier", "T3", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["tier"] == "T3"
    assert code == (1 if payload["errors"] else 0)


def test_doctor_rejects_an_unknown_tier(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["doctor", "--require-tier", "T9", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert any("--require-tier must be one of" in item for item in payload["errors"])


def test_doctor_reports_the_vendor_pin(capsys: pytest.CaptureFixture[str]) -> None:
    if paths.find_harness_root() is None:
        pytest.skip("not running inside the harness checkout")
    cli.main(["doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["pin"]["pact_version"] == "4.8.36"
    assert payload["vendor"] is not None
    assert payload["vendor"]["at_pin"] is True


def test_unknown_subcommand_exits_two() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["nonsense"])
    assert excinfo.value.code == 2


def test_contract_schemas_are_present_and_identified() -> None:
    assert cli._schema_problems() == []


def dsh_event(tmp_path: Path) -> EventV1:
    return EventV1(
        id="evt_cli_1",
        kind="tool.pre",
        at="2026-11-24T12:00:00Z",
        adapter="dsh",
        session=SessionRef(id="sess-cli", cwd=str(tmp_path), project_dir=str(tmp_path)),
        actor=ActorRef(role="lead", name="lead", type="pact-orchestrator"),
        tool=ToolRef(canonical="Edit", native="edit", input={"file_path": str(tmp_path / "a.py")}),
    )


def test_event_command_prints_an_envelope(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if paths.resolve_plugin_root(env={}) is None:
        pytest.skip("vendored submodule not checked out")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(dsh_event(tmp_path).to_dict())))
    code = cli.main(
        [
            "event",
            "--adapter",
            "dsh",
            "--state-root",
            str(tmp_path / "state"),
            "--python",
            sys.executable,
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["v"] == 1
    assert payload["meta"]["adapter"] == "dsh"
    assert payload["meta"]["event"] == "tool.pre"
    assert payload["directives"]


def test_event_command_rejects_non_json(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("{oops"))
    assert cli.main(["event", "--adapter", "dsh"]) == 1
    assert "not JSON" in capsys.readouterr().err


def test_event_command_works_as_a_process(tmp_path: Path) -> None:
    if paths.resolve_plugin_root(env={}) is None:
        pytest.skip("vendored submodule not checked out")
    env = dict(os.environ)
    src = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pact_harness",
            "event",
            "--adapter",
            "dsh",
            "--state-root",
            str(tmp_path / "state"),
            "--python",
            sys.executable,
        ],
        input=json.dumps(dsh_event(tmp_path).to_dict()).encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    payload = json.loads(completed.stdout)
    assert payload["directives"]
    assert payload["meta"]["adapter"] == "dsh"
