"""The harness contract: pact.event.v1 and pact.directive.v1.

Two documents cross the platform boundary. A platform adapter turns a native
event into an EventV1; the runtime turns hook output into an EnvelopeV1.
"""

from __future__ import annotations

from .codec import (
    dumps_envelope,
    dumps_event,
    envelope_from_dict,
    envelope_to_dict,
    event_from_dict,
    event_to_dict,
    loads_envelope,
    loads_event,
)
from .directives import (
    DIRECTIVE_KINDS,
    HOOK_ERROR_STAGES,
    INJECT_TARGETS,
    NOTIFY_LEVELS,
    Directive,
    EnvelopeMeta,
    EnvelopeV1,
    GateSkip,
)
from .events import (
    ACTOR_ROLES,
    EVENT_KINDS,
    ActorRef,
    EventV1,
    SessionRef,
    TaskRef,
    TeamRef,
    ToolRef,
)

__all__ = [
    "ACTOR_ROLES",
    "DIRECTIVE_KINDS",
    "EVENT_KINDS",
    "HOOK_ERROR_STAGES",
    "INJECT_TARGETS",
    "NOTIFY_LEVELS",
    "ActorRef",
    "Directive",
    "EnvelopeMeta",
    "EnvelopeV1",
    "EventV1",
    "GateSkip",
    "SessionRef",
    "TaskRef",
    "TeamRef",
    "ToolRef",
    "dumps_envelope",
    "dumps_event",
    "envelope_from_dict",
    "envelope_to_dict",
    "event_from_dict",
    "event_to_dict",
    "loads_envelope",
    "loads_event",
]
