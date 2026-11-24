"""Adapter discovery.

Detection follows the same rule as the rest of the harness: prefer an explicit
answer, then a platform marker, and never guess past a marker that contradicts
another. At M1 only the Claude Code adapter exists; naming an unimplemented
adapter produces a message rather than a silent fallback to a different one.
"""

from __future__ import annotations

import os
from typing import Mapping

from .base import PlatformAdapter


class NotImplementedAdapterError(NotImplementedError):
    """The adapter was named but is not part of this build."""


_IMPLEMENTED = ("claude-code", "dsh", "mcp")


def available_adapters() -> tuple[str, ...]:
    """Adapter ids this build implements."""
    return _IMPLEMENTED


def detect_adapter(env: Mapping[str, str] | None = None) -> str:
    """Guess the platform from the environment."""
    environ = os.environ if env is None else env
    explicit = (environ.get("PACT_ADAPTER") or "").strip()
    if explicit:
        return explicit
    if (environ.get("DSH_SESSION_ID") or "").strip() or (environ.get("DSH_PROFILE") or "").strip():
        return "dsh"
    if (environ.get("CLAUDE_PLUGIN_ROOT") or "").strip():
        return "claude-code"
    return "generic"


def get_adapter(adapter_id: str) -> PlatformAdapter:
    """Return the adapter for an id, refusing ids this build does not ship."""
    if adapter_id == "claude-code":
        from .claude_code import ClaudeCodeAdapter

        return ClaudeCodeAdapter()
    if adapter_id == "dsh":
        from .dsh import DshAdapter

        return DshAdapter()
    if adapter_id == "mcp":
        from .portable import PortableAdapter

        return PortableAdapter()
    raise NotImplementedAdapterError(
        f"adapter {adapter_id!r} is not implemented in this build; "
        f"available: {', '.join(_IMPLEMENTED)}"
    )
