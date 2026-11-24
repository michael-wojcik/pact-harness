"""The journal backend seam: namespacing, append, read, and the MCP tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from pact_harness import paths
from pact_harness.journal import (
    CliJournalBackend,
    JournalBackendError,
    sanitize_session_id,
)
from pact_harness.mcp.tools import ToolOptions, call_tool


def plugin_root() -> Path:
    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    candidate = paths.resolve_plugin_root(env={}, harness_root=root)
    if candidate is None or not candidate.is_dir():
        pytest.skip("vendored submodule not checked out")
    return candidate


class StubJournal:
    def __init__(self) -> None:
        self.appended: list[tuple[str, Mapping[str, Any] | None, str]] = []
        self.reads: list[tuple[str, str | None, str | None]] = []

    def append(self, event_type: str, data: Mapping[str, Any] | None, session_id: str) -> dict[str, Any]:
        self.appended.append((event_type, data, session_id))
        return {"ok": True, "type": event_type}

    def read(self, session_id: str, *, event_type: str | None = None, since: str | None = None):
        self.reads.append((session_id, event_type, since))
        return [{"type": "dispatch_decision", "decision": "allow"}]


class FailingJournal(StubJournal):
    def append(self, event_type, data, session_id):
        raise JournalBackendError("session_journal: invalid event schema (missing required field)")


def test_session_ids_are_sanitised_for_paths() -> None:
    assert sanitize_session_id("abc-123_XYZ") == "abc-123_XYZ"
    assert sanitize_session_id("../../etc/passwd") == "etc_passwd"
    assert sanitize_session_id("") == "mcp"
    assert len(sanitize_session_id("x" * 200)) == 64


def test_the_journal_lives_in_a_harness_namespace(tmp_path: Path) -> None:
    backend = CliJournalBackend(plugin_root=plugin_root(), state_root=tmp_path)
    directory = backend.session_dir("sess one")
    assert directory == tmp_path / "pact-harness" / "journal" / "sess_one"


def test_append_and_read_round_trip(tmp_path: Path) -> None:
    backend = CliJournalBackend(plugin_root=plugin_root(), state_root=tmp_path)
    result = backend.append("dispatch_decision", {"decision": "allow"}, "mcp")
    assert result["ok"] is True
    events = backend.read("mcp")
    assert len(events) == 1
    assert events[0]["type"] == "dispatch_decision"
    assert events[0]["decision"] == "allow"
    # The session id was injected so the event satisfies PACT's own schema.
    assert events[0]["session_id"] == "mcp"


def test_read_filters_by_type(tmp_path: Path) -> None:
    backend = CliJournalBackend(plugin_root=plugin_root(), state_root=tmp_path)
    backend.append("dispatch_decision", {"decision": "allow"}, "mcp")
    assert len(backend.read("mcp", event_type="dispatch_decision")) == 1
    assert backend.read("mcp", event_type="session_start") == []


def test_a_refused_event_surfaces_the_cli_reason(tmp_path: Path) -> None:
    backend = CliJournalBackend(plugin_root=plugin_root(), state_root=tmp_path)
    with pytest.raises(JournalBackendError, match="session_start|invalid event schema"):
        backend.append("session_start", {}, "mcp")


def test_mcp_journal_append_uses_the_backend() -> None:
    stub = StubJournal()
    result = call_tool(
        "pact_journal_append",
        {"type": "dispatch_decision", "data": {"decision": "allow"}, "session_id": "s1"},
        ToolOptions(journal=stub),
    )
    assert result["isError"] is False
    assert stub.appended == [("dispatch_decision", {"decision": "allow"}, "s1")]
    assert json.loads(result["content"][0]["text"])["ok"] is True


def test_mcp_journal_append_defaults_the_namespace() -> None:
    stub = StubJournal()
    call_tool("pact_journal_append", {"type": "dispatch_decision"}, ToolOptions(journal=stub))
    assert stub.appended[0][2] == "mcp"


def test_mcp_journal_read_passes_the_filters() -> None:
    stub = StubJournal()
    result = call_tool(
        "pact_journal_read",
        {"type": "dispatch_decision", "since": "2026-01-01T00:00:00Z"},
        ToolOptions(journal=stub),
    )
    assert result["isError"] is False
    assert stub.reads == [("mcp", "dispatch_decision", "2026-01-01T00:00:00Z")]
    assert json.loads(result["content"][0]["text"])[0]["decision"] == "allow"


def test_mcp_journal_append_requires_a_type() -> None:
    result = call_tool("pact_journal_append", {}, ToolOptions(journal=StubJournal()))
    assert result["isError"] is True
    assert "type must be" in result["content"][0]["text"]


def test_a_backend_refusal_becomes_a_tool_error() -> None:
    result = call_tool(
        "pact_journal_append",
        {"type": "session_start"},
        ToolOptions(journal=FailingJournal()),
    )
    assert result["isError"] is True
    assert "invalid event schema" in result["content"][0]["text"]
