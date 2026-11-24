"""Read the vendored hooks.json into registrations.

The registry is derived from upstream's own manifest rather than from a copy.
That is what makes an upstream release that adds, removes or re-matches a hook
show up as a different registry instead of as silent drift.

Matchers are evaluated against the canonical tool name. Upstream's matchers are
written in Claude Code's vocabulary, and the harness's canonical vocabulary is
that same vocabulary, so an adapter canonicalises before matching and the
matcher text never needs rewriting.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import PactHarnessError

HOOKS_JSON_RELATIVE = Path("hooks/hooks.json")

#: The token every hook command uses to name its script.
ROOT_TOKEN = "${CLAUDE_PLUGIN_ROOT}/"


class RegistryError(PactHarnessError):
    """The vendored hook registry is missing or malformed."""


@dataclass(frozen=True, slots=True)
class HookCommand:
    """One command line inside a hook group."""

    command: str
    timeout_s: float | None = None
    async_: bool = False


@dataclass(frozen=True, slots=True)
class HookGroup:
    """One matcher group: an optional matcher and the commands it runs."""

    matcher: str | None
    commands: tuple[HookCommand, ...]


@dataclass(frozen=True, slots=True)
class HookRegistration:
    """One command flattened into something the runtime can execute."""

    event: str
    group_index: int
    command_index: int
    matcher: str | None
    script: Path
    hook: str
    timeout_s: float | None
    async_: bool

    @property
    def key(self) -> str:
        """A stable identifier for reports and fixtures."""
        return f"{self.event}:{self.group_index}:{self.command_index}"

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view for the doctor and conformance output."""
        return {
            "event": self.event,
            "group": self.group_index,
            "command": self.command_index,
            "matcher": self.matcher,
            "hook": self.hook,
            "script": str(self.script.name),
            "timeout_s": self.timeout_s,
            "async": self.async_,
        }


def _parse_group(raw_group: Any, index: int) -> HookGroup:
    if not isinstance(raw_group, dict):
        raise RegistryError(f"hooks.json: group {index} is not an object")
    matcher = raw_group.get("matcher")
    if matcher is not None and not isinstance(matcher, str):
        raise RegistryError(f"hooks.json: group {index} matcher is not a string")
    raw_commands = raw_group.get("hooks", [])
    if not isinstance(raw_commands, list):
        raise RegistryError(f"hooks.json: group {index} hooks is not a list")
    commands: list[HookCommand] = []
    for command_index, raw_command in enumerate(raw_commands):
        if not isinstance(raw_command, dict):
            raise RegistryError(
                f"hooks.json: group {index} command {command_index} is not an object"
            )
        command = raw_command.get("command")
        if not isinstance(command, str) or not command:
            raise RegistryError(
                f"hooks.json: group {index} command {command_index} has no command string"
            )
        raw_timeout = raw_command.get("timeout")
        timeout_s: float | None = None
        if raw_timeout is not None:
            if not isinstance(raw_timeout, (int, float)) or isinstance(raw_timeout, bool):
                raise RegistryError(
                    f"hooks.json: group {index} command {command_index} timeout is not a number"
                )
            timeout_s = float(raw_timeout)
        commands.append(
            HookCommand(
                command=command,
                timeout_s=timeout_s,
                async_=bool(raw_command.get("async", False)),
            )
        )
    return HookGroup(matcher=matcher, commands=tuple(commands))


def matcher_matches(matcher: str | None, tool_name: str | None) -> bool:
    """Whether a group's matcher admits one tool call.

    An absent or empty matcher admits every call. A matcher is a pipe-separated
    list of exact tool names, never a pattern: upstream relies on that, and a
    prefix or substring rule here would widen every gate's surface.
    """
    if matcher is None or matcher.strip() == "":
        return True
    if tool_name is None:
        # A matcher on an event that carries no tool is upstream's own choice;
        # admitting it keeps the harness from silently dropping a registration.
        return True
    return tool_name in [part.strip() for part in matcher.split("|")]


class HookRegistry:
    """Every hook registration in one vendored plugin tree."""

    def __init__(self, registrations: tuple[HookRegistration, ...], source: Path) -> None:
        self._registrations = registrations
        self._source = source

    @property
    def source(self) -> Path:
        """The hooks.json this registry was read from."""
        return self._source

    @property
    def registrations(self) -> tuple[HookRegistration, ...]:
        """Every registration, in manifest order."""
        return self._registrations

    @property
    def events(self) -> tuple[str, ...]:
        """The event names present, in first-seen order."""
        seen: list[str] = []
        for registration in self._registrations:
            if registration.event not in seen:
                seen.append(registration.event)
        return tuple(seen)

    def modules(self) -> tuple[str, ...]:
        """Distinct hook module names, in first-seen order."""
        seen: list[str] = []
        for registration in self._registrations:
            if registration.hook not in seen:
                seen.append(registration.hook)
        return tuple(seen)

    def for_event(self, event: str, *, tool_name: str | None = None) -> tuple[HookRegistration, ...]:
        """Registrations for one event, filtered by matcher when a tool is named."""
        return tuple(
            registration
            for registration in self._registrations
            if registration.event == event
            and matcher_matches(registration.matcher, tool_name)
        )

    def __len__(self) -> int:
        return len(self._registrations)

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view for the doctor."""
        return {
            "source": str(self._source),
            "registrations": len(self._registrations),
            "events": list(self.events),
            "modules": list(self.modules()),
            "hooks": [registration.to_dict() for registration in self._registrations],
        }

    @classmethod
    def from_plugin_root(cls, plugin_root: Path) -> "HookRegistry":
        """Load the registry from a vendored PACT tree."""
        path = plugin_root / HOOKS_JSON_RELATIVE
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RegistryError(f"hook registry missing: {path}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryError(f"hook registry unreadable: {path}: {exc}") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("hooks"), dict):
            raise RegistryError(f"hook registry has no hooks object: {path}")

        registrations: list[HookRegistration] = []
        for event, raw_groups in raw["hooks"].items():
            if not isinstance(event, str) or not isinstance(raw_groups, list):
                raise RegistryError(f"hook registry event {event!r} is malformed")
            for group_index, raw_group in enumerate(raw_groups):
                group = _parse_group(raw_group, group_index)
                for command_index, command in enumerate(group.commands):
                    marker = command.command.find(ROOT_TOKEN)
                    if marker < 0:
                        raise RegistryError(
                            "hook command does not reference " + ROOT_TOKEN + ": "
                            f"{command.command!r}"
                        )
                    tail = command.command[marker + len(ROOT_TOKEN):]
                    relative = tail.split('"')[0].strip()
                    if not relative:
                        raise RegistryError(f"hook command names no script: {command.command!r}")
                    script = (plugin_root / relative).resolve()
                    registrations.append(
                        HookRegistration(
                            event=event,
                            group_index=group_index,
                            command_index=command_index,
                            matcher=group.matcher,
                            script=script,
                            hook=script.stem,
                            timeout_s=command.timeout_s,
                            async_=command.async_,
                        )
                    )
        return cls(tuple(registrations), path)
