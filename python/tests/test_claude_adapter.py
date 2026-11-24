"""The Claude Code adapter: normalisation, legacy frame, and environment."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pact_harness.adapters.base import AdapterContext
from pact_harness.adapters.claude_code import ClaudeCodeAdapter, sanitize_id
from pact_harness.capabilities import Tier


def frame(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "session_id": "sess-1",
        "cwd": "/tmp/project",
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": "/tmp/project/a.py", "old_string": "a", "new_string": "b"},
        "agent_type": "pact-orchestrator",
        "tool_use_id": "toolu_1",
    }
    base.update(overrides)
    return base


@pytest.fixture()
def adapter() -> ClaudeCodeAdapter:
    return ClaudeCodeAdapter()


def test_declares_the_reference_tier(adapter: ClaudeCodeAdapter) -> None:
    assert adapter.capabilities.tier is Tier.T3


def test_tool_frame_normalises(adapter: ClaudeCodeAdapter) -> None:
    event = adapter.normalize(frame())
    assert event.kind == "tool.pre"
    assert event.tool is not None
    assert event.tool.canonical == "Edit"
    assert event.tool.native == "Edit"
    assert event.tool.input["file_path"] == "/tmp/project/a.py"
    assert event.session.id == "sess-1"
    assert event.adapter == "claude-code"


def test_lead_role_comes_from_the_agent_type(adapter: ClaudeCodeAdapter) -> None:
    assert adapter.normalize(frame()).actor.role == "lead"
    assert adapter.normalize(frame(agent_type="PACT:pact-orchestrator")).actor.role == "lead"
    assert adapter.normalize(frame(agent_type="pact-backend-coder")).actor.role == "teammate"
    assert adapter.normalize(frame(agent_type=None)).actor.role == "unknown"


def test_agent_type_is_preserved_for_the_hooks(adapter: ClaudeCodeAdapter) -> None:
    event = adapter.normalize(frame(agent_type="PACT:pact-orchestrator"))
    assert event.actor.type == "PACT:pact-orchestrator"
    assert json.loads(adapter.legacy_stdin(event).data)["agent_type"] == "PACT:pact-orchestrator"


def test_agent_spawn_is_its_own_kind(adapter: ClaudeCodeAdapter) -> None:
    event = adapter.normalize(
        frame(tool_name="Agent", tool_input={"subagent_type": "pact-architect", "name": "architect", "prompt": "design it"})
    )
    assert event.kind == "agent.spawn.pre"
    assert event.tool is None
    assert event.payload["subagent_type"] == "pact-architect"


def test_task_completed_carries_a_task(adapter: ClaudeCodeAdapter) -> None:
    event = adapter.normalize(
        frame(
            hook_event_name="TaskCompleted",
            tool_name=None,
            tool_input=None,
            task_id="task-7",
            task_subject="CODE: feature",
            team_name="pact-team",
            teammate_name="backend",
        )
    )
    assert event.kind == "task.completed"
    assert event.task is not None
    assert event.task.id == "task-7"
    assert event.task.teammate_name == "backend"


def test_unknown_event_name_is_refused(adapter: ClaudeCodeAdapter) -> None:
    with pytest.raises(ValueError, match="unknown Claude Code hook_event_name"):
        adapter.normalize(frame(hook_event_name="BeamMeUp"))


def test_legacy_stdin_reproduces_the_raw_frame(adapter: ClaudeCodeAdapter) -> None:
    original = frame()
    event = adapter.normalize(original)
    output = adapter.legacy_stdin(event)
    assert output.source == "raw-frame"
    assert json.loads(output.data) == original


def test_synthetic_event_falls_back_to_synthesis(adapter: ClaudeCodeAdapter) -> None:
    event = adapter.normalize(frame())
    synthetic = event.__class__(
        id="evt_1",
        kind=event.kind,
        at=event.at,
        adapter=event.adapter,
        session=event.session,
        actor=event.actor,
        tool=event.tool,
        payload=event.payload,
        raw={},
    )
    output = adapter.legacy_stdin(synthetic)
    assert output.source == "synthesized"
    decoded = json.loads(output.data)
    assert decoded["hook_event_name"] == "PreToolUse"
    assert decoded["tool_name"] == "Edit"


def test_hook_environment_sets_and_withholds(adapter: ClaudeCodeAdapter, tmp_path: Path) -> None:
    event = adapter.normalize(frame(cwd=str(tmp_path)))
    context = AdapterContext(
        plugin_root=Path("/tmp/plugin"),
        state_root=Path("/tmp/state"),
        python="python3",
        base_env={"PATH": "/usr/bin", "CLAUDE_ENV_FILE": "/tmp/env", "PACT_WORKTREE_PATH": "/tmp/w"},
    )
    env = adapter.hook_env(event, context)
    assert env.values["CLAUDE_CONFIG_DIR"] == "/tmp/state"
    assert env.values["CLAUDE_PLUGIN_ROOT"] == "/tmp/plugin"
    assert env.values["CLAUDE_PROJECT_DIR"] == str(tmp_path)
    assert env.values["CLAUDE_CODE_SESSION_ID"] == "sess-1"
    assert env.values["CLAUDE_PID"]
    assert "CLAUDE_ENV_FILE" not in env.values
    assert env.values["PACT_WORKTREE_PATH"] == "/tmp/w"


def test_render_is_identity(adapter: ClaudeCodeAdapter) -> None:
    event = adapter.normalize(frame())
    assert adapter.render([], event) == []


def test_identifier_sanitisation() -> None:
    assert sanitize_id("abc-123_XYZ") == "abc-123_XYZ"
    assert sanitize_id("session/with spaces") == "session_with_spaces"
    assert sanitize_id("", fallback="fallback") == "fallback"
    assert len(sanitize_id("x" * 200)) == 64
