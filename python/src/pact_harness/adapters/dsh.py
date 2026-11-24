"""The DeepSeek Harness adapter, Python side.

The bridge in adapters/dsh/ is a small cordis plugin that normalises DSH's
extension points into pact.event.v1 and calls this process. So by the time an
event reaches this adapter it is already normalised: normalising again would
mean two implementations of the same mapping, which is how the two drift.

What is left here is the part only Python can do: reconstruct the Claude-shaped
frame the vendored hooks read, and build their environment. The frame is
synthesised rather than passed through, so every field it carries has to be
named deliberately -- that is the whole risk of a non-native platform, and it
is why the mapping is a table instead of a pile of getattr calls.

Agent type is the load-bearing field. The hooks decide lead versus teammate by
membership in a two-element set, so a lead must arrive as pact-orchestrator and
a teammate must not.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from ..capabilities import Capability, CapabilitySet
from ..contract import Directive, EventV1
from ..runtime.env import HookEnv, build_hook_env
from .base import AdapterContext, LegacyFrame

#: The agent_type a lead must present to the vendored hooks.
LEAD_AGENT_TYPE = "pact-orchestrator"

#: Fields copied straight from the event payload into the legacy frame.
_PAYLOAD_FIELDS = (
    "source",
    "compact_summary",
    "last_assistant_message",
    "background_tasks",
    "session_crons",
    "stop_hook_active",
    "prompt",
    "subagent_type",
    "name",
    "description",
    "run_in_background",
)


class DshAdapter:
    """Turn an already-normalised DSH event into a hook invocation."""

    id = "dsh"

    capabilities = CapabilitySet(
        frozenset(
            {
                Capability.EVENT_SESSION_START,
                Capability.EVENT_SESSION_END,
                Capability.EVENT_PROMPT_SUBMIT,
                Capability.EVENT_TOOL_PRE,
                Capability.EVENT_TOOL_POST,
                Capability.EVENT_TOOL_FAIL,
                Capability.EVENT_AGENT_SPAWN_PRE,
                Capability.EVENT_AGENT_START,
                Capability.EVENT_AGENT_STOP,
                Capability.EVENT_AGENT_IDLE,
                Capability.EVENT_TASK_COMPLETED,
                Capability.EVENT_COMPACT_PRE,
                Capability.EVENT_COMPACT_POST,
                Capability.EVENT_TURN_STOP,
                Capability.CONTROL_TOOL_VETO,
                Capability.CONTROL_TOOL_ASK,
                Capability.CONTROL_TURN_BLOCK,
                Capability.UI_CONTEXT_INJECT,
                Capability.UI_NOTIFY,
                Capability.UI_COMPACT_INSTRUCTIONS,
                Capability.NATIVE_TASK_BOARD,
                Capability.NATIVE_MESSAGING,
                Capability.NATIVE_SKILLS,
                Capability.NATIVE_COMMANDS,
                Capability.NATIVE_SUBAGENTS_NAMED,
            }
        )
    )

    def normalize(self, native: Mapping[str, Any]) -> EventV1:
        """The bridge already normalised; decode and validate the contract."""
        return EventV1.from_dict(dict(native))

    def legacy_stdin(self, event: EventV1) -> LegacyFrame:
        """Synthesise the frame the vendored hooks read."""
        frame: dict[str, Any] = {
            "hook_event_name": _KIND_TO_MANIFEST[event.kind],
            "session_id": event.session.id,
            "cwd": event.session.cwd,
            "project_dir": event.session.project_dir,
            "transcript_path": event.session.transcript_path,
            "team_name": event.team.name if event.team else None,
            "agent_type": _agent_type_for(event),
            "agent_name": event.actor.name,
            "agent_id": event.actor.id,
        }

        if event.kind == "agent.spawn.pre":
            frame["tool_name"] = "Agent"
            frame["tool_input"] = {
                key: event.payload[key]
                for key in ("subagent_type", "name", "prompt", "description", "run_in_background")
                if key in event.payload
            }
        elif event.tool is not None:
            frame["tool_name"] = event.tool.canonical
            frame["tool_input"] = dict(event.tool.input)
            if event.tool.result is not None:
                frame["tool_response"] = dict(event.tool.result)
            if event.tool.error is not None:
                frame["error"] = dict(event.tool.error)
            frame["tool_use_id"] = event.tool.call_id

        if event.task is not None:
            frame["task_id"] = event.task.id
            frame["task_subject"] = event.task.subject
            frame["team_name"] = event.task.team_name or frame["team_name"]
            frame["teammate_name"] = event.task.teammate_name
            frame["task_status"] = event.task.status
            frame["task_owner"] = event.task.owner

        for field in _PAYLOAD_FIELDS:
            if field in event.payload:
                frame[field] = event.payload[field]

        clean = {key: value for key, value in frame.items() if value is not None}
        data = json.dumps(clean, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return LegacyFrame(data=data, source="dsh-synthesized")

    def hook_env(self, event: EventV1, context: AdapterContext) -> HookEnv:
        """Build the child environment for one hook invocation."""
        return build_hook_env(
            state_root=context.state_root,
            plugin_root=context.plugin_root,
            session_id=event.session.id,
            project_dir=event.session.project_dir or event.session.cwd,
            harness_pid=context.harness_pid,
            base_env=context.env_values(),
        )

    def render(self, directives: Sequence[Directive], event: EventV1) -> list[Directive]:
        """The bridge renders directives into DSH decisions; nothing to adjust."""
        return list(directives)


def _agent_type_for(event: EventV1) -> str | None:
    """Map the harness role onto the string the hooks classify lead by."""
    if event.actor.role == "lead":
        return LEAD_AGENT_TYPE
    if event.actor.role == "teammate":
        return event.actor.type or event.actor.name or "pact-teammate"
    return None


_KIND_TO_MANIFEST: dict[str, str] = {
    "session.start": "SessionStart",
    "session.end": "SessionEnd",
    "prompt.submit": "UserPromptSubmit",
    "tool.pre": "PreToolUse",
    "tool.post": "PostToolUse",
    "tool.fail": "PostToolUseFailure",
    "agent.spawn.pre": "PreToolUse",
    "agent.start": "SubagentStart",
    "agent.stop": "SubagentStop",
    "agent.idle": "TeammateIdle",
    "task.completed": "TaskCompleted",
    "compact.pre": "PreCompact",
    "compact.post": "PostCompact",
    "turn.stop": "Stop",
}
