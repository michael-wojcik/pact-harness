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
]


@dataclass(frozen=True, slots=True)
class ToolOptions:
    """Where the tool implementations find the tree and state."""

    adapter_id: str = "mcp"
    plugin_root: Path | None = None
    state_root: Path | None = None
    python: str | None = None


def _now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat()


def _event_id(seed: str) -> str:
    return "evt_mcp_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _text(value: str, *, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": value}], "isError": is_error}


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
