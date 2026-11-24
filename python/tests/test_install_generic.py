"""The advisory installer: a managed section, symlinked skills, and honest removal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pact_harness import paths
from pact_harness.install.generic import (
    MD_BEGIN,
    MD_END,
    GenericInstallError,
    install_generic,
    render_section,
    uninstall_generic,
    vendored_skills,
)


def plugin_root() -> Path:
    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    candidate = paths.resolve_plugin_root(env={}, harness_root=root)
    if candidate is None or not candidate.is_dir():
        pytest.skip("vendored submodule not checked out")
    return candidate


def fake_plugin(tmp_path: Path) -> Path:
    root = tmp_path / "plugin"
    for name in ("pact-alpha", "pact-beta"):
        directory = root / "skills" / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(f"# {name}\n")
    return root


def test_the_section_names_the_tools_and_says_it_is_advisory() -> None:
    text = render_section(["pact-alpha"])
    assert text.startswith(MD_BEGIN)
    assert text.rstrip().endswith(MD_END)
    assert "pact_gate" in text
    assert "Honour a DENY yourself" in text
    assert "pact-alpha" in text
    assert chr(96) * 3 + "json" in text


def test_install_appends_the_block_and_links_the_skills(tmp_path: Path) -> None:
    root = fake_plugin(tmp_path)
    target = tmp_path / "project"
    target.mkdir()
    (target / "AGENTS.md").write_text("Existing instructions.\n")
    result = install_generic(target, root, skills_root=tmp_path / "skills")
    assert result.linked == ("pact-alpha", "pact-beta")
    content = (target / "AGENTS.md").read_text()
    assert content.startswith("Existing instructions.\n")
    assert MD_BEGIN in content
    link = tmp_path / "skills" / "pact-alpha"
    assert link.is_symlink()
    assert link.resolve() == (root / "skills" / "pact-alpha").resolve()


def test_install_is_idempotent(tmp_path: Path) -> None:
    root = fake_plugin(tmp_path)
    target = tmp_path / "project"
    skills = tmp_path / "skills"
    install_generic(target, root, skills_root=skills)
    install_generic(target, root, skills_root=skills)
    content = (target / "AGENTS.md").read_text()
    assert content.count(MD_BEGIN) == 1
    assert content.count(MD_END) == 1
    assert len(list(skills.iterdir())) == 2


def test_uninstall_restores_the_original_file_and_removes_only_our_links(tmp_path: Path) -> None:
    root = fake_plugin(tmp_path)
    target = tmp_path / "project"
    target.mkdir()
    (target / "AGENTS.md").write_text("Existing instructions.\n")
    skills = tmp_path / "skills"
    install_generic(target, root, skills_root=skills)
    user_dir = skills / "pact-user-owned"
    user_dir.mkdir()
    result = uninstall_generic(target, root, skills_root=skills)
    assert (target / "AGENTS.md").read_text() == "Existing instructions.\n"
    assert not (skills / "pact-alpha").exists()
    assert user_dir.is_dir()
    assert set(result.linked) == {"pact-alpha", "pact-beta"}


def test_a_preexisting_directory_is_left_alone(tmp_path: Path) -> None:
    root = fake_plugin(tmp_path)
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "pact-alpha").mkdir()
    result = install_generic(tmp_path / "project", root, skills_root=skills)
    assert result.skipped == ("pact-alpha",)
    assert (skills / "pact-alpha").is_dir()


def test_a_plugin_without_skills_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(GenericInstallError, match="no skills"):
        install_generic(tmp_path / "project", tmp_path / "empty")


def test_no_skills_writes_only_the_section(tmp_path: Path) -> None:
    root = fake_plugin(tmp_path)
    result = install_generic(tmp_path / "project", root, skills_root=tmp_path / "s", link_skills=False)
    assert result.linked == ()
    assert not (tmp_path / "s").exists()


def test_the_real_tree_links_all_21_skills(tmp_path: Path) -> None:
    root = plugin_root()
    assert len(vendored_skills(root)) == 21
    result = install_generic(tmp_path / "project", root, skills_root=tmp_path / "skills")
    assert len(result.linked) == 21


def test_the_cli_installs_and_uninstalls(tmp_path: Path, capsys) -> None:
    from pact_harness import cli

    target = tmp_path / "project"
    skills = tmp_path / "skills"
    code = cli.main(
        ["install", "generic", "--target", str(target), "--skills-root", str(skills), "--json"]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["linked"]) == 21
    code = cli.main(
        ["uninstall", "generic", "--target", str(target), "--skills-root", str(skills), "--json"]
    )
    assert code == 0
    assert (target / "AGENTS.md").read_text().strip() == ""
