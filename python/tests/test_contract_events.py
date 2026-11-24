"""pact.event.v1: construction rules and wire round-trips."""

from __future__ import annotations

import pytest

from pact_harness.contract import ActorRef, EventV1, SessionRef, TaskRef, ToolRef
from pact_harness.contract.events import EVENT_KINDS
from pact_harness.errors import ContractError

TOOL_KINDS = {"tool.pre", "tool.post", "tool.fail"}
TASK_KINDS = {"task.completed"}
BASE = dict(
    at="2026-11-24T12:00:00-05:00",
    adapter="test",
    session=SessionRef(id="sess-1", cwd="/tmp/project"),
)


def make_event(kind: str) -> EventV1:
    tool = ToolRef(canonical="Bash", native="bash") if kind in TOOL_KINDS else None
    task = TaskRef(id="task-1") if kind in TASK_KINDS else None
    return EventV1(id="evt-1", kind=kind, tool=tool, task=task, **BASE)


@pytest.mark.parametrize("kind", sorted(EVENT_KINDS))
def test_every_kind_round_trips(kind: str) -> None:
    event = make_event(kind)
    assert EventV1.from_dict(event.to_dict()) == event


def test_actor_defaults_to_unknown_role() -> None:
    assert make_event("session.start").actor.role == "unknown"


def test_unknown_field_is_rejected() -> None:
    payload = make_event("session.start").to_dict()
    payload["surprise"] = True
    with pytest.raises(ContractError, match="unknown field"):
        EventV1.from_dict(payload)


def test_wrong_contract_version_is_rejected() -> None:
    payload = make_event("session.start").to_dict()
    payload["v"] = 2
    with pytest.raises(ContractError, match="expected 1"):
        EventV1.from_dict(payload)


def test_missing_required_field_names_the_path() -> None:
    payload = make_event("session.start").to_dict()
    del payload["session"]["cwd"]
    with pytest.raises(ContractError, match="session.cwd"):
        EventV1.from_dict(payload)


def test_tool_kind_requires_tool() -> None:
    with pytest.raises(ContractError, match="required for kind"):
        EventV1(id="evt-1", kind="tool.pre", **BASE)


def test_tool_is_rejected_on_other_kinds() -> None:
    with pytest.raises(ContractError, match="not allowed for kind"):
        EventV1(
            id="evt-1",
            kind="session.start",
            tool=ToolRef(canonical="Bash", native="bash"),
            **BASE,
        )


def test_task_kind_requires_task() -> None:
    with pytest.raises(ContractError, match="required for kind"):
        EventV1(id="evt-1", kind="task.completed", **BASE)


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ContractError, match="is not one of"):
        EventV1(id="evt-1", kind="tool.pretty", **BASE)


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ContractError, match="no UTC offset"):
        EventV1(
            id="evt-1",
            kind="session.start",
            at="2026-11-24T12:00:00",
            adapter="test",
            session=SessionRef(id="sess-1", cwd="/tmp"),
        )


def test_unknown_role_is_rejected() -> None:
    with pytest.raises(ContractError, match="actor.role"):
        ActorRef(role="captain")


def test_sub_dispatch_is_preserved() -> None:
    event = EventV1(
        id="evt-1",
        kind="tool.pre",
        tool=ToolRef(
            canonical="Edit",
            native="edit",
            input={"file_path": "/tmp/a.py"},
            call_id="call-1",
            parent_call_id="call-0",
            is_sub_dispatch=True,
        ),
        **BASE,
    )
    wire = event.to_dict()
    assert wire["tool"]["is_sub_dispatch"] is True
    assert EventV1.from_dict(wire) == event


def test_payload_helper_returns_a_copy() -> None:
    event = make_event("session.start")
    enriched = event.with_payload(source="resume")
    assert enriched.payload == {"source": "resume"}
    assert event.payload == {}
