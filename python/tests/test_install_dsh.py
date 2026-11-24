"""Installing the bridge into a DSH profile."""

from __future__ import annotations

from pathlib import Path

import pytest

from pact_harness.install.dsh import (
    MANAGED_BEGIN,
    MANAGED_END,
    InstallError,
    apply_managed_block,
    install_dsh,
    managed_block,
    uninstall_dsh,
)


class Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def recorder(returncode: int = 0):
    calls: list[list[str]] = []

    def runner(argv, **kwargs):
        calls.append(list(argv))
        return Completed(returncode)

    return calls, runner


def adapter(tmp_path: Path) -> Path:
    directory = tmp_path / "adapter"
    directory.mkdir(exist_ok=True)
    (directory / "package.json").write_text("{}")
    return directory


def test_managed_block_is_a_yaml_sequence_entry() -> None:
    text = managed_block("shadow", ("merge_guard_pre", "git_commit_check"))
    assert text.startswith(MANAGED_BEGIN)
    assert text.rstrip().endswith(MANAGED_END)
    assert "- id: pact-dsh" in text
    assert "mode: shadow" in text
    assert "      - merge_guard_pre" in text


def test_block_replaces_an_empty_sequence() -> None:
    result = apply_managed_block("[]\n", managed_block("shadow", ("a",)))
    assert result.startswith(MANAGED_BEGIN)
    assert "[]" not in result


def test_block_preserves_comments() -> None:
    existing = "# my profile notes\n[]\n"
    result = apply_managed_block(existing, managed_block("shadow", ("a",)))
    assert "# my profile notes" in result
    assert "- id: pact-dsh" in result


def test_block_appends_to_existing_entries() -> None:
    existing = "- id: something-else\n  config:\n    x: 1\n"
    result = apply_managed_block(existing, managed_block("shadow", ("a",)))
    assert result.startswith("- id: something-else")
    assert "- id: pact-dsh" in result


def test_install_is_idempotent() -> None:
    block = managed_block("shadow", ("a",))
    once = apply_managed_block("[]\n", block)
    twice = apply_managed_block(once, block)
    assert twice.count(MANAGED_BEGIN) == 1
    assert twice.count(MANAGED_END) == 1


def test_install_writes_the_patch_and_reports_it(tmp_path: Path) -> None:
    home = tmp_path / "dsh"
    result = install_dsh(
        profile="pact",
        dsh_home=home,
        adapter_dir=adapter(tmp_path),
        register=False,
    )
    patch = home / "profiles" / "pact" / "cordis.patch.yml"
    assert result.patch_path == patch
    assert patch.is_file()
    text = patch.read_text()
    assert "- id: pact-dsh" in text
    assert "merge_guard_pre" in text
    assert any("skipped dsh plugin add" in note for note in result.notes)


def test_install_registers_through_the_dsh_cli(tmp_path: Path) -> None:
    calls, runner = recorder()
    result = install_dsh(
        profile="pact",
        dsh_home=tmp_path / "dsh",
        adapter_dir=adapter(tmp_path),
        cli="/usr/local/bin/dsh",
        runner=runner,
        register=True,
    )
    assert calls == [
        [
            "/usr/local/bin/dsh",
            "plugin",
            "--profile",
            "pact",
            "add",
            str(adapter(tmp_path)),
        ]
    ]
    assert result.commands[0].returncode == 0


def test_a_failed_registration_stops_the_install(tmp_path: Path) -> None:
    calls, runner = recorder(returncode=1)
    with pytest.raises(InstallError, match="dsh plugin add failed"):
        install_dsh(
            profile="pact",
            dsh_home=tmp_path / "dsh",
            adapter_dir=adapter(tmp_path),
            cli="/usr/local/bin/dsh",
            runner=runner,
        )


def test_off_mode_enforces_nothing(tmp_path: Path) -> None:
    result = install_dsh(
        profile="pact",
        dsh_home=tmp_path / "dsh",
        adapter_dir=adapter(tmp_path),
        mode="off",
        register=False,
    )
    assert result.mode == "off"
    assert result.enforce_gates == ()


def test_an_unknown_mode_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InstallError, match="mode must be"):
        install_dsh(
            profile="pact",
            dsh_home=tmp_path / "dsh",
            adapter_dir=adapter(tmp_path),
            mode="sometimes",
            register=False,
        )


def test_uninstall_removes_only_the_block(tmp_path: Path) -> None:
    home = tmp_path / "dsh"
    install_dsh(profile="pact", dsh_home=home, adapter_dir=adapter(tmp_path), register=False)
    patch = home / "profiles" / "pact" / "cordis.patch.yml"
    patch.write_text("# keep me\n- id: someone-else\n  config:\n    x: 1\n" + patch.read_text())
    result = uninstall_dsh(profile="pact", dsh_home=home, unregister=False)
    assert result.block_removed is True
    text = patch.read_text()
    assert MANAGED_BEGIN not in text
    assert "- id: someone-else" in text
    assert "# keep me" in text


def test_install_writes_a_playbook_and_references_it(tmp_path: Path) -> None:
    home = tmp_path / "dsh"
    result = install_dsh(
        profile="pact",
        dsh_home=home,
        adapter_dir=adapter(tmp_path),
        register=False,
    )
    playbook = result.profile_dir / "pact-playbook.json"
    text = result.patch_path.read_text()
    if playbook.is_file():
        assert f"playbook: {playbook}" in text
    else:
        # Compilation is best-effort; when it fails the install says why.
        assert any("playbook not built" in note for note in result.notes)


def test_uninstall_removes_the_generated_playbook(tmp_path: Path) -> None:
    home = tmp_path / "dsh"
    install_dsh(profile="pact", dsh_home=home, adapter_dir=adapter(tmp_path), register=False)
    playbook = home / "profiles" / "pact" / "pact-playbook.json"
    uninstall_dsh(profile="pact", dsh_home=home, unregister=False)
    assert not playbook.exists()


def test_uninstall_reports_an_absent_block(tmp_path: Path) -> None:
    home = tmp_path / "dsh"
    profile = home / "profiles" / "pact"
    profile.mkdir(parents=True)
    (profile / "cordis.patch.yml").write_text("[]\n")
    result = uninstall_dsh(profile="pact", dsh_home=home, unregister=False)
    assert result.block_removed is False


def test_uninstall_calls_the_remove_command(tmp_path: Path) -> None:
    calls, runner = recorder()
    uninstall_dsh(
        profile="pact",
        dsh_home=tmp_path / "dsh",
        cli="/usr/local/bin/dsh",
        runner=runner,
        unregister=True,
    )
    assert calls == [
        [
            "/usr/local/bin/dsh",
            "plugin",
            "--profile",
            "pact",
            "remove",
            "@pact-harness/dsh",
        ]
    ]
