"""CLI behaviour: dispatch, honest reporting, and exit codes."""

from __future__ import annotations

import json

import pytest

from pact_harness import cli, paths


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
    code = cli.main(["doctor", "--adapter", "dsh", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["adapter_implemented"] is False
    assert payload["tier"] == "T0"
    assert any("not implemented" in note for note in payload["notes"])
    assert code == (1 if payload["errors"] else 0)


def test_paths_json_reports_separate_memory(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["paths", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state_root"].endswith(".pact") or "CLAUDE_CONFIG_DIR" in payload["state_root_source"]
    assert payload["memory_dir"].startswith(str(paths.Path.home()))
    assert "pact-memory" in payload["memory_dir"]


def test_doctor_exit_code_matches_its_own_errors(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["tier"] == "T0"
    assert payload["contract"] == 1
    assert code == (1 if payload["errors"] else 0)


def test_doctor_fails_a_tier_requirement_it_cannot_meet(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["doctor", "--require-tier", "T2", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert any("below the required T2" in item for item in payload["errors"])


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
