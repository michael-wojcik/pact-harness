"""The corpus verdict matrix: recording, replaying, and naming a change."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pact_harness import paths
from pact_harness.conformance.harvest import (
    Harvest,
    HarvestCell,
    HarvestError,
    harvest_corpus,
    load_harvest,
    replay_harvest,
    save_harvest,
)


def plugin_root() -> Path:
    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    candidate = paths.resolve_plugin_root(env={}, harness_root=root)
    if candidate is None or not candidate.is_dir():
        pytest.skip("vendored submodule not checked out")
    return candidate


def pin_values() -> tuple[str, str]:
    from pact_harness.vendor import load_pin

    root = paths.find_harness_root()
    if root is None:
        return "", ""
    pin = load_pin(root)
    return pin.pact_version, pin.commit


@pytest.fixture(scope="module")
def recorded() -> Harvest:
    version, commit = pin_values()
    return harvest_corpus(plugin_root(), pact_version=version, pact_commit=commit)


def test_the_matrix_covers_the_corpus(recorded: Harvest) -> None:
    assert len(recorded.cells) == 48
    assert len({cell.hook for cell in recorded.cells}) == 28
    assert len({cell.frame for cell in recorded.cells}) == 24
    kinds = {kind for cell in recorded.cells for kind in cell.directives}
    assert {"deny", "inject", "silent"} <= kinds


def test_the_committed_baseline_matches_the_pinned_tree(recorded: Harvest) -> None:
    """The committed baseline is also the determinism guard.

    A fresh harvest must reproduce the committed file exactly. If it does not,
    either the pin moved without regenerating the baseline or the corpus stopped
    being deterministic -- both are failures here.
    """
    root = paths.find_harness_root()
    assert root is not None
    committed = load_harvest(root / "harness" / "corpus" / "pact-4.8.36.json")
    assert committed.to_dict() == recorded.to_dict()


def test_save_and_load_round_trip(tmp_path: Path, recorded: Harvest) -> None:
    path = save_harvest(tmp_path / "baseline.json", recorded)
    loaded = load_harvest(path)
    assert loaded.to_dict() == recorded.to_dict()
    assert loaded.pact_version == recorded.pact_version


def test_replaying_the_same_tree_reports_nothing(recorded: Harvest) -> None:
    report = replay_harvest(plugin_root(), recorded)
    assert report.ok
    assert report.changes == ()
    assert report.cells == 48


def test_replay_names_every_kind_of_change(recorded: Harvest) -> None:
    """One replay, all three outcomes.

    A recorded cell whose verdict differs is a change; a recorded cell the
    current tree no longer runs is a removal; a current cell the record does not
    contain is an addition. The baseline is mutated in all three ways at once so
    the comparison costs one harvest.
    """
    cells = list(recorded.cells)
    target = cells[0]
    cells[0] = HarvestCell(
        frame=target.frame,
        event=target.event,
        hook=target.hook,
        exit_code=target.exit_code,
        directives=("deny",),
        ok=target.ok,
    )
    dropped = cells.pop()  # current tree still runs this one -> an addition
    ghost = HarvestCell(
        frame="invented-frame",
        event="PreToolUse",
        hook="ghost_gate",
        exit_code=0,
        directives=(),
        ok=True,
    )
    cells.append(ghost)  # current tree never runs this -> a removal
    mutated = Harvest(pact_version=recorded.pact_version, pact_commit="", cells=tuple(cells))
    report = replay_harvest(plugin_root(), mutated)
    assert not report.ok
    assert any(change.field == "directives" for change in report.changes)
    assert ghost.key in report.removed
    assert dropped.key in report.added


def test_a_bad_baseline_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"schemaVersion": 99}))
    with pytest.raises(HarvestError, match="schemaVersion"):
        load_harvest(path)
    with pytest.raises(HarvestError, match="cannot read"):
        load_harvest(tmp_path / "missing.json")


def test_the_corpus_cli_records_and_replays(tmp_path: Path, capsys) -> None:
    from pact_harness import cli

    out = tmp_path / "matrix.json"
    assert cli.main(["corpus", "harvest", "--out", str(out)]) == 0
    capsys.readouterr()
    assert cli.main(["corpus", "replay", str(out)]) == 0
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["pactVersion"] == pin_values()[0]
    assert len(document["cells"]) == 48
