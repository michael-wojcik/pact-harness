"""What a platform can do, and what that entitles the harness to claim.

Nothing here is inferred from a platform's name. An adapter declares a
CapabilitySet; the tier is derived from it; the doctor prints both. That is
the mechanism behind the rule that the harness never claims enforcement it has
not wired.

Tiers
-----
T0 advisory   nothing; instructions and the CLI only
T1 observed   the platform reports tool activity; PACT journals and advises
T2 enforced   the platform can veto a tool call before it runs
T3 native     T2 plus named subagents, a task board and compaction events

The tiers are cumulative by construction: T2 requires T1's events, and T3
requires T2's veto. derive_tier returns the highest tier whose requirements
are all present.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


class Capability(str, Enum):
    """One thing a platform can give the harness."""

    # Events
    EVENT_SESSION_START = "event.session.start"
    EVENT_SESSION_END = "event.session.end"
    EVENT_PROMPT_SUBMIT = "event.prompt.submit"
    EVENT_TOOL_PRE = "event.tool.pre"
    EVENT_TOOL_POST = "event.tool.post"
    EVENT_TOOL_FAIL = "event.tool.fail"
    EVENT_AGENT_SPAWN_PRE = "event.agent.spawn.pre"
    EVENT_AGENT_START = "event.agent.start"
    EVENT_AGENT_STOP = "event.agent.stop"
    EVENT_AGENT_IDLE = "event.agent.idle"
    EVENT_TASK_COMPLETED = "event.task.completed"
    EVENT_COMPACT_PRE = "event.compact.pre"
    EVENT_COMPACT_POST = "event.compact.post"
    EVENT_TURN_STOP = "event.turn.stop"

    # Control
    CONTROL_TOOL_VETO = "control.tool.veto"
    CONTROL_TOOL_ASK = "control.tool.ask"
    CONTROL_TOOL_CANCEL = "control.tool.cancel"
    CONTROL_TURN_BLOCK = "control.turn.block"
    CONTROL_TURN_STEER = "control.turn.steer"
    CONTROL_SPAWN_INJECT = "control.spawn.inject"

    # Delivery
    UI_CONTEXT_INJECT = "ui.context.inject"
    UI_NOTIFY = "ui.notify"
    UI_COMPACT_INSTRUCTIONS = "ui.compact.instructions"

    # Native gaps the harness may have to fill
    NATIVE_TASK_BOARD = "native.task.board"
    NATIVE_MESSAGING = "native.messaging"
    NATIVE_SKILLS = "native.skills"
    NATIVE_COMMANDS = "native.commands"
    NATIVE_SUBAGENTS_NAMED = "native.subagents.named"
    NATIVE_TRANSCRIPT = "native.transcript"
    NATIVE_INSTRUCTION_FILE = "native.instruction_file"


class Tier(str, Enum):
    """Derived enforcement tier. Never configured directly."""

    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"


class Applies(str, Enum):
    """What the applicable platform should do with one upstream hook module."""

    RUN = "run"
    ADAPT = "adapt"
    DISABLE = "disable"
    REPLACE = "replace"


#: Tier T1: the platform reports tool activity.
T1_REQUIREMENTS: frozenset[Capability] = frozenset(
    {
        Capability.EVENT_TOOL_PRE,
        Capability.EVENT_TOOL_POST,
    }
)

#: Tier T2: the platform can veto before dispatch.
T2_REQUIREMENTS: frozenset[Capability] = T1_REQUIREMENTS | frozenset(
    {Capability.CONTROL_TOOL_VETO}
)

#: Tier T3: T2 plus named subagents, a task board and compaction events.
T3_REQUIREMENTS: frozenset[Capability] = T2_REQUIREMENTS | frozenset(
    {
        Capability.EVENT_AGENT_START,
        Capability.EVENT_AGENT_STOP,
        Capability.NATIVE_SUBAGENTS_NAMED,
        Capability.NATIVE_TASK_BOARD,
        Capability.EVENT_COMPACT_PRE,
        Capability.EVENT_COMPACT_POST,
    }
)


def derive_tier(capabilities: Iterable[Capability]) -> Tier:
    """Return the highest tier whose requirements are all present."""
    present = frozenset(capabilities)
    if T3_REQUIREMENTS <= present:
        return Tier.T3
    if T2_REQUIREMENTS <= present:
        return Tier.T2
    if T1_REQUIREMENTS <= present:
        return Tier.T1
    return Tier.T0


@dataclass(frozen=True, slots=True)
class CapabilitySet:
    """An adapter's declared capabilities."""

    capabilities: frozenset[Capability] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        unknown = [item for item in self.capabilities if not isinstance(item, Capability)]
        if unknown:
            raise TypeError(f"not Capability members: {unknown!r}")

    def has(self, capability: Capability) -> bool:
        """Whether the platform declares one capability."""
        return capability in self.capabilities

    def has_all(self, required: Iterable[Capability]) -> bool:
        """Whether every required capability is present."""
        return frozenset(required) <= self.capabilities

    def missing(self, required: Iterable[Capability]) -> frozenset[Capability]:
        """The required capabilities that are absent."""
        return frozenset(required) - self.capabilities

    @property
    def tier(self) -> Tier:
        """The tier this capability set earns."""
        return derive_tier(self.capabilities)

    def sorted_names(self) -> tuple[str, ...]:
        """Capability values, sorted, for stable doctor output."""
        return tuple(sorted(item.value for item in self.capabilities))

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view for adapter manifests."""
        return {
            "tier": self.tier.value,
            "capabilities": list(self.sorted_names()),
        }


@dataclass(frozen=True, slots=True)
class ApplicabilityRow:
    """How one upstream hook module applies to one platform.

    The four actions are the whole vocabulary: run it unchanged, adapt it
    (platform-specific document target or vocabulary), disable it (its premise
    does not exist here), or replace it with a platform artifact.
    """

    hook: str
    module: str
    events: tuple[str, ...]
    applies: Applies
    required: frozenset[Capability] = field(default_factory=frozenset)
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.applies, Applies):
            raise TypeError("applies must be an Applies member")
        if not self.hook:
            raise TypeError("hook must be a non-empty string")
        if not self.module:
            raise TypeError("module must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view for adapter manifests."""
        return {
            "hook": self.hook,
            "module": self.module,
            "events": list(self.events),
            "applies": self.applies.value,
            "required": sorted(item.value for item in self.required),
            "reason": self.reason,
        }

