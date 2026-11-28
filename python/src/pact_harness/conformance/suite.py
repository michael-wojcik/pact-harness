"""Run every registration and check the harness understood it.

Two assertions per check, chosen because each catches a different silent
failure.

Frame fidelity: the adapter re-serialises a Claude Code frame, and decoding that
must reproduce the original document. A dropped field would be a field a hook
never sees, which looks exactly like the hook deciding nothing.

Decode health: the hook's stdout and exit code must decode without a problem,
a refusal must produce a refusal, and no fatal decode error may appear. A
new upstream output shape that the decoder does not recognise would otherwise
pass unnoticed until a gate stopped applying.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ..adapters.base import AdapterContext
from ..adapters.registry import get_adapter
from ..errors import PactHarnessError
from ..runtime.abi import HookRun, run_event
from ..runtime.registry import HookRegistry
from ..runtime.vocabulary import inject_target_for
from .corpus import CorpusFrame, corpus_frames

#: Adapters whose frames this corpus speaks.
_NATIVE_ADAPTERS = frozenset({"claude-code"})


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One hook's outcome under one frame."""

    frame: str
    event: str
    hook: str
    exit_code: int | None
    directives: tuple[str, ...]
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view."""
        return {
            "frame": self.frame,
            "event": self.event,
            "hook": self.hook,
            "exit": self.exit_code,
            "directives": list(self.directives),
            "ok": self.ok,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ConformanceReport:
    """Every check, plus the registrations never exercised."""

    checks: tuple[CheckResult, ...] = ()
    uncovered: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()
    frames: tuple[str, ...] = field(default_factory=tuple)
    exercised: int = 0
    total_registrations: int = 0

    @property
    def ok(self) -> bool:
        """Whether every check passed and every registration was exercised."""
        return not self.failures and not self.uncovered

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view."""
        return {
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
            "uncovered": list(self.uncovered),
            "failures": list(self.failures),
            "frames": list(self.frames),
            "counts": {
                "checks": len(self.checks),
                "frames": len(self.frames),
                "failures": len(self.failures),
                "uncovered": len(self.uncovered),
                "exercised": self.exercised,
                "total_registrations": self.total_registrations,
            },
        }


def _judge(frame: str, run: HookRun) -> CheckResult:
    """Decide whether one run is conformant, and why not when it is not."""
    kinds = tuple(directive.kind for directive in run.decoded.directives)
    hook = run.registration.hook
    exit_code = run.result.exit

    def result(ok: bool, detail: str = "") -> CheckResult:
        return CheckResult(
            frame=frame,
            event=run.registration.event,
            hook=hook,
            exit_code=exit_code,
            directives=kinds,
            ok=ok,
            detail=detail,
        )

    if run.result.launch_error:
        return result(False, f"launch failed: {run.result.launch_error}")
    if run.result.timed_out:
        return result(False, "hook timed out")
    if exit_code not in (0, 2):
        return result(False, f"unexpected exit code {exit_code!r}")
    if run.decoded.problem is not None:
        return result(False, f"decode problem: {run.decoded.problem}")
    if any(directive.kind == "hook_error" and directive.fatal for directive in run.decoded.directives):
        return result(False, "fatal decode error")
    if exit_code == 2 and not any(kind in ("deny", "ask") for kind in kinds):
        return result(False, "a refusal produced no deny or ask directive")
    return result(True)


@dataclass(frozen=True, slots=True)
class CorpusRun:
    """One registration executed against one corpus frame.

    The stdin, environment and working directory are carried alongside the
    result because a parity check has to replay exactly what the hook saw, not
    reconstruct it from the corpus.
    """

    frame: "CorpusFrame"
    run: HookRun
    stdin: bytes = b""
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: str = ""


