"""The Claude Code adapter.

Claude Code is the platform PACT was written for, so this adapter is close to
an identity map. Its raw frame is preserved on the event and re-serialised for
the child: the hooks parse JSON, so semantic identity is what they depend on,
and keeping the original frame means a field this adapter does not understand
still reaches the hook that does.

The adapter claims the full capability set because it is the reference platform
-- PACT runs natively here today. The harness still reports what it can prove;
claiming T3 is not a claim about this build's testing, it is a statement that
the platform supplies every extension point.
"""

from __future__ import annotations

import datetime as _datetime
import hashlib
import json
from typing import Any, Mapping, Sequence

from ..capabilities import Capability, CapabilitySet
from ..contract import ActorRef, Directive, EventV1, SessionRef, TaskRef, TeamRef, ToolRef
from ..runtime.env import HookEnv, build_hook_env
from ..runtime.vocabulary import canonicalize, inject_target_for
from .base import AdapterContext, LegacyFrame

#: Claude's hook event names, mapped to harness kinds.
_EVENT_KIND: dict[str, str] = {
    "SessionStart": "session.start",
    "SessionEnd": "session.end",
    "UserPromptSubmit": "prompt.submit",
    "PreToolUse": "tool.pre",
    "PostToolUse": "tool.post",
    "PostToolUseFailure": "tool.fail",
    "SubagentStart": "agent.start",
    "SubagentStop": "agent.stop",
    "TaskCompleted": "task.completed",
    "PreCompact": "compact.pre",
    "PostCompact": "compact.post",
    "Stop": "turn.stop",
    "TeammateIdle": "agent.idle",
}

#: The exact agent_type values that mean "team lead".
LEAD_AGENT_TYPES: frozenset[str] = frozenset(
    {"PACT:pact-orchestrator", "pact-orchestrator"}
)

#: Frame fields copied into the event payload untouched.
_PAYLOAD_FIELDS = (
    "source",
    "compact_summary",
    "last_assistant_message",
    "background_tasks",
    "session_crons",
    "stop_hook_active",
    "prompt",
)

_ID_ALLOWED = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


def sanitize_id(value: str, fallback: str = "unknown") -> str:
    """Force a platform identifier into the contract's id alphabet."""
    text = "".join(character if character in _ID_ALLOWED else "_" for character in value)
    if not text:
        text = fallback
    if text[0] not in _ID_ALLOWED or not text[0].isalnum():
        text = "x" + text
    return text[:64]


