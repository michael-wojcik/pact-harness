"""An MCP server that lets a model ask PACT what it thinks.

Platforms with no hook surface cannot have PACT observe or veto anything, so
this adapter exposes the harness as tools the model calls itself. That is a
real limitation and the tools say so: a verdict returned here is advice the
model may follow, not a refusal the platform enforces.
"""

from __future__ import annotations

from .server import McpServer, handle_message
from .tools import TOOL_DEFINITIONS, call_tool

__all__ = ["McpServer", "TOOL_DEFINITIONS", "call_tool", "handle_message"]