def collect_corpus_runs(
    plugin_root: Path,
    *,
    adapter_id: str = "claude-code",
    python: str | None = None,
    state_root: Path | None = None,
    extra_frames: tuple[CorpusFrame, ...] = (),
) -> tuple[tuple[CorpusRun, ...], tuple[str, ...]]:
    """Run every frame through every matching registration and return raw runs.

    Frame fidelity is checked here rather than by each caller: a frame the
    adapter cannot re-serialise without loss means no hook saw the document the
    corpus wrote, so nothing recorded from that frame is evidence.
    """
    if adapter_id not in _NATIVE_ADAPTERS:
        raise PactHarnessError(
            f"conformance corpus speaks the native frame vocabulary; {adapter_id!r} is not covered"
        )
    interpreter = python or sys.executable
    adapter = get_adapter(adapter_id)
    registry = HookRegistry.from_plugin_root(plugin_root)

    scratch = Path(tempfile.mkdtemp(prefix="pact-conformance-"))
    project = scratch / "project"
    project.mkdir()
    state = state_root if state_root is not None else scratch / "state"
    state.mkdir(parents=True, exist_ok=True)
    # The host's own CLAUDE_* and PACT_* variables must not leak into a
    # conformance run: they would make the result depend on the machine.
    base_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("CLAUDE_") and not key.startswith("PACT_")
    }
    context = AdapterContext(
        plugin_root=plugin_root,
        state_root=state,
        python=interpreter,
        base_env=base_env,
    )

    collected: list[CorpusRun] = []
    failures: list[str] = []
    frames = corpus_frames(project)
    if extra_frames:
        from .upstream_corpus import rebase

        frames = frames + tuple(rebase(frame, project) for frame in extra_frames)
    for corpus_frame in frames:
        event = adapter.normalize(corpus_frame.frame)
        legacy = adapter.legacy_stdin(event)
        try:
            redelivered = json.loads(legacy.data)
        except json.JSONDecodeError:
            failures.append(f"{corpus_frame.name}: adapter produced unreadable stdin")
            continue
        if redelivered != corpus_frame.frame:
            failures.append(f"{corpus_frame.name}: frame fidelity lost in re-serialisation")
            continue
        env = adapter.hook_env(event, context)
        runs = run_event(
            registry,
            corpus_frame.event,
            stdin=legacy.data,
            env=env.values,
            python=interpreter,
            tool_name=corpus_frame.tool_name,
            cwd=str(project),
            inject_target=inject_target_for(event.kind),
        )
        for run in runs:
            collected.append(
                CorpusRun(
                    corpus_frame,
                    run,
                    stdin=legacy.data,
                    env=env.values,
                    cwd=str(project),
                )
            )
    return tuple(collected), tuple(failures)


def run_conformance(
    plugin_root: Path,
    *,
    adapter_id: str = "claude-code",
    python: str | None = None,
    state_root: Path | None = None,
    extra_frames: tuple[CorpusFrame, ...] = (),
) -> ConformanceReport:
    """Run the corpus through every registration."""
    collected, frame_failures = collect_corpus_runs(
        plugin_root,
        adapter_id=adapter_id,
        python=python,
        state_root=state_root,
        extra_frames=extra_frames,
    )
    checks: list[CheckResult] = []
    failures: list[str] = list(frame_failures)
    covered: set[str] = set()
    frame_names: list[str] = []
    for item in collected:
        covered.add(item.run.registration.key)
        if item.frame.name not in frame_names:
            frame_names.append(item.frame.name)
        check = _judge(item.frame.name, item.run)
        checks.append(check)
        if not check.ok:
            failures.append(f"{item.frame.name}: {check.hook} failed: {check.detail}")

    registry = HookRegistry.from_plugin_root(plugin_root)
    uncovered = tuple(
        registration.key
        for registration in registry.registrations
        if registration.key not in covered
    )
    return ConformanceReport(
        checks=tuple(checks),
        uncovered=uncovered,
        failures=tuple(failures),
        frames=tuple(frame_names),
        exercised=len(covered),
        total_registrations=len(registry),
    )