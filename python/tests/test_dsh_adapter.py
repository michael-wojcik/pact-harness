"""The DSH adapter, Python side: the frame it synthesises for the hooks."""

from __future__ import annotations

import json
from pathlib import Path

from pact_harness.adapters.base import AdapterContext
from pact_harness.adapters.dsh import LEAD_AGENT_TYPE, DshAdapter
from pact_harness.capabilities import Tier
from pact_harness.contract import ActorRef, EventV1, SessionRef, TaskRef, TeamRef, ToolRef


def adapter() -> DshAdapter:
    return DshAdapter()


def event(**overrides) -> EventV1:
    base = dict(
        id="evt_1",
        kind="tool.pre",
        at="2026-11-24T12:00:00Z",
        adapter="dsh",
        session=SessionRef(id="sess", cwd="/tmp/project"),
        actor=ActorRef(role="lead", name="lead", type="pact-orchestrator"),
        tool=ToolRef(canonical="Edit", native="edit", input={"file_path": "/tmp/project/a.py"}),
    )
    base.update(overrides)
    return EventV1(**base)


def frame_of(dsh: DshAdapter, value: EventV1) -> dict:
    return json.loads(dsh.legacy_stdin(value).data)


def test_declares_the_full_tier() -> None:
    assert adapter().capabilities.tier is Tier.T3


def test_normalize_accepts_the_contract_document() -> None:
    document = event().to_dict()
    assert adapter().normalize(document) == event()


def test_lead_maps_onto_the_type_the_hooks_classify_by() -> None:
    frame = frame_of(adapter(), event())
    assert frame["agent_type"] == LEAD_AGENT_TYPE


def test_teammate_maps_onto_its_own_type() -> None:
    teammate = event(actor=ActorRef(role="teammate", name="architect", type="pact-architect"))
    assert frame_of(adapter(), teammate)["agent_type"] == "pact-architect"


def test_teammate_without_a_type_falls_back_to_its_name() -> None:
    teammate = event(actor=ActorRef(role="teammate", name="coder"))
    assert frame_of(adapter(), teammate)["agent_type"] == "coder"


def test_unknown_role_omits_the_field_entirely() -> None:
    plain = event(actor=ActorRef(role="unknown"))
    assert "agent_type" not in frame_of(adapter(), plain)


def test_tool_fields_come_from_the_canonical_name() -> None:
    frame = frame_of(adapter(), event())
    assert frame["tool_name"] == "Edit"
    assert frame["tool_input"] == {"file_path": "/tmp/project/a.py"}
    assert frame["hook_event_name"] == "PreToolUse"


def test_a_spawn_request_becomes_an_agent_tool_call() -> None:
    spawn = EventV1(
        id="evt_spawn",
        kind="agent.spawn.pre",
        at="2026-11-24T12:00:00Z",
        adapter="dsh",
        session=SessionRef(id="sess", cwd="/tmp/project"),
        actor=ActorRef(role="lead"),
        payload={
            "subagent_type": "pact-architect",
            "name": "architect",
            "prompt": "design it",
            "description": "architecture",
        },
    )
    frame = frame_of(adapter(), spawn)
    assert frame["tool_name"] == "Agent"
    assert frame["tool_input"]["subagent_type"] == "pact-architect"
    assert frame["hook_event_name"] == "PreToolUse"


def test_a_task_event_carries_its_task() -> None:
    completed = event(
        kind="task.completed",
        tool=None,
        task=TaskRef(
            id="task-7",
            subject="CODE: feature",
            team_name="pact-team",
            teammate_name="backend",
        ),
    )
    frame = frame_of(adapter(), completed)
    assert frame["task_id"] == "task-7"
    assert frame["task_subject"] == "CODE: feature"
    assert frame["teammate_name"] == "backend"
    assert frame["hook_event_name"] == "TaskCompleted"


def test_team_name_reaches_the_frame() -> None:
    with_team = event(team=TeamRef(name="pact-team"))
    assert frame_of(adapter(), with_team)["team_name"] == "pact-team"


def test_hook_environment_points_at_the_dsh_state_root(tmp_path: Path) -> None:
    context = AdapterContext(
        plugin_root=Path("/tmp/plugin"),
        state_root=Path("/tmp/state"),
        base_env={"PATH": "/usr/bin"},
    )
    env = adapter().hook_env(event(), context)
    assert env.values["CLAUDE_CONFIG_DIR"] == "/tmp/state"
    assert env.values["CLAUDE_PROJECT_DIR"] == "/tmp/project"
    assert env.values["CLAUDE_CODE_SESSION_ID"] == "sess"
