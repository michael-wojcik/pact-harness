"""pact.event.v1: the normalised event a platform adapter hands to PACT.

One event kind maps to one platform occurrence. The fields a hook needs are
carried explicitly rather than inferred, because the runtime synthesises the
Claude-shaped stdin the vendored hooks read and must do so from this document
alone.

Kinds beyond the twelve Claude Code events
------------------------------------------
tool.fail exists because Claude fires PostToolUseFailure separately from
PostToolUse, and the two carry different payloads: a failure carries an error,
not a tool_response, and a hook that reads the wrong one sees an empty object.
Splitting it here keeps that distinction visible to every adapter.

Structural rules enforced at construction
-----------------------------------------
tool.pre, tool.post and tool.fail require a ToolRef and no TaskRef.
task.completed requires a TaskRef and no ToolRef.
Every other kind must carry neither. An adapter that attaches the wrong
subject fails loudly at the boundary instead of producing an event a gate
silently misreads.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from .. import CONTRACT_VERSION
from ..errors import ContractError
from ._util import (
    fail,
    optional_object,
    optional_str,
    parse_timestamp,
    reject_unknown,
    require_enum,
    require_object,
    require_str,
)

#: Every event kind in pact.event.v1.
EVENT_KINDS: frozenset[str] = frozenset(
    {
        "session.start",
        "session.end",
        "prompt.submit",
        "tool.pre",
        "tool.post",
        "tool.fail",
        "agent.spawn.pre",
        "agent.start",
        "agent.stop",
        "agent.idle",
        "task.completed",
        "compact.pre",
        "compact.post",
        "turn.stop",
    }
)

#: Kinds that carry a ToolRef.
TOOL_KINDS: frozenset[str] = frozenset({"tool.pre", "tool.post", "tool.fail"})

#: Kinds that carry a TaskRef.
TASK_KINDS: frozenset[str] = frozenset({"task.completed"})

#: The three roles the vendored hooks distinguish.
ACTOR_ROLES: frozenset[str] = frozenset({"lead", "teammate", "unknown"})

_ID_ALLOWED = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


def _validate_id(value: str, path: str) -> str:
    text = require_str(value, path)
    if len(text) > 64:
        raise fail(path, "expected at most 64 characters")
    bad = [character for character in text if character not in _ID_ALLOWED]
    if bad:
        raise fail(path, f"contains unsupported character(s) {bad!r}")
    return text


@dataclass(frozen=True, slots=True)
class SessionRef:
    """The session a platform event belongs to."""

    id: str
    cwd: str
    project_dir: str | None = None
    transcript_path: str | None = None

    def __post_init__(self) -> None:
        _validate_id(self.id, "session.id")
        require_str(self.cwd, "session.cwd")
        if self.project_dir is not None:
            require_str(self.project_dir, "session.project_dir")
        if self.transcript_path is not None:
            require_str(self.transcript_path, "session.transcript_path")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "cwd": self.cwd}
        if self.project_dir is not None:
            out["project_dir"] = self.project_dir
        if self.transcript_path is not None:
            out["transcript_path"] = self.transcript_path
        return out

    @classmethod
    def from_dict(cls, value: Any, path: str = "session") -> "SessionRef":
        obj = require_object(value, path)
        reject_unknown(
            obj, {"id", "cwd", "project_dir", "transcript_path"}, path
        )
        if "id" not in obj:
            raise fail(f"{path}.id", "is required")
        if "cwd" not in obj:
            raise fail(f"{path}.cwd", "is required")
        return cls(
            id=obj["id"],
            cwd=obj["cwd"],
            project_dir=optional_str(obj.get("project_dir"), f"{path}.project_dir"),
            transcript_path=optional_str(obj.get("transcript_path"), f"{path}.transcript_path"),
        )


@dataclass(frozen=True, slots=True)
class ActorRef:
    """Who caused the event, as far as the platform can say."""

    role: str = "unknown"
    name: str | None = None
    id: str | None = None
    type: str | None = None

    def __post_init__(self) -> None:
        require_enum(self.role, ACTOR_ROLES, "actor.role")
        if self.name is not None:
            require_str(self.name, "actor.name")
        if self.id is not None:
            _validate_id(self.id, "actor.id")
        if self.type is not None:
            require_str(self.type, "actor.type")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role}
        if self.name is not None:
            out["name"] = self.name
        if self.id is not None:
            out["id"] = self.id
        if self.type is not None:
            out["type"] = self.type
        return out

    @classmethod
    def from_dict(cls, value: Any, path: str = "actor") -> "ActorRef":
        obj = require_object(value, path)
        reject_unknown(obj, {"role", "name", "id", "type"}, path)
        return cls(
            role=obj.get("role", "unknown"),
            name=optional_str(obj.get("name"), f"{path}.name"),
            id=optional_str(obj.get("id"), f"{path}.id"),
            type=optional_str(obj.get("type"), f"{path}.type"),
        )


@dataclass(frozen=True, slots=True)
class TeamRef:
    """The team a session belongs to, when the platform has one."""

    name: str | None = None

    def __post_init__(self) -> None:
        if self.name is not None:
            require_str(self.name, "team.name")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.name is not None:
            out["name"] = self.name
        return out

    @classmethod
    def from_dict(cls, value: Any, path: str = "team") -> "TeamRef":
        if value is None:
            return cls()
        obj = require_object(value, path)
        reject_unknown(obj, {"name"}, path)
        return cls(name=optional_str(obj.get("name"), f"{path}.name"))


@dataclass(frozen=True, slots=True)
class ToolRef:
    """One tool call, in the canonical vocabulary the gates read."""

    canonical: str
    native: str
    input: Mapping[str, Any] = field(default_factory=dict)
    result: Mapping[str, Any] | None = None
    error: Mapping[str, Any] | None = None
    call_id: str | None = None
    parent_call_id: str | None = None
    is_sub_dispatch: bool = False

    def __post_init__(self) -> None:
        require_str(self.canonical, "tool.canonical")
        require_str(self.native, "tool.native")
        object.__setattr__(self, "input", require_object(self.input, "tool.input"))
        if self.result is not None:
            object.__setattr__(self, "result", require_object(self.result, "tool.result"))
        if self.error is not None:
            object.__setattr__(self, "error", require_object(self.error, "tool.error"))
        if self.call_id is not None:
            _validate_id(self.call_id, "tool.call_id")
        if self.parent_call_id is not None:
            _validate_id(self.parent_call_id, "tool.parent_call_id")
        if not isinstance(self.is_sub_dispatch, bool):
            raise fail("tool.is_sub_dispatch", "expected a boolean")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "canonical": self.canonical,
            "native": self.native,
            "input": dict(self.input),
        }
        if self.result is not None:
            out["result"] = dict(self.result)
        if self.error is not None:
            out["error"] = dict(self.error)
        if self.call_id is not None:
            out["call_id"] = self.call_id
        if self.parent_call_id is not None:
            out["parent_call_id"] = self.parent_call_id
        if self.is_sub_dispatch:
            out["is_sub_dispatch"] = True
        return out

    @classmethod
    def from_dict(cls, value: Any, path: str = "tool") -> "ToolRef":
        obj = require_object(value, path)
        reject_unknown(
            obj,
            {
                "canonical",
                "native",
                "input",
                "result",
                "error",
                "call_id",
                "parent_call_id",
                "is_sub_dispatch",
            },
            path,
        )
        for required in ("canonical", "native"):
            if required not in obj:
                raise fail(f"{path}.{required}", "is required")
        return cls(
            canonical=obj["canonical"],
            native=obj["native"],
            input=optional_object(obj.get("input"), f"{path}.input") or {},
            result=optional_object(obj.get("result"), f"{path}.result"),
            error=optional_object(obj.get("error"), f"{path}.error"),
            call_id=optional_str(obj.get("call_id"), f"{path}.call_id"),
            parent_call_id=optional_str(obj.get("parent_call_id"), f"{path}.parent_call_id"),
            is_sub_dispatch=obj.get("is_sub_dispatch", False),
        )


@dataclass(frozen=True, slots=True)
class TaskRef:
    """One task-board transition, when the platform has a task board."""

    id: str
    subject: str | None = None
    team_name: str | None = None
    teammate_name: str | None = None
    status: str | None = None
    owner: str | None = None

    def __post_init__(self) -> None:
        require_str(self.id, "task.id")
        for name in ("subject", "team_name", "teammate_name", "status", "owner"):
            value = getattr(self, name)
            if value is not None:
                require_str(value, f"task.{name}")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id}
        for name in ("subject", "team_name", "teammate_name", "status", "owner"):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        return out

    @classmethod
    def from_dict(cls, value: Any, path: str = "task") -> "TaskRef":
        obj = require_object(value, path)
        reject_unknown(
            obj, {"id", "subject", "team_name", "teammate_name", "status", "owner"}, path
        )
        if "id" not in obj:
            raise fail(f"{path}.id", "is required")
        return cls(
            id=obj["id"],
            subject=optional_str(obj.get("subject"), f"{path}.subject"),
            team_name=optional_str(obj.get("team_name"), f"{path}.team_name"),
            teammate_name=optional_str(obj.get("teammate_name"), f"{path}.teammate_name"),
            status=optional_str(obj.get("status"), f"{path}.status"),
            owner=optional_str(obj.get("owner"), f"{path}.owner"),
        )


@dataclass(frozen=True, slots=True)
class EventV1:
    """One platform occurrence, normalised for the harness."""

    id: str
    kind: str
    at: str
    adapter: str
    session: SessionRef
    actor: ActorRef = field(default_factory=ActorRef)
    team: TeamRef | None = None
    tool: ToolRef | None = None
    task: TaskRef | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    raw: Mapping[str, Any] = field(default_factory=dict)
    v: int = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.v != CONTRACT_VERSION:
            raise fail("v", f"expected {CONTRACT_VERSION}, got {self.v!r}")
        _validate_id(self.id, "id")
        require_enum(self.kind, EVENT_KINDS, "kind")
        parse_timestamp(self.at, "at")
        require_str(self.adapter, "adapter")
        if not isinstance(self.session, SessionRef):
            raise fail("session", "expected a SessionRef")
        if not isinstance(self.actor, ActorRef):
            raise fail("actor", "expected an ActorRef")
        if self.team is not None and not isinstance(self.team, TeamRef):
            raise fail("team", "expected a TeamRef or None")
        if self.tool is not None and not isinstance(self.tool, ToolRef):
            raise fail("tool", "expected a ToolRef or None")
        if self.task is not None and not isinstance(self.task, TaskRef):
            raise fail("task", "expected a TaskRef or None")
        object.__setattr__(self, "payload", require_object(self.payload, "payload"))
        object.__setattr__(self, "raw", require_object(self.raw, "raw"))
        if self.kind in TOOL_KINDS and self.tool is None:
            raise fail("tool", f"is required for kind {self.kind!r}")
        if self.kind not in TOOL_KINDS and self.tool is not None:
            raise fail("tool", f"is not allowed for kind {self.kind!r}")
        if self.kind in TASK_KINDS and self.task is None:
            raise fail("task", f"is required for kind {self.kind!r}")
        if self.kind not in TASK_KINDS and self.task is not None:
            raise fail("task", f"is not allowed for kind {self.kind!r}")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "v": self.v,
            "id": self.id,
            "kind": self.kind,
            "at": self.at,
            "adapter": self.adapter,
            "session": self.session.to_dict(),
            "actor": self.actor.to_dict(),
        }
        if self.team is not None:
            out["team"] = self.team.to_dict()
        if self.tool is not None:
            out["tool"] = self.tool.to_dict()
        if self.task is not None:
            out["task"] = self.task.to_dict()
        if self.payload:
            out["payload"] = dict(self.payload)
        if self.raw:
            out["raw"] = dict(self.raw)
        return out

    @classmethod
    def from_dict(cls, value: Any, path: str = "event") -> "EventV1":
        obj = require_object(value, path)
        reject_unknown(
            obj,
            {
                "v",
                "id",
                "kind",
                "at",
                "adapter",
                "session",
                "actor",
                "team",
                "tool",
                "task",
                "payload",
                "raw",
            },
            path,
        )
        for required in ("v", "id", "kind", "at", "adapter", "session"):
            if required not in obj:
                raise fail(f"{path}.{required}", "is required")
        team_obj = obj.get("team")
        tool_obj = obj.get("tool")
        task_obj = obj.get("task")
        return cls(
            id=obj["id"],
            kind=obj["kind"],
            at=obj["at"],
            adapter=obj["adapter"],
            session=SessionRef.from_dict(obj["session"], f"{path}.session"),
            actor=ActorRef.from_dict(obj.get("actor", {}), f"{path}.actor"),
            team=TeamRef.from_dict(team_obj, f"{path}.team") if team_obj is not None else None,
            tool=ToolRef.from_dict(tool_obj, f"{path}.tool") if tool_obj is not None else None,
            task=TaskRef.from_dict(task_obj, f"{path}.task") if task_obj is not None else None,
            payload=optional_object(obj.get("payload"), f"{path}.payload") or {},
            raw=optional_object(obj.get("raw"), f"{path}.raw") or {},
            v=obj["v"],
        )

    def with_payload(self, **extra: Any) -> "EventV1":
        """Return a copy whose payload carries additional keys."""
        merged = dict(self.payload)
        merged.update(extra)
        return replace(self, payload=merged)
