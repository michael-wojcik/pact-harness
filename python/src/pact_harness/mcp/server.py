"""The MCP server: newline-delimited JSON-RPC on stdio.

The transport is deliberately dumb. One JSON document per line, one response
per request, and no attempt to be clever about batching: a malformed line is
answered with a parse error and the loop continues. An MCP client that sends one
bad message should not take the server down with it.

The server is also deliberately narrow. It implements initialize, ping,
tools/list and tools/call, because those are what an advisory platform needs.
Anything else is a method-not-found error rather than a guessed behaviour.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, IO, Mapping

from .. import __version__
from .tools import TOOL_DEFINITIONS, ToolOptions, call_tool

#: The protocol version this server speaks when the client names nothing better.
DEFAULT_PROTOCOL_VERSION = "2024-11-05"

#: Versions accepted from a client. An unknown one falls back to the default.
SUPPORTED_PROTOCOL_VERSIONS = frozenset(
    {"2024-11-05", "2025-03-26", "2025-06-18"}
)

SERVER_NAME = "pact-harness"


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result(request_id: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": dict(payload)}


def handle_message(payload: Any, *, options: ToolOptions | None = None) -> dict[str, Any] | None:
    """Handle one message with a throwaway server (the common test path)."""
    return McpServer(options=options or ToolOptions()).handle(payload)


@dataclass
class McpServer:
    """One server session."""

    options: ToolOptions = field(default_factory=ToolOptions)
    initialized: bool = False

    def handle(self, payload: Any) -> dict[str, Any] | None:
        """Handle one decoded message; None means "no response"."""
        if not isinstance(payload, Mapping):
            return _error(None, -32600, "Invalid Request: expected a JSON object")
        method = payload.get("method")
        if not isinstance(method, str):
            return _error(payload.get("id"), -32600, "Invalid Request: no method")
        request_id = payload.get("id")
        is_request = "id" in payload and request_id is not None

        if method == "initialize":
            params = payload.get("params")
            requested = params.get("protocolVersion") if isinstance(params, Mapping) else None
            version = (
                requested
                if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS
                else DEFAULT_PROTOCOL_VERSION
            )
            self.initialized = True
            return _result(
                request_id,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": __version__},
                    "instructions": (
                        "PACT is advisory on this platform: the tools report what PACT "
                        "thinks, and nothing here can veto a tool call."
                    ),
                },
            )
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return _result(request_id, {})
        if method == "tools/list":
            return _result(request_id, {"tools": TOOL_DEFINITIONS})
        if method == "tools/call":
            params = payload.get("params")
            if not isinstance(params, Mapping):
                return _error(request_id, -32602, "Invalid params: expected an object")
            name = params.get("name")
            if not isinstance(name, str):
                return _error(request_id, -32602, "Invalid params: name is required")
            arguments = params.get("arguments")
            if arguments is not None and not isinstance(arguments, Mapping):
                return _error(request_id, -32602, "Invalid params: arguments must be an object")
            return _result(request_id, call_tool(name, arguments, self.options))
        if not is_request:
            # A notification the server does not implement is ignored, as the
            # protocol requires: there is no id to answer.
            return None
        return _error(request_id, -32601, f"Method not found: {method}")

    def serve(self, stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> int:
        """Read messages until end of input."""
        source = stdin if stdin is not None else sys.stdin
        sink = stdout if stdout is not None else sys.stdout
        for raw in source:
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                response: dict[str, Any] | None = _error(
                    None, -32700, f"Parse error: {exc.msg}"
                )
            else:
                response = self.handle(payload)
            if response is None:
                continue
            sink.write(json.dumps(response, ensure_ascii=False) + "\n")
            sink.flush()
        return 0
