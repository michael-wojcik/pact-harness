"""Prove the bridged shim is byte-identical to running the hook directly.

Bridged mode replaces a hooks.json command with a call into the harness. That is
only safe if the replacement is invisible: the same bytes in, the same bytes
out, the same exit code.

Comparing the two paths requires care, because the obvious comparison is unfair.
Running both in one directory lets the first run mutate state the second sees.
Running them in two directories makes every path in the output differ. So each
path gets its own freshly built tree, and each output has its own tree's paths
replaced with a placeholder before comparison. What is left is the decision
channel: exit code and the bytes the hook wrote.

Stderr is not compared. Hooks log warnings and timings there, and two runs of the
same hook already differ.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..adapters.base import AdapterContext
from ..adapters.registry import get_adapter
from ..errors import PactHarnessError
from ..runtime.abi import run_event
from ..runtime.registry import HookRegistry
from ..runtime.vocabulary import inject_target_for
from .corpus import CorpusFrame, corpus_frames


@dataclass(frozen=True, slots=True)
class ParityCell:
    """One hook, run directly and through the bridged shim."""

    frame: str
    event: str
    hook: str
    direct_exit: int | None
    bridged_exit: int | None
    direct_sha: str
    bridged_sha: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view."""
        return {
            "frame": self.frame,
            "event": self.event,
            "hook": self.hook,
            "direct_exit": self.direct_exit,
            "bridged_exit": self.bridged_exit,
            "direct_sha": self.direct_sha,
            "bridged_sha": self.bridged_sha,
            "ok": self.ok,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ParityReport:
    """Every cell compared, plus the ones that diverged."""

    cells: tuple[ParityCell, ...] = ()
    failures: tuple[str, ...] = ()
    frames: int = 0

    @property
    def ok(self) -> bool:
        """Whether every cell was byte-identical in the decision channel."""
        return not self.failures and all(cell.ok for cell in self.cells)

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view."""
        return {
            "ok": self.ok,
            "frames": self.frames,
            "counts": {"cells": len(self.cells), "mismatches": len(self.failures)},
            "cells": [cell.to_dict() for cell in self.cells],
            "failures": list(self.failures),
        }


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _src_root() -> Path:
    """The directory that makes pact_harness importable in a child."""
    return Path(__file__).resolve().parents[2]


def _clean_env() -> dict[str, str]:
    """The environment both paths inherit, without host CLAUDE_/PACT_ values."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("CLAUDE_") and not key.startswith("PACT_")
    }


def _normalise(data: bytes, roots: tuple[Path, ...]) -> bytes:
    """Replace each run's own paths with placeholders before comparison."""
    text = data
    for index, root in enumerate(roots):
        text = text.replace(str(root).encode("utf-8"), b"<tree%d>" % index)
    return text


def compare_decision(
    direct_stdout: bytes,
    direct_exit: int | None,
    bridged_stdout: bytes,
    bridged_exit: int | None,
    *,
    direct_roots: tuple[Path, ...] = (),
    bridged_roots: tuple[Path, ...] = (),
) -> tuple[bool, str]:
    """Compare the decision channel of two runs after path normalisation."""
    if direct_exit != bridged_exit:
        return False, f"exit {direct_exit!r} != {bridged_exit!r}"
    left = _normalise(direct_stdout, direct_roots)
    right = _normalise(bridged_stdout, bridged_roots)
    if left != right:
        return False, f"stdout sha {_sha(left)} != {_sha(right)}"
    return True, ""


def run_parity(
    plugin_root: Path,
    *,
    adapter_id: str = "claude-code",
    python: str | None = None,
    timeout_s: float = 120.0,
) -> ParityReport:
    """Run every corpus cell directly and through the shim, then compare."""
    if adapter_id != "claude-code":
        raise PactHarnessError(
            f"byte parity is defined against the native adapter; {adapter_id!r} is not covered"
        )
    interpreter = python or sys.executable
    adapter = get_adapter(adapter_id)
    registry = HookRegistry.from_plugin_root(plugin_root)
    base_env = _clean_env()
    src = str(_src_root())

    scratch = Path(tempfile.mkdtemp(prefix="pact-parity-"))
    direct_project, direct_state = scratch / "direct" / "project", scratch / "direct" / "state"
    bridged_project, bridged_state = scratch / "bridged" / "project", scratch / "bridged" / "state"
    for directory in (direct_project, direct_state, bridged_project, bridged_state):
        directory.mkdir(parents=True)

    direct_frames = corpus_frames(direct_project)
    bridged_frames = corpus_frames(bridged_project)

    cells: list[ParityCell] = []
    failures: list[str] = []
    for frame_a, frame_b in zip(direct_frames, bridged_frames):
        event = adapter.normalize(frame_a.frame)
        legacy = adapter.legacy_stdin(event)
        context = AdapterContext(
            plugin_root=plugin_root,
            state_root=direct_state,
            python=interpreter,
            base_env=base_env,
        )
        env = adapter.hook_env(event, context)
        runs = run_event(
            registry,
            frame_a.event,
            stdin=legacy.data,
            env=env.values,
            python=interpreter,
            tool_name=frame_a.tool_name,
            cwd=str(direct_project),
            inject_target=inject_target_for(event.kind),
        )
        bridged_input = json.dumps(frame_b.frame).encode("utf-8")
        bridged_env = dict(base_env)
        bridged_env["PYTHONPATH"] = src + (os.pathsep + bridged_env["PYTHONPATH"] if bridged_env.get("PYTHONPATH") else "")
        for direct in runs:
            try:
                bridged = subprocess.run(
                    [
                        interpreter,
                        "-m",
                        "pact_harness",
                        "hook",
                        direct.registration.hook,
                        "--adapter",
                        adapter_id,
                        "--passthrough",
                        "--plugin-root",
                        str(plugin_root),
                        "--state-root",
                        str(bridged_state),
                    ],
                    input=bridged_input,
                    capture_output=True,
                    env=bridged_env,
                    cwd=str(bridged_project),
                    timeout=timeout_s,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                detail = f"bridged shim failed to start: {exc}"
                cells.append(
                    ParityCell(
                        frame=frame_a.name,
                        event=direct.registration.event,
                        hook=direct.registration.hook,
                        direct_exit=direct.result.exit,
                        bridged_exit=None,
                        direct_sha=_sha(direct.result.stdout),
                        bridged_sha="",
                        ok=False,
                        detail=detail,
                    )
                )
                failures.append(f"{frame_a.name}: {direct.registration.hook}: {detail}")
                continue
            ok, detail = compare_decision(
                direct.result.stdout,
                direct.result.exit,
                bridged.stdout,
                bridged.returncode,
                direct_roots=(direct_project, direct_state),
                bridged_roots=(bridged_project, bridged_state),
            )
            cells.append(
                ParityCell(
                    frame=frame_a.name,
                    event=direct.registration.event,
                    hook=direct.registration.hook,
                    direct_exit=direct.result.exit,
                    bridged_exit=bridged.returncode,
                    direct_sha=_sha(direct.result.stdout),
                    bridged_sha=_sha(bridged.stdout),
                    ok=ok,
                    detail=detail,
                )
            )
            if not ok:
                failures.append(f"{frame_a.name}: {direct.registration.hook}: {detail}")
    return ParityReport(cells=tuple(cells), failures=tuple(failures), frames=len(direct_frames))
