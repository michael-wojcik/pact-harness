"""The memory CLI's venv: created once, reused after that."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from pact_harness.install.venv import MARKER_NAME, VenvError, ensure_venv, venv_root


def test_the_venv_lives_inside_the_state_home() -> None:
    assert venv_root(state_root=Path("/tmp/home/.pact")) == Path("/tmp/home/.pact/venv")
    assert venv_root(home=Path("/tmp/user")) == Path("/tmp/user/.pact/venv")


def test_a_venv_is_created_and_then_reused(tmp_path: Path) -> None:
    target = venv_root(tmp_path / ".pact")
    first = ensure_venv(target)
    assert first.created is True
    assert (target / MARKER_NAME).is_file()
    assert (target / "bin" / "python").exists()
    marker = json.loads((target / MARKER_NAME).read_text())
    assert marker["path"] == str(target)

    second = ensure_venv(target)
    assert second.created is False
    assert "left in place" in " ".join(second.notes)


def test_a_failed_creation_is_an_error(tmp_path: Path) -> None:
    class Failing:
        returncode = 1
        stdout = ""
        stderr = "no venv for you"

    with pytest.raises(VenvError, match="no venv for you"):
        ensure_venv(tmp_path / "venv", runner=lambda *args, **kwargs: Failing())


def test_the_cli_creates_the_venv(tmp_path: Path, monkeypatch, capsys) -> None:
    from pact_harness import cli
    from pact_harness.paths import StateRoot

    state = tmp_path / ".pact"
    state.mkdir()
    monkeypatch.setattr(
        "pact_harness.cli.resolve_state_root",
        lambda **kwargs: StateRoot(path=state, source="test"),
    )
    code = cli.main(["install", "claude-code", "--venv"])
    assert code == 0
    out = capsys.readouterr().out
    assert "venv created" in out
    assert (state / "venv" / MARKER_NAME).is_file()
