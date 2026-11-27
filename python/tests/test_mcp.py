"""The MCP adapter: protocol handling, tools, and the honest advisory claim."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pact_harness import paths
from pact_harness.mcp.server import McpServer
from pact_harness.mcp.tools import TOOL_DEFINITIONS, ToolOptions, call_tool


def plugin_root() -> Path:
    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    candidate = paths.resolve_plugin_root(env={}, harness_root=root)
    if candidate is None or not candidate.is_dir():
        pytest.skip("vendored submodule not checked out")
    return candidate


@pytest.fixture()
def options(tmp_path: Path) -> ToolOptions:
    return ToolOptions(adapter_id="mcp", plugin_root=plugin_root(), state_root=tmp_path / "state")


@pytest.fixture()
def server(options: ToolOptions) -> McpServer:
    return McpServer(options=options)


def test_initialize_returns_the_default_version(server: McpServer) -> None:
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert response["id"] == 1
    result = response["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "pact-harness"
    assert "tools" in result["capabilities"]
    assert "advisory" in result["instructions"]


def test_initialize_echoes_a_supported_version(server: McpServer) -> None:
    response = server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}
    )
    assert response["result"]["protocolVersion"] == "2025-06-18"


def test_initialize_falls_back_on_an_unknown_version(server: McpServer) -> None:
    response = server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}
    )
    assert response["result"]["protocolVersion"] == "2024-11-05"


def test_notifications_get_no_response(server: McpServer) -> None:
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/something-else"}) is None


def test_ping(server: McpServer) -> None:
    assert server.handle({"jsonrpc": "2.0", "id": 2, "method": "ping"})["result"] == {}


def test_tools_are_listed_with_schemas(server: McpServer) -> None:
    response = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    tools = response["result"]["tools"]
    assert {tool["name"] for tool in tools} == {
        "pact_event",
        "pact_gate",
        "pact_journal_append",
        "pact_journal_read",
        "pact_memory_save",
        "pact_memory_search",
        "pact_memory_status",
        "pact_status",
    }
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
    assert TOOL_DEFINITIONS == tools
    assert len(tools) == 8


def test_status_reports_t0_and_the_registry(server: McpServer, pin) -> None:
    response = server.handle(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "pact_status", "arguments": {}}}
    )
    result = response["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["adapter"] == "mcp"
    assert payload["tier"] == "T0"
    assert payload["advisory"] is True
    assert payload["hook_registry"]["registrations"] == 34
    assert payload["pact"]["version"] == pin.pact_version


def test_event_evaluates_a_real_frame(server: McpServer) -> None:
    event = {
        "v": 1,
        "id": "evt_mcp_test",
        "kind": "tool.pre",
        "at": "2026-11-24T12:00:00Z",
        "adapter": "mcp",
        "session": {"id": "sess-mcp", "cwd": str(Path.cwd())},
        "actor": {"role": "unknown"},
        "tool": {"canonical": "Read", "native": "read", "input": {"file_path": "/tmp/x"}},
    }
    response = server.handle(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "pact_event", "arguments": {"event": event}}}
    )
    result = response["result"]
    assert result["isError"] is False
    envelope = json.loads(result["content"][0]["text"])
    assert envelope["v"] == 1
    assert envelope["meta"]["adapter"] == "mcp"
    assert envelope["directives"]


def test_gate_refuses_a_force_push_and_says_it_is_advisory(server: McpServer) -> None:
    root = paths.find_harness_root()
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "pact_gate",
                "arguments": {
                    "tool": {"canonical": "Bash", "native": "bash", "input": {"command": "git push --force origin main"}},
                    "session": {"id": "sess-mcp", "cwd": str(root)},
                },
            },
        }
    )
    text = response["result"]["content"][0]["text"]
    assert text.startswith("DENY (merge_guard_pre)")
    assert "Advisory only" in text


def test_an_unknown_tool_is_an_error_result(server: McpServer) -> None:
    response = server.handle(
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "nope", "arguments": {}}}
    )
    assert response["result"]["isError"] is True
    assert "unknown tool" in response["result"]["content"][0]["text"]


def test_invalid_params_are_a_protocol_error(server: McpServer) -> None:
    response = server.handle({"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": "nope"})
    assert response["error"]["code"] == -32602


def test_an_unknown_method_is_a_protocol_error(server: McpServer) -> None:
    response = server.handle({"jsonrpc": "2.0", "id": 9, "method": "resources/list"})
    assert response["error"]["code"] == -32601


def test_the_serve_loop_answers_requests_and_survives_bad_input(server: McpServer) -> None:
    source = io.StringIO(
        "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                "{not json",
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            ]
        )
        + "\n"
    )
    sink = io.StringIO()
    assert server.serve(source, sink) == 0
    lines = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert len(lines) == 3
    assert lines[0]["result"]["serverInfo"]["name"] == "pact-harness"
    assert lines[1]["error"]["code"] == -32700
    assert len(lines[2]["result"]["tools"]) == 8


def test_the_server_runs_as_a_process(tmp_path: Path) -> None:
    root = plugin_root()
    env = dict(os.environ)
    src = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pact_harness",
            "mcp",
            "serve",
            "--plugin-root",
            str(root),
            "--state-root",
            str(tmp_path / "state"),
        ],
        input="\n".join(json.dumps(message) for message in messages) + "\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    assert responses[0]["result"]["protocolVersion"] == "2024-11-05"
    assert len(responses[1]["result"]["tools"]) == 8
