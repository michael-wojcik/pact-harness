"""The memory backend seam: location reporting, and the MCP tools over it."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

import pytest

from pact_harness import paths
from pact_harness.memory import CliMemoryBackend, MemoryBackendError, MemoryStatus
from pact_harness.mcp.tools import ToolOptions, call_tool


def plugin_root() -> Path:
    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    candidate = paths.resolve_plugin_root(env={}, harness_root=root)
    if candidate is None or not candidate.is_dir():
        pytest.skip("vendored submodule not checked out")
    return candidate


class StubBackend:
    def __init__(self) -> None:
        self.saved: list[Mapping[str, Any]] = []
        self.searched: list[tuple[str, int]] = []

    def status(self) -> MemoryStatus:
        return MemoryStatus(
            backend="stub",
            location="/tmp/stub-memory",
            exists=True,
            size_bytes=42,
            detail="stub store",
        )

    def save(self, memory: Mapping[str, Any]) -> dict[str, Any]:
        self.saved.append(memory)
        return {"ok": True, "id": "mem-1"}

    def search(self, query: str, limit: int = 10) -> dict[str, Any]:
        self.searched.append((query, limit))
        return {"ok": True, "results": [{"id": "mem-1", "score": 0.9}]}


class FailingBackend(StubBackend):
    def save(self, memory: Mapping[str, Any]) -> dict[str, Any]:
        raise MemoryBackendError("BACKEND_DOWN: cannot reach the store")


def test_the_backend_resolves_the_pinned_location(tmp_path: Path) -> None:
    backend = CliMemoryBackend(
        plugin_root=plugin_root(),
        env={"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )
    status = backend.status()
    assert status.backend == "pact-memory-cli"
    assert status.location == str(tmp_path / ".claude" / "pact-memory")
    assert status.exists is False
    assert status.size_bytes is None
    assert "no store yet" in status.detail


def test_status_does_not_open_the_store(tmp_path: Path) -> None:
    # An empty file called memory.db is enough for the existence probe. Opening
    # it as SQLite would either fail or create a journal beside it, and the
    # status command must not change what it measures.
    location = tmp_path / ".claude" / "pact-memory"
    location.mkdir(parents=True)
    (location / "memory.db").write_bytes(b"")
    backend = CliMemoryBackend(
        plugin_root=plugin_root(),
        env={"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )
    status = backend.status()
    assert status.exists is True
    assert status.size_bytes == 0
    assert sorted(path.name for path in location.iterdir()) == ["memory.db"]


def test_mcp_memory_status_uses_the_backend() -> None:
    stub = StubBackend()
    result = call_tool("pact_memory_status", {}, ToolOptions(memory=stub))
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["backend"] == "stub"
    assert payload["exists"] is True
    assert payload["size_bytes"] == 42


def test_mcp_memory_save_passes_the_object_through() -> None:
    stub = StubBackend()
    memory = {"type": "lesson", "content": "prefer absolute paths"}
    result = call_tool("pact_memory_save", {"memory": memory}, ToolOptions(memory=stub))
    assert result["isError"] is False
    assert stub.saved == [memory]
    assert json.loads(result["content"][0]["text"])["id"] == "mem-1"


def test_mcp_memory_search_passes_the_query_and_limit() -> None:
    stub = StubBackend()
    result = call_tool(
        "pact_memory_search", {"query": "pool leak", "limit": 3}, ToolOptions(memory=stub)
    )
    assert result["isError"] is False
    assert stub.searched == [("pool leak", 3)]


def test_mcp_memory_search_rejects_a_bad_limit() -> None:
    stub = StubBackend()
    result = call_tool(
        "pact_memory_search", {"query": "x", "limit": 0}, ToolOptions(memory=stub)
    )
    assert result["isError"] is True
    assert "limit must be" in result["content"][0]["text"]


def test_mcp_memory_save_requires_an_object() -> None:
    result = call_tool("pact_memory_save", {"memory": "nope"}, ToolOptions(memory=StubBackend()))
    assert result["isError"] is True


def test_a_backend_failure_becomes_a_tool_error() -> None:
    result = call_tool(
        "pact_memory_save",
        {"memory": {"content": "x"}},
        ToolOptions(memory=FailingBackend()),
    )
    assert result["isError"] is True
    assert "BACKEND_DOWN" in result["content"][0]["text"]


def test_the_cli_reports_the_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pact_harness import cli

    monkeypatch.setenv("HOME", str(tmp_path))
    code = cli.main(["memory", "status", "--json", "--plugin-root", str(plugin_root())])
    assert code == 0
