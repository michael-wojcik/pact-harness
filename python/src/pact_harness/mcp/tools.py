"""The tools the MCP adapter exposes.

Three tools, each answering a question the model can already ask itself and
would answer worse: what does PACT think about the next tool call, what does it
think about an event, and what is actually installed.

Every result is advisory. A platform that reaches PACT through this server has
no interception point, so the tool text says "advisory" rather than implying a
refusal the platform cannot enforce.
"""

from __future__ import annotations

import datetime as _datetime
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .. import CONTRACT_VERSION, __version__
from ..adapters.registry import get_adapter
from ..contract import EventV1
from ..engine import EngineConfig, config_for, evaluate, registry_for
from ..paths import find_harness_root, resolve_plugin_root, resolve_state_root
from ..journal import CliJournalBackend, JournalBackend
from ..memory import CliMemoryBackend, MemoryBackend
from ..vendor import VendorError, load_pin

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "pact_event",
        "description": (
            "Ask PACT to evaluate one pact.event.v1 event. Returns the directive "
            "envelope as JSON. Advisory: this platform cannot enforce a refusal."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["event"],
            "properties": {
                "event": {
                    "type": "object",
                    "description": "A pact.event.v1 document.",
                }
            },
        },
    },
    {
        "name": "pact_gate",
        "description": (
            "Ask PACT whether a tool call should proceed. Returns ALLOW, ASK or "
            "DENY with the gate's rule and reason, plus the envelope. Advisory: "
            "honour a DENY yourself; the platform will not enforce it."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["tool"],
            "properties": {
                "tool": {
                    "type": "object",
                    "required": ["canonical"],
                    "properties": {
                        "canonical": {"type": "string", "description": "Canonical tool name, e.g. Bash or Edit."},
                        "native": {"type": "string", "description": "The platform's own tool name."},
                        "input": {"type": "object", "description": "The call arguments."},
                    },
                },
                "session": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "cwd": {"type": "string"},
                        "project_dir": {"type": "string"},
                    },
                },
                "actor": {
                    "type": "object",
                    "properties": {
                        "role": {"enum": ["lead", "teammate", "unknown"]},
                        "name": {"type": "string"},
                    },
                },
            },
        },
    },
    {
        "name": "pact_status",
        "description": (
            "Report what PACT is actually wired into: adapter, tier, pinned "
            "version, hook registry size, and resolved paths."
        ),
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}},
    },
    {
        "name": "pact_journal_append",
        "description": (
            "Append one event to the harness-owned journal. The event type and its "
            "required fields belong to PACT; the backend reports what it refuses."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["type"],
            "properties": {
                "type": {"type": "string", "description": "PACT journal event type."},
                "data": {"type": "object", "description": "The event's fields."},
                "session_id": {
                    "type": "string",
                    "description": "Journal namespace; defaults to mcp.",
                },
            },
        },
    },
    {
        "name": "pact_journal_read",
        "description": "Read events from the harness-owned journal.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "type": {"type": "string", "description": "Only this event type."},
                "since": {"type": "string", "description": "Only events at or after this time."},
                "session_id": {
                    "type": "string",
                    "description": "Journal namespace; defaults to mcp.",
                },
            },
        },
    },
    {
        "name": "pact_memory_status",
        "description": (
            "Report the memory backend's location and whether a store exists. "
            "Does not open the store."
        ),
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}},
    },
    {
        "name": "pact_memory_save",
        "description": "Save one memory object through the PACT memory backend.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["memory"],
            "properties": {
                "memory": {
                    "type": "object",
                    "description": "A PACT memory object: context, goal, task, lesson or decision.",
                }
            },
        },
    },
    {
        "name": "pact_memory_search",
        "description": "Search PACT's memory through the backend.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "description": "Search text."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
            },
        },
    },
]


@dataclass(frozen=True, slots=True)
class ToolOptions:
    """Where the tool implementations find the tree and state."""

    adapter_id: str = "mcp"
    plugin_root: Path | None = None
    state_root: Path | None = None
    python: str | None = None
    memory: MemoryBackend | None = None
    journal: JournalBackend | None = None


def _now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat()


def _event_id(seed: str) -> str:
    return "evt_mcp_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _text(value: str, *, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": value}], "isError": is_error}


def _memory(options: ToolOptions) -> MemoryBackend:
    """Resolve the memory backend, defaulting to the vendor CLI."""
    if options.memory is not None:
        return options.memory
    plugin_root = options.plugin_root or resolve_plugin_root()
    if plugin_root is None:
        raise RuntimeError("plugin root unresolved; cannot reach the memory backend")
    return CliMemoryBackend(plugin_root=plugin_root, python=options.python or "")


def _journal(options: ToolOptions) -> JournalBackend:
    """Resolve the journal backend, defaulting to the vendor CLI."""
    if options.journal is not None:
        return options.journal
    plugin_root = options.plugin_root or resolve_plugin_root()
    if plugin_root is None:
        raise RuntimeError("plugin root unresolved; cannot reach the journal backend")
    state_root = options.state_root or resolve_state_root(adapter=options.adapter_id).path
    return CliJournalBackend(
        plugin_root=plugin_root, state_root=state_root, python=options.python or ""
    )


def _config(options: ToolOptions) -> EngineConfig:
    return config_for(
        adapter_id=options.adapter_id,
        plugin_root=options.plugin_root,
        state_root=options.state_root,
        python=options.python,
    )


def _evaluate_event(event: EventV1, options: ToolOptions) -> dict[str, Any]:
    config = _config(options)
    adapter = get_adapter(options.adapter_id)
    envelope = evaluate(event, config, adapter=adapter)
    return envelope.to_dict()


