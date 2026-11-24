"""The M5 exit criterion, run against a scratch copy of the real tree.

A change made in a working checkout must be graded correctly and reported
against the harness's contract: an artifact-only edit is L1 and needs no code
change, a new tool name is L2 and verify names it, and a removal is L3.

Each test copies the vendored tree once. The copy is deliberate: mutating the
submodule is forbidden, and a test that could mutate it would eventually do so.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pact_harness import paths
from pact_harness.upstream.diff import Level, diff_surfaces
from pact_harness.upstream.inspect import inspect_surface
from pact_harness.upstream.verify import verify_surface


def plugin_root() -> Path:
    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    candidate = paths.resolve_plugin_root(env={}, harness_root=root)
    if candidate is None or not candidate.is_dir():
        pytest.skip("vendored submodule not checked out")
    return candidate


@pytest.fixture(scope="module")
def baseline(plugin_root_path: Path):
    tree = inspect_surface(plugin_root_path)
    return tree


@pytest.fixture(scope="module")
def plugin_root_path() -> Path:
    return plugin_root()


@pytest.fixture()
def scratch(plugin_root_path: Path, tmp_path: Path) -> Path:
    destination = tmp_path / "scratch"
    shutil.copytree(plugin_root_path, destination)
    return destination


def edit_artifact(scratch: Path) -> None:
    command = scratch / "commands" / "orchestrate.md"
    command.parent.mkdir(parents=True, exist_ok=True)
    command.write_text(command.read_text(encoding="utf-8") + "\nA new sentence.\n", encoding="utf-8")


def test_an_artifact_only_change_is_l1_and_needs_no_code_edits(
    baseline, scratch: Path, tmp_path: Path
) -> None:
    from pact_harness import cli

    edit_artifact(scratch)
    after = inspect_surface(scratch)
    report = diff_surfaces(baseline.to_dict(), after.to_dict())
    assert report.level is Level.L1
    assert verify_surface(after).ok
    # The playbook compiles from the changed tree with no code change anywhere.
    out = tmp_path / "playbook"
    assert cli.main(["playbook", "build", "--plugin-root", str(scratch), "--out", str(out)]) == 0


def test_a_new_tool_name_is_l2_and_verify_names_it(baseline, scratch: Path) -> None:
    gate = scratch / "hooks" / "bootstrap_gate.py"
    text = gate.read_text(encoding="utf-8")
    text = text.replace(
        "_BLOCKED_TOOLS = frozenset({",
        '_BLOCKED_TOOLS = frozenset({"Frobnicate",',
        1,
    )
    gate.write_text(text, encoding="utf-8")
    after = inspect_surface(scratch)
    report = diff_surfaces(baseline.to_dict(), after.to_dict())
    assert report.level is Level.L2
    assert any(difference.kind == "tool-added" for difference in report.differences)
    verification = verify_surface(after)
    assert not verification.ok
    assert any("Frobnicate" in error for error in verification.errors)


def test_a_new_environment_read_is_l2_and_verify_names_it(baseline, scratch: Path) -> None:
    gate = scratch / "hooks" / "bootstrap_gate.py"
    gate.write_text(
        gate.read_text(encoding="utf-8") + '\n_EXTRA = os.environ.get("NEW_UNCLASSIFIED_VAR")\n',
        encoding="utf-8",
    )
    after = inspect_surface(scratch)
    report = diff_surfaces(baseline.to_dict(), after.to_dict())
    assert report.level is Level.L2
    verification = verify_surface(after)
    assert not verification.ok
    assert any("NEW_UNCLASSIFIED_VAR" in error for error in verification.errors)


def test_a_removed_registration_is_l3(baseline, scratch: Path) -> None:
    hooks_json = scratch / "hooks" / "hooks.json"
    document = json.loads(hooks_json.read_text(encoding="utf-8"))
    events = document["hooks"]
    event = next(name for name, groups in events.items() if groups)
    events[event].pop()
    hooks_json.write_text(json.dumps(document), encoding="utf-8")
    after = inspect_surface(scratch)
    report = diff_surfaces(baseline.to_dict(), after.to_dict())
    assert report.level is Level.L3
    assert any(difference.kind == "registration-removed" for difference in report.differences)


def test_an_emptied_registry_is_l4(baseline, scratch: Path) -> None:
    hooks_json = scratch / "hooks" / "hooks.json"
    hooks_json.write_text(json.dumps({"hooks": {}}), encoding="utf-8")
    after = inspect_surface(scratch)
    report = diff_surfaces(baseline.to_dict(), after.to_dict())
    assert report.level is Level.L4
