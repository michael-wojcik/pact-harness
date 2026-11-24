"""The development link: precedence, degradation, and honest reporting."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from pact_harness import paths
from pact_harness.dev import DevLinkError, git_dirty, link, status, tree_version, unlink


def plugin_root() -> Path:
    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    candidate = paths.resolve_plugin_root(env={}, harness_root=root)
    if candidate is None or not candidate.is_dir():
        pytest.skip("vendored submodule not checked out")
    return candidate


def checkout(tmp_path: Path) -> Path:
    """A minimal tree that looks like a PACT plugin."""
    root = tmp_path / "checkout"
    (root / "hooks").mkdir(parents=True)
    (root / "hooks" / "hooks.json").write_text("{}")
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "plugin.json").write_text('{"version": "9.9.9"}')
    return root


@pytest.fixture()
def link_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "dev-link.json"
    monkeypatch.setenv("PACT_DEV_LINK", str(target))
    monkeypatch.delenv("PACT_PLUGIN_ROOT", raising=False)
    return target


def test_link_records_the_tree_and_changes_resolution(tmp_path: Path, link_file: Path) -> None:
    root = checkout(tmp_path)
    result = link(root)
    assert result.plugin_root == root.resolve()
    assert result.version == "9.9.9"
    assert link_file.is_file()
    assert paths.plugin_source() == "dev-link"
    assert paths.resolve_plugin_root() == root.resolve()


def test_the_environment_still_wins_over_a_link(tmp_path: Path, link_file: Path, monkeypatch) -> None:
    link(checkout(tmp_path))
    other = tmp_path / "other"
    (other / "hooks").mkdir(parents=True)
    (other / "hooks" / "hooks.json").write_text("{}")
    monkeypatch.setenv("PACT_PLUGIN_ROOT", str(other))
    assert paths.plugin_source() == "env"
    assert paths.resolve_plugin_root() == other.resolve()


def test_unlink_restores_the_pin(tmp_path: Path, link_file: Path) -> None:
    link(checkout(tmp_path))
    removed = unlink()
    assert removed is not None
    assert not link_file.exists()
    assert paths.plugin_source() in {"vendored", "env"}
    assert paths.resolve_plugin_root() == plugin_root().resolve()


def test_unlink_without_a_link_is_not_an_error(link_file: Path) -> None:
    assert unlink() is None


def test_a_tree_that_is_not_a_plugin_is_rejected(tmp_path: Path, link_file: Path) -> None:
    with pytest.raises(DevLinkError, match="not a PACT plugin tree"):
        link(tmp_path)


def test_a_deleted_tree_degrades_to_the_pin(tmp_path: Path, link_file: Path) -> None:
    root = checkout(tmp_path)
    link(root)
    import shutil

    shutil.rmtree(root)
    assert paths.plugin_source() == "vendored"
    assert paths.resolve_plugin_root() == plugin_root().resolve()
    assert status()["present"] is False


def test_dirty_checkout_detection(tmp_path: Path) -> None:
    root = checkout(tmp_path)
    assert git_dirty(root) is None  # not a repository
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "init"],
        check=True,
    )
    assert git_dirty(root) is False
    (root / "hooks" / "hooks.json").write_text('{"changed": true}')
    assert git_dirty(root) is True
    assert tree_version(root) == "9.9.9"


def test_doctor_reports_a_dirty_linked_tree(
    tmp_path: Path, link_file: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pact_harness import cli

    root = checkout(tmp_path)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "init"],
        check=True,
    )
    (root / "hooks" / "hooks.json").write_text('{"changed": true}')
    link(root)
    monkeypatch.setenv("PACT_DEV_LINK", str(link_file))
    assert cli.main(["doctor", "--adapter", "dsh", "--json"]) in (0, 1)
    payload = json.loads(capsys.readouterr().out)
    assert payload["plugin_source"] == "dev-link"
    assert payload["plugin_dirty"] is True
    assert any("uncommitted" in warning for warning in payload["warnings"])


def test_the_dev_cli_links_and_unlinks(tmp_path: Path, link_file: Path, capsys) -> None:
    from pact_harness import cli

    root = checkout(tmp_path)
    assert cli.main(["dev", "link", "--plugin", str(root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] == "9.9.9"
    assert cli.main(["dev", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["present"] is True
    assert cli.main(["dev", "unlink"]) == 0
    capsys.readouterr()  # drain the unlink's text output
    assert cli.main(["dev", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["recorded"] is False