def _gate_event(arguments: Mapping[str, Any]) -> EventV1:
    tool = arguments.get("tool")
    if not isinstance(tool, dict) or not isinstance(tool.get("canonical"), str):
        raise ValueError("tool.canonical is required")
    session = arguments.get("session") if isinstance(arguments.get("session"), dict) else {}
    cwd = str(session.get("cwd") or os.getcwd())
    actor = arguments.get("actor") if isinstance(arguments.get("actor"), dict) else {}
    seed = json.dumps([tool.get("canonical"), tool.get("input"), cwd], sort_keys=True, default=str)
    return EventV1.from_dict(
        {
            "v": CONTRACT_VERSION,
            "id": _event_id(seed),
            "kind": "tool.pre",
            "at": _now(),
            "adapter": "mcp",
            "session": {
                "id": str(session.get("id") or "mcp-session"),
                "cwd": cwd,
                "project_dir": str(session.get("project_dir") or cwd),
            },
            "actor": {"role": str(actor.get("role") or "unknown"), **(
                {"name": str(actor["name"])} if actor.get("name") else {}
            )},
            "tool": {
                "canonical": tool["canonical"],
                "native": str(tool.get("native") or tool["canonical"]),
                "input": tool.get("input") if isinstance(tool.get("input"), dict) else {},
            },
        }
    )


def _verdict(envelope: Mapping[str, Any]) -> str:
    directives = envelope.get("directives") or []
    for directive in directives:
        if directive.get("kind") == "deny":
            rule = directive.get("rule") or "unknown gate"
            return f"DENY ({rule}): {directive.get('reason')}"
    for directive in directives:
        if directive.get("kind") == "ask":
            return f"ASK: {directive.get('reason') or 'approval required'}"
    return "ALLOW"


def _status(options: ToolOptions) -> dict[str, Any]:
    root = find_harness_root()
    plugin = options.plugin_root or resolve_plugin_root(harness_root=root)
    state = options.state_root or resolve_state_root(adapter=options.adapter_id).path
    adapter = get_adapter(options.adapter_id)
    payload: dict[str, Any] = {
        "harness": __version__,
        "contract": CONTRACT_VERSION,
        "adapter": options.adapter_id,
        "tier": adapter.capabilities.tier.value,
        "advisory": adapter.capabilities.tier.value == "T0",
        "plugin_root": str(plugin) if plugin else None,
        "state_root": str(state),
    }
    if plugin is not None:
        registry = registry_for(plugin)
        payload["hook_registry"] = {
            "registrations": len(registry),
            "events": len(registry.events),
            "modules": len(registry.modules()),
        }
    if root is not None:
        try:
            pin = load_pin(root)
            payload["pact"] = {"version": pin.pact_version, "commit": pin.commit}
        except VendorError:
            payload["pact"] = None
    return payload


def call_tool(name: str, arguments: Mapping[str, Any] | None, options: ToolOptions | None = None) -> dict[str, Any]:
    """Run one MCP tool, returning an MCP tool result."""
    resolved = options or ToolOptions()
    args = arguments if isinstance(arguments, Mapping) else {}
    try:
        if name == "pact_status":
            return _text(json.dumps(_status(resolved), indent=2, sort_keys=True))
        if name == "pact_journal_append":
            event_type = args.get("type")
            if not isinstance(event_type, str) or not event_type.strip():
                return _text("type must be a non-empty string", is_error=True)
            data = args.get("data")
            if data is not None and not isinstance(data, Mapping):
                return _text("data must be an object", is_error=True)
            session_id = str(args.get("session_id") or "mcp")
            return _text(
                json.dumps(
                    _journal(resolved).append(event_type, data, session_id),
                    indent=2,
                    sort_keys=True,
                )
            )
        if name == "pact_journal_read":
            event_type = args.get("type")
            if event_type is not None and not isinstance(event_type, str):
                return _text("type must be a string", is_error=True)
            since = args.get("since")
            if since is not None and not isinstance(since, str):
                return _text("since must be a string", is_error=True)
            session_id = str(args.get("session_id") or "mcp")
            events = _journal(resolved).read(session_id, event_type=event_type, since=since)
            return _text(json.dumps(events, indent=2, sort_keys=True))
        if name == "pact_memory_status":
            return _text(json.dumps(_memory(resolved).status().to_dict(), indent=2, sort_keys=True))
        if name == "pact_memory_save":
            memory = args.get("memory")
            if not isinstance(memory, Mapping):
                return _text("memory must be an object", is_error=True)
            return _text(json.dumps(_memory(resolved).save(memory), indent=2, sort_keys=True))
        if name == "pact_memory_search":
            query = args.get("query")
            if not isinstance(query, str) or not query.strip():
                return _text("query must be a non-empty string", is_error=True)
            limit = args.get("limit", 10)
            if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
                return _text("limit must be an integer from 1 to 100", is_error=True)
            return _text(
                json.dumps(_memory(resolved).search(query, limit), indent=2, sort_keys=True)
            )
        if name == "pact_event":
            event = args.get("event")
            if not isinstance(event, Mapping):
                return _text("event must be a pact.event.v1 object", is_error=True)
            envelope = _evaluate_event(EventV1.from_dict(dict(event)), resolved)
            return _text(json.dumps(envelope, indent=2, sort_keys=True))
        if name == "pact_gate":
            envelope = _evaluate_event(_gate_event(args), resolved)
            summary = _verdict(envelope)
            return _text(
                f"{summary}\n\nAdvisory only: this platform does not enforce PACT verdicts.\n\n"
                + json.dumps(envelope, indent=2, sort_keys=True)
            )
        return _text(f"unknown tool: {name}", is_error=True)
    except Exception as exc:  # noqa: BLE001 -- a tool failure is a result, not a crash
        return _text(f"pact {name} failed: {type(exc).__name__}: {exc}", is_error=True)
