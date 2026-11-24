"""End-to-end: a real vendored hook, run and decoded through the harness.

These tests execute the actual PACT hooks from the pinned submodule. They are
the first place where the adapter, the registry, the launcher and the decoder
are exercised together, and they are deliberately behavioural: a real gate must
really refuse a really dangerous command.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pact_harness import paths
from pact_harness.adapters.base import AdapterContext
from pact_harness.adapters.claude_code import ClaudeCodeAdapter
from pact_harness.runtime.abi import run_event
from pact_harness.runtime.registry import HookRegistry, HookRegistration
from pact_harness.runtime.transport import ColdTransport, warm_supported

REPO_ROOT = paths.find_harness_root()


def plugin_root() -> Path:
    if REPO_ROOT is None:
        pytest.skip("not running inside the harness checkout")
    root = paths.resolve_plugin_root(env={}, harness_root=REPO_ROOT)
    if root is None or not root.is_dir():
        pytest.skip("vendored submodule not checked out")
    return root


@pytest.fixture(scope="module")
def registry() -> HookRegistry:
    return HookRegistry.from_plugin_root(plugin_root())


@pytest.fixture()
def adapter() -> ClaudeCodeAdapter:
    return ClaudeCodeAdapter()


def invocation(adapter: ClaudeCodeAdapter, frame: dict, state: Path):
    event = adapter.normalize(frame)
    context = AdapterContext(plugin_root=plugin_root(), state_root=state, python=sys.executable)
    return event, context, adapter.hook_env(event, context)


def test_edit_event_runs_its_five_hooks(registry: HookRegistry, adapter: ClaudeCodeAdapter, tmp_path: Path) -> None:
    work = tmp_path / "proj"
    work.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    frame = {
        "session_id": "sess-abi",
        "cwd": str(work),
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(work / "a.py"), "old_string": "a", "new_string": "b"},
        "agent_type": "pact-orchestrator",
        "tool_use_id": "toolu_abi",
    }
    event, context, env = invocation(adapter, frame, state)
    runs = run_event(
        registry,
        "PreToolUse",
        stdin=adapter.legacy_stdin(event).data,
        env=env.values,
        python=sys.executable,
        tool_name="Edit",
        cwd=str(work),
    )
    assert len(runs) == 5
    assert [run.registration.hook for run in runs] == [
        "bootstrap_gate",
        "worktree_guard",
        "pin_staleness_gate",
        "pin_caps_gate",
        "task_claim_gate",
    ]
    for run in runs:
        assert run.result.launch_error is None
        assert run.result.timed_out is False
        assert run.result.exit in (0, 2)
        assert run.decoded.directives, run.registration.hook


def test_force_push_is_refused_by_the_real_gate(
    registry: HookRegistry, adapter: ClaudeCodeAdapter, tmp_path: Path
) -> None:
    if REPO_ROOT is None:
        pytest.skip("not running inside the harness checkout")
    state = tmp_path / "state"
    state.mkdir()
    frame = {
        "session_id": "sess-abi",
        "cwd": str(REPO_ROOT),
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "git push --force origin main"},
        "agent_type": "pact-orchestrator",
        "tool_use_id": "toolu_push",
    }
    event, context, env = invocation(adapter, frame, state)
    runs = run_event(
        registry,
        "PreToolUse",
        stdin=adapter.legacy_stdin(event).data,
        env=env.values,
        python=sys.executable,
        tool_name="Bash",
        cwd=str(REPO_ROOT),
    )
    merge_guard = [run for run in runs if run.registration.hook == "merge_guard_pre"]
    assert merge_guard, "merge_guard_pre is registered on a Bash PreToolUse frame"
    decoded = merge_guard[0].decoded
    assert decoded.directives[0].kind == "deny"
    assert decoded.directives[0].rule == "merge_guard_pre"
    assert "approval" in (decoded.directives[0].reason or "").lower()


def test_environment_isolates_state(
    adapter: ClaudeCodeAdapter, tmp_path: Path
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    frame = {
        "session_id": "sess-env",
        "cwd": str(tmp_path),
        "hook_event_name": "SessionStart",
        "source": "startup",
        "agent_type": "pact-orchestrator",
    }
    event, context, env = invocation(adapter, frame, state)
    assert env.values["CLAUDE_CONFIG_DIR"] == str(state)
    assert env.values["CLAUDE_PROJECT_DIR"] == str(tmp_path)
    assert env.values["CLAUDE_CODE_SESSION_ID"] == "sess-env"
    assert "CLAUDE_ENV_FILE" not in env.values


def test_registrations_are_executable(registry: HookRegistry) -> None:
    for registration in registry.registrations:
        assert isinstance(registration, HookRegistration)
        assert registration.script.suffix == ".py"
        assert registration.script.is_file()


def test_cold_transport_is_the_default_and_warm_is_available_here() -> None:
    assert ColdTransport().name == "cold"
    # The warm transport is POSIX-only and is the planned latency fix.
    assert warm_supported() is True
