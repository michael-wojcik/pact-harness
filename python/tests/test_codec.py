"""Contract codec: bounded, strict, and lossless."""

from __future__ import annotations

import pytest

from pact_harness.contract import ActorRef, EventV1, SessionRef, dumps_event, loads_event
from pact_harness.contract import codec
from pact_harness.errors import ContractError


def sample() -> EventV1:
    return EventV1(
        id="evt-1",
        kind="session.start",
        at="2026-11-24T12:00:00Z",
        adapter="test",
        session=SessionRef(id="sess-1", cwd="/tmp"),
        actor=ActorRef(role="lead", name="lead"),
        payload={"source": "startup"},
    )


def test_round_trip_through_json_text() -> None:
    event = sample()
    assert loads_event(dumps_event(event)) == event


def test_accepts_bytes() -> None:
    event = sample()
    assert loads_event(dumps_event(event).encode("utf-8")) == event


def test_invalid_json_names_the_document() -> None:
    with pytest.raises(ContractError, match="event: invalid JSON"):
        loads_event("{not json")


def test_invalid_utf8_is_rejected() -> None:
    with pytest.raises(ContractError, match="not valid UTF-8"):
        loads_event(b"\xff\xfe")


def test_oversized_document_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codec, "MAX_DOCUMENT_BYTES", 8)
    with pytest.raises(ContractError, match="exceeds"):
        loads_event(dumps_event(sample()))