def _now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat()


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _object(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


class ClaudeCodeAdapter:
    """Translate Claude Code hook frames into the harness contract."""

    id = "claude-code"

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
                Capability.NATIVE_TRANSCRIPT,
                Capability.NATIVE_INSTRUCTION_FILE,
            }
        )
    )

    # -- normalize --------------------------------------------------------

    def normalize(self, native: Mapping[str, Any]) -> EventV1:
        """Turn one Claude Code hook frame into an event."""
        frame = dict(native)
        hook_event = _string(frame.get("hook_event_name"))
        if hook_event is None or hook_event not in _EVENT_KIND:
            raise ValueError(f"unknown Claude Code hook_event_name: {hook_event!r}")

        tool_name = _string(frame.get("tool_name"))
        kind = _EVENT_KIND[hook_event]
        payload: dict[str, Any] = {}
        tool: ToolRef | None = None

        if kind == "tool.pre" and tool_name == "Agent":
            # A spawn request is its own kind: the gates distinguish "delegate
            # to a specialist" from "run a tool", and collapsing them would
            # apply tool gates to delegation.
            kind = "agent.spawn.pre"
            tool_input = _object(frame.get("tool_input")) or {}
            for field in ("subagent_type", "name", "prompt", "description", "run_in_background"):
                if field in tool_input:
                    payload[field] = tool_input[field]
        elif kind in ("tool.pre", "tool.post", "tool.fail"):
            tool_input = _object(frame.get("tool_input")) or {}
            tool = ToolRef(
                canonical=canonicalize(tool_name) or "Unknown",
                native=tool_name or "Unknown",
                input=tool_input,
                result=_object(frame.get("tool_response")),
                error=_object(frame.get("error")),
                call_id=sanitize_id(frame["tool_use_id"]) if _string(frame.get("tool_use_id")) else None,
            )

        for field in _PAYLOAD_FIELDS:
            if field in frame:
                payload[field] = frame[field]

        session = SessionRef(
            id=sanitize_id(_string(frame.get("session_id")) or "session"),
            cwd=_string(frame.get("cwd")) or ".",
            project_dir=_string(frame.get("project_dir")),
            transcript_path=_string(frame.get("transcript_path")),
        )

        agent_type = _string(frame.get("agent_type"))
        if agent_type in LEAD_AGENT_TYPES:
            role = "lead"
        elif agent_type:
            role = "teammate"
        else:
            role = "unknown"
        actor = ActorRef(
            role=role,
            name=_string(frame.get("agent_name")),
            id=sanitize_id(frame["agent_id"]) if _string(frame.get("agent_id")) else None,
            type=agent_type,
        )

        team = TeamRef(name=_string(frame.get("team_name"))) if _string(frame.get("team_name")) else None

        task: TaskRef | None = None
        task_id = _string(frame.get("task_id"))
        if kind == "task.completed":
            task = TaskRef(
                id=task_id or "task",
                subject=_string(frame.get("task_subject")),
                team_name=_string(frame.get("team_name")),
                teammate_name=_string(frame.get("teammate_name")),
                status=_string(frame.get("task_status")),
                owner=_string(frame.get("task_owner")),
            )

        return EventV1(
            id=self._event_id(frame, kind, session.id),
            kind=kind,
            at=_now(),
            adapter=self.id,
            session=session,
            actor=actor,
            team=team,
            tool=tool,
            task=task,
            payload=payload,
            raw=frame,
        )

    @staticmethod
    def _event_id(frame: Mapping[str, Any], kind: str, session_id: str) -> str:
        for field in ("tool_use_id", "prompt_id", "agent_id"):
            value = _string(frame.get(field))
            if value:
                return sanitize_id(value)
        digest = hashlib.sha256(
            json.dumps(
                [session_id, kind, frame.get("tool_name"), frame.get("tool_input")],
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:16]
        return "evt_" + digest

    # -- legacy ABI -------------------------------------------------------

    def legacy_stdin(self, event: EventV1) -> LegacyFrame:
        """Re-emit the frame the hooks expect.

        The raw frame is used whenever it is present. A synthetic event (one
        built by a test or another adapter) falls back to synthesising the
        fields the hooks read from the normalised event.
        """
        if event.raw:
            data = json.dumps(event.raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            return LegacyFrame(data=data, source="raw-frame")
        frame: dict[str, Any] = {
            "hook_event_name": _KIND_TO_EVENT[event.kind],
            "session_id": event.session.id,
            "cwd": event.session.cwd,
            "agent_type": event.actor.type,
            "agent_name": event.actor.name,
            "agent_id": event.actor.id,
            "team_name": event.team.name if event.team else None,
            "transcript_path": event.session.transcript_path,
        }
        if event.tool is not None:
            frame["tool_name"] = event.tool.native
            frame["tool_input"] = dict(event.tool.input)
            if event.tool.result is not None:
                frame["tool_response"] = dict(event.tool.result)
            if event.tool.error is not None:
                frame["error"] = dict(event.tool.error)
            frame["tool_use_id"] = event.tool.call_id
        if event.task is not None:
            frame["task_id"] = event.task.id
            frame["task_subject"] = event.task.subject
            frame["team_name"] = event.task.team_name
            frame["teammate_name"] = event.task.teammate_name
        frame.update(dict(event.payload))
        clean = {key: value for key, value in frame.items() if value is not None}
        data = json.dumps(clean, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return LegacyFrame(data=data, source="synthesized")

    def hook_env(self, event: EventV1, context: AdapterContext) -> HookEnv:
        """Build the child environment for one hook invocation."""
        raw_session = _string((event.raw or {}).get("session_id")) or event.session.id
        project_dir = _string((event.raw or {}).get("cwd")) or event.session.cwd
        return build_hook_env(
            state_root=context.state_root,
            plugin_root=context.plugin_root,
            session_id=raw_session,
            project_dir=project_dir,
            harness_pid=context.harness_pid,
            base_env=context.env_values(),
        )

    def render(self, directives: Sequence[Directive], event: EventV1) -> list[Directive]:
        """Claude Code accepts the decoded directives unchanged."""
        return list(directives)


#: The inverse mapping, for synthesising a frame from an event.
_KIND_TO_EVENT: dict[str, str] = {
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