#: Manifest event -> the capability that makes it observable on a platform.
EVENT_CAPABILITIES: dict[str, Capability] = {
    "SessionStart": Capability.EVENT_SESSION_START,
    "SessionEnd": Capability.EVENT_SESSION_END,
    "UserPromptSubmit": Capability.EVENT_PROMPT_SUBMIT,
    "PreToolUse": Capability.EVENT_TOOL_PRE,
    "PostToolUse": Capability.EVENT_TOOL_POST,
    "PostToolUseFailure": Capability.EVENT_TOOL_FAIL,
    "SubagentStart": Capability.EVENT_AGENT_START,
    "SubagentStop": Capability.EVENT_AGENT_STOP,
    "TeammateIdle": Capability.EVENT_AGENT_IDLE,
    "TaskCompleted": Capability.EVENT_TASK_COMPLETED,
    "PreCompact": Capability.EVENT_COMPACT_PRE,
    "PostCompact": Capability.EVENT_COMPACT_POST,
    "Stop": Capability.EVENT_TURN_STOP,
}


def applicability_for(
    plugin_root: "Path", capabilities: CapabilitySet
) -> tuple[ApplicabilityRow, ...]:
    """Derive the applicability table for one adapter from the registry.

    Only what the registry and the capability set can answer is claimed here.
    A module whose events the platform surfaces is RUN; a module whose events do
    not exist on the platform is DISABLE, with the missing capability named.
    ADAPT and REPLACE need knowledge of the module's internals, and inventing
    them would be exactly the kind of unbacked claim this file exists to
    prevent.
    """
    from pathlib import Path

    from .runtime.registry import HookRegistry

    registry = HookRegistry.from_plugin_root(Path(plugin_root))
    grouped: dict[str, dict[str, Any]] = {}
    for registration in registry.registrations:
        entry = grouped.setdefault(
            registration.hook, {"module": registration.script.name, "events": []}
        )
        if registration.event not in entry["events"]:
            entry["events"].append(registration.event)
    rows: list[ApplicabilityRow] = []
    for hook in sorted(grouped):
        entry = grouped[hook]
        events = tuple(entry["events"])
        required = frozenset(
            EVENT_CAPABILITIES[event] for event in events if event in EVENT_CAPABILITIES
        )
        missing = sorted(item.value for item in required - capabilities.capabilities)
        if missing:
            applies = Applies.DISABLE
            reason = "event not surfaced by this platform: " + ", ".join(missing)
        else:
            applies = Applies.RUN
            reason = "every event this module runs on is surfaced"
        rows.append(
            ApplicabilityRow(
                hook=hook,
                module=str(entry["module"]),
                events=events,
                applies=applies,
                required=required,
                reason=reason,
            )
        )
    return tuple(rows)
