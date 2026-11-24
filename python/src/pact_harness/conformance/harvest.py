"""Record and replay the corpus verdicts across an upstream bump.

Conformance answers a question about one tree: does every registration run, and
does the harness understand what each hook said. It cannot answer "what changed
between these two trees" because its expectations are assertions, not a record.

This module records the answer for every (frame, registration) pair and replays
it against another tree. What it stores is the verdict, not the bytes: frame
name, hook, exit code, directive kinds and whether the run was conformant.
Verdicts are deterministic; stdout is not, because hooks embed paths and
timestamps, and a byte baseline would cry wolf on every bump.

So a bump that changes nothing reports nothing, and a bump that flips a gate's
verdict names the frame and the hook.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import PactHarnessError
from .suite import collect_corpus_runs

SCHEMA_VERSION = 1


class HarvestError(PactHarnessError):
    """The baseline could not be read or written."""


@dataclass(frozen=True, slots=True)
class HarvestCell:
    """One registration's verdict under one frame."""

    frame: str
    event: str
    hook: str
    exit_code: int | None
    directives: tuple[str, ...]
    ok: bool

    @property
    def key(self) -> str:
        """Stable identity across trees."""
        return f"{self.frame}|{self.event}|{self.hook}"

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view."""
        return {
            "frame": self.frame,
            "event": self.event,
            "hook": self.hook,
            "exit_code": self.exit_code,
            "directives": list(self.directives),
            "ok": self.ok,
        }


@dataclass(frozen=True, slots=True)
class Harvest:
    """The recorded verdict matrix for one tree."""

    pact_version: str
    pact_commit: str
    cells: tuple[HarvestCell, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view, deterministic."""
        return {
            "schemaVersion": self.schema_version,
            "pactVersion": self.pact_version,
            "pactCommit": self.pact_commit,
            "cells": [cell.to_dict() for cell in self.cells],
        }


@dataclass(frozen=True, slots=True)
class CellChange:
    """One field that differs for a cell present in both trees."""

    key: str
    field: str
    before: Any
    after: Any

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view."""
        return {
            "key": self.key,
            "field": self.field,
            "before": self.before,
            "after": self.after,
        }


@dataclass(frozen=True, slots=True)
class ReplayReport:
    """What replaying a recorded verdict matrix produced."""

    recorded_version: str
    current_version: str
    cells: int = 0
    changes: tuple[CellChange, ...] = ()
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether the verdict matrix is identical."""
        return not (self.changes or self.added or self.removed)

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view."""
        return {
            "ok": self.ok,
            "recorded_version": self.recorded_version,
            "current_version": self.current_version,
            "cells": self.cells,
            "changes": [change.to_dict() for change in self.changes],
            "added": list(self.added),
            "removed": list(self.removed),
        }


def harvest_corpus(
    plugin_root: Path,
    *,
    adapter_id: str = "claude-code",
    python: str | None = None,
    pact_version: str = "",
    pact_commit: str = "",
) -> Harvest:
    """Run the corpus once and record every registration's verdict."""
    runs, failures = collect_corpus_runs(
        plugin_root, adapter_id=adapter_id, python=python
    )
    if failures:
        raise HarvestError("; ".join(failures))
    cells: list[HarvestCell] = []
    for item in runs:
        kinds = tuple(directive.kind for directive in item.run.decoded.directives)
        cells.append(
            HarvestCell(
                frame=item.frame.name,
                event=item.run.registration.event,
                hook=item.run.registration.hook,
                exit_code=item.run.result.exit,
                directives=kinds,
                ok=item.run.decoded.problem is None,
            )
        )
    cells.sort(key=lambda cell: cell.key)
    return Harvest(pact_version=pact_version, pact_commit=pact_commit, cells=tuple(cells))


def save_harvest(path: Path, harvest: Harvest) -> Path:
    """Write a baseline deterministically."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(harvest.to_dict(), indent=2, sort_keys=True) + chr(10),
        encoding="utf-8",
    )
    return destination


def load_harvest(path: Path) -> Harvest:
    """Read a baseline."""
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise HarvestError(f"cannot read {source}: {exc}") from exc
    except ValueError as exc:
        raise HarvestError(f"{source} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("schemaVersion") != SCHEMA_VERSION:
        raise HarvestError(f"{source} is not a harvest baseline (schemaVersion {SCHEMA_VERSION})")
    raw_cells = data.get("cells")
    if not isinstance(raw_cells, list):
        raise HarvestError(f"{source} has no cells list")
    cells: list[HarvestCell] = []
    for raw in raw_cells:
        if not isinstance(raw, dict):
            raise HarvestError(f"{source} has a non-object cell")
        cells.append(
            HarvestCell(
                frame=str(raw.get("frame")),
                event=str(raw.get("event")),
                hook=str(raw.get("hook")),
                exit_code=raw.get("exit_code"),
                directives=tuple(str(item) for item in raw.get("directives") or ()),
                ok=bool(raw.get("ok")),
            )
        )
    return Harvest(
        pact_version=str(data.get("pactVersion") or ""),
        pact_commit=str(data.get("pactCommit") or ""),
        cells=tuple(cells),
    )


def replay_harvest(
    plugin_root: Path,
    recorded: Harvest,
    *,
    adapter_id: str = "claude-code",
    python: str | None = None,
    current_version: str = "",
) -> ReplayReport:
    """Run the corpus against a tree and compare it with a baseline."""
    current = harvest_corpus(
        plugin_root,
        adapter_id=adapter_id,
        python=python,
        pact_version=current_version,
    )
    before = {cell.key: cell for cell in recorded.cells}
    after = {cell.key: cell for cell in current.cells}
    changes: list[CellChange] = []
    for key in sorted(before.keys() & after.keys()):
        old, new = before[key], after[key]
        if old.exit_code != new.exit_code:
            changes.append(CellChange(key, "exit_code", old.exit_code, new.exit_code))
        if old.directives != new.directives:
            changes.append(
                CellChange(key, "directives", list(old.directives), list(new.directives))
            )
        if old.ok != new.ok:
            changes.append(CellChange(key, "ok", old.ok, new.ok))
    return ReplayReport(
        recorded_version=recorded.pact_version,
        current_version=current_version,
        cells=len(after),
        changes=tuple(changes),
        added=tuple(sorted(after.keys() - before.keys())),
        removed=tuple(sorted(before.keys() - after.keys())),
    )
