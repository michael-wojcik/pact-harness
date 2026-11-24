"""The engine: folding several hook outcomes into one decision."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pact_harness import paths
from pact_harness.adapters.claude_code import ClaudeCodeAdapter
from pact_harness.adapters.dsh import DshAdapter
from pact_harness.contract import ActorRef, Directive, EventV1, SessionRef, TaskRef, ToolRef
from pact_harness.engine import EngineConfig, evaluate, fold
from pact_harness.runtime.abi import HookRun
from pact_harness.runtime.decode import DecodedHook
from pact_harness.runtime.launch import HookResult
from pact_harness.runtime.registry import HookRegistration


def run_of(hook: str, *directives: Directive, problem: str | None = None) -> HookRun:
    registration = HookRegistration(
        event="PreToolUse",
        group_index=0,
        command_index=0,
        matcher=None,
        script=Path("/tmp") / f"{hook}.py",
        hook=hook,
        timeout_s=None,
        async_=False,
    )
    result = HookResult(
        hook=hook,
        script=registration.script,
        exit=0,
        stdout=b"",
        stderr=b"",
        duration_ms=1,
    )
    return HookRun(
        registration=registration,
        result=result,
        decoded=DecodedHook(directives=tuple(directives), problem=problem),
    )


def event_of(kind: str = "tool.pre") -> EventV1:
    tool = (
        ToolRef(canonical="Edit", native="edit", input={})
        if kind in {"tool.pre", "tool.post", "tool.fail"}
        else None
    )
    task = TaskRef(id="task-1") if kind == "task.completed" else None
    return EventV1(
        id="evt-1",
        kind=kind,
        at="2026-11-24T12:00:00Z",
        adapter="test",
        session=SessionRef(id="sess-1", cwd="/tmp"),
        tool=tool,
        task=task,
    )


def kinds(directives) -> list[str]:
    return [directive.kind for directive in directives]


def test_a_refusal_beats_advice() -> None:
    runs = (
        run_of("a", Directive.inject("consider this")),
        run_of("b", Directive.deny("no")),
        run_of("c", Directive.notify("also no")),
    )
    folded = fold(runs, event_of())
    assert kinds(folded) == ["deny", "inject", "notify"]


def test_ask_applies_when_nothing_refuses() -> None:
    assert kinds(fold((run_of("a", Directive.ask("confirm")),), event_of())) == ["ask"]


def test_block_applies_when_nothing_refuses() -> None:
    assert kinds(fold((run_of("a", Directive.block("finish")),), event_of())) == ["block"]


def test_cancel_is_kept() -> None:
    assert kinds(fold((run_of("a", Directive.cancel()),), event_of())) == ["cancel"]


def test_allow_is_dropped() -> None:
    assert kinds(fold((run_of("a", Directive.allow()),), event_of())) == ["silent"]


def test_silent_is_dropped_beside_advice() -> None:
    runs = (run_of("a", Directive.silent()), run_of("b", Directive.notify("hi")))
    assert kinds(fold(runs, event_of())) == ["notify"]


def test_everything_silent_folds_to_silent() -> None:
    assert kinds(fold((run_of("a", Directive.silent()),), event_of())) == ["silent"]


def test_fatal_guard_failure_refuses_on_a_veto_event() -> None:
    run = run_of("dispatch_gate", Directive.hook_error("dispatch_gate", stage="launch", message="missing", fatal=True))
    folded = fold((run,), event_of("tool.pre"))
    assert kinds(folded) == ["deny"]
    assert folded[0].rule == "dispatch_gate"
    assert "guard unavailable" in (folded[0].reason or "")


def test_fatal_guard_failure_is_only_advice_where_a_veto_is_meaningless() -> None:
    run = run_of("peer_inject", Directive.hook_error("peer_inject", stage="launch", message="missing", fatal=True))
    folded = fold((run,), event_of("agent.start"))
    assert kinds(folded) == ["notify"]


def test_non_fatal_failure_is_advice_on_every_event() -> None:
    run = run_of("track_files", Directive.hook_error("track_files", stage="exit", message="crashed"))
    assert kinds(fold((run,), event_of("tool.post"))) == ["notify"]


def test_fold_preserves_hook_order_for_advice() -> None:
    runs = (
        run_of("a", Directive.inject("one")),
        run_of("b", Directive.inject("two")),
    )
    assert [directive.text for directive in fold(runs, event_of())] == ["one", "two"]


def test_engine_evaluates_a_real_event() -> None:
    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    plugin = paths.resolve_plugin_root(env={}, harness_root=root)
    if plugin is None:
        pytest.skip("vendored submodule not checked out")
    adapter = ClaudeCodeAdapter()
    event = adapter.normalize(
        {
            "session_id": "sess-engine",
            "cwd": str(root),
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(root / "README.md"), "old_string": "a", "new_string": "b"},
            "agent_type": "pact-orchestrator",
            "tool_use_id": "toolu_engine",
        }
    )
    config = EngineConfig(
        adapter_id="claude-code",
        plugin_root=plugin,
        state_root=Path("/tmp/pact-engine-state"),
        python=sys.executable,
    )
    envelope = evaluate(event, config, adapter=adapter)
    assert envelope.meta.adapter == "claude-code"
    assert envelope.meta.event == "tool.pre"
    assert envelope.meta.elapsed_ms is not None and envelope.meta.elapsed_ms >= 0
    assert envelope.directives


def test_dsh_adapter_evaluates_a_normalised_event() -> None:
    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    plugin = paths.resolve_plugin_root(env={}, harness_root=root)
    if plugin is None:
        pytest.skip("vendored submodule not checked out")
    event = EventV1(
        id="evt_dsh_1",
        kind="tool.pre",
        at="2026-11-24T12:00:00Z",
        adapter="dsh",
        session=SessionRef(id="dsh-session", cwd=str(root), project_dir=str(root)),
        actor=ActorRef(role="lead", name="lead", type="pact-orchestrator"),
        tool=ToolRef(canonical="Edit", native="edit", input={"file_path": str(root / "README.md")}),
    )
    config = EngineConfig(
        adapter_id="dsh",
        plugin_root=plugin,
        state_root=Path("/tmp/pact-dsh-state"),
        python=sys.executable,
    )
    envelope = evaluate(event, config, adapter=DshAdapter())
    assert envelope.meta.adapter == "dsh"
    assert envelope.directives
