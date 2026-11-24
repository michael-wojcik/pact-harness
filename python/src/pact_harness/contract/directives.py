"""pact.directive.v1: what PACT tells a platform to do.

A directive is deliberately flat. The runtime produces them in a fresh process
per platform event, and adapters consume them across a process boundary, so a
tagged union with one class per kind would buy type safety at the cost of a
decoder per kind on the hot path. Instead there is one class, one set of
per-kind rules, and constructors that make the valid shapes obvious:

    Directive.deny("blocked until bootstrap completes", rule="bootstrap_gate")
    Directive.inject("context text", target="step", source="pact")
    Directive.hook_error("dispatch_gate", stage="launch", message="...", fatal=True)

Rules
-----
A field that does not belong to a kind is rejected rather than ignored: an
adapter that fills the wrong field has a bug, and a silently dropped directive
is worse than a failed decode.

An envelope with no directives means allow. The runtime always emits at least
one directive, so an empty envelope is a decoder artifact rather than a
policy decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .. import CONTRACT_VERSION
from ..capabilities import Tier
from ._util import (
    fail,
    optional_object,
    optional_str,
    reject_unknown,
    require_bool,
    require_enum,
    require_int,
    require_object,
    require_str,
)

#: Every directive kind in pact.directive.v1.
DIRECTIVE_KINDS: frozenset[str] = frozenset(
    {
        "deny",
        "ask",
        "cancel",
        "allow",
        "inject",
        "notify",
        "block",
        "compact_instructions",
        "silent",
        "hook_error",
    }
)

#: Where injected text lands.
INJECT_TARGETS: frozenset[str] = frozenset({"step", "turn", "user"})

#: Visible notification severities.
NOTIFY_LEVELS: frozenset[str] = frozenset({"info", "warning"})

#: How far the runtime got before the hook failed.
HOOK_ERROR_STAGES: frozenset[str] = frozenset({"launch", "exit", "decode"})

#: Fields each kind may populate.
_KIND_FIELDS: dict[str, frozenset[str]] = {
    "deny": frozenset({"reason", "rule", "info"}),
    "ask": frozenset({"reason"}),
    "cancel": frozenset(),
    "allow": frozenset(),
    "inject": frozenset({"text", "target", "source"}),
    "notify": frozenset({"text", "level"}),
    "block": frozenset({"reason"}),
    "compact_instructions": frozenset({"text"}),
    "silent": frozenset(),
    "hook_error": frozenset({"hook", "stage", "message", "fatal"}),
}

#: Fields each kind must populate.
_KIND_REQUIRED: dict[str, frozenset[str]] = {
    "deny": frozenset({"reason"}),
    "ask": frozenset(),
    "cancel": frozenset(),
    "allow": frozenset(),
    "inject": frozenset({"text", "target", "source"}),
    "notify": frozenset({"text", "level"}),
    "block": frozenset({"reason"}),
    "compact_instructions": frozenset({"text"}),
    "silent": frozenset(),
    "hook_error": frozenset({"hook", "stage", "message"}),
}


@dataclass(frozen=True, slots=True)
class Directive:
    """One instruction from PACT to a platform."""

    kind: str
    reason: str | None = None
    rule: str | None = None
    info: Mapping[str, Any] | None = None
    text: str | None = None
    target: str | None = None
    source: str | None = None
    level: str | None = None
    hook: str | None = None
    stage: str | None = None
    message: str | None = None
    fatal: bool = False

    def __post_init__(self) -> None:
        require_enum(self.kind, DIRECTIVE_KINDS, "kind")
        supplied = self._supplied_fields()
        allowed = _KIND_FIELDS[self.kind]
        extra = sorted(supplied - allowed)
        if extra:
            raise fail(
                "directive",
                f"field(s) {extra!r} do not belong to kind {self.kind!r}",
            )
        missing = sorted(_KIND_REQUIRED[self.kind] - supplied)
        if missing:
            raise fail(
                "directive",
                f"kind {self.kind!r} requires {missing!r}",
            )
        if self.kind == "inject":
            require_enum(self.target, INJECT_TARGETS, "directive.target")
            require_str(self.source, "directive.source")
            require_str(self.text, "directive.text")
        if self.kind == "notify":
            require_enum(self.level, NOTIFY_LEVELS, "directive.level")
            require_str(self.text, "directive.text")
        if self.kind == "hook_error":
            require_str(self.hook, "directive.hook")
            require_enum(self.stage, HOOK_ERROR_STAGES, "directive.stage")
            require_str(self.message, "directive.message")
            require_bool(self.fatal, "directive.fatal")
        if self.info is not None:
            object.__setattr__(self, "info", require_object(self.info, "directive.info"))

    def _supplied_fields(self) -> set[str]:
        names = {
            "reason",
            "rule",
            "info",
            "text",
            "target",
            "source",
            "level",
            "hook",
            "stage",
            "message",
        }
        supplied = {name for name in names if getattr(self, name) is not None}
        if self.fatal:
            supplied.add("fatal")
        return supplied

    def to_dict(self) -> dict[str, Any]:
        """The wire form, omitting absent fields."""
        out: dict[str, Any] = {"kind": self.kind}
        for name in (
            "reason",
            "rule",
            "info",
            "text",
            "target",
            "source",
            "level",
            "hook",
            "stage",
            "message",
        ):
            value = getattr(self, name)
            if value is not None:
                out[name] = dict(value) if isinstance(value, Mapping) else value
        if self.kind == "hook_error" and self.fatal:
            out["fatal"] = True
        return out

    @classmethod
    def from_dict(cls, value: Any, path: str = "directive") -> "Directive":
        obj = require_object(value, path)
        reject_unknown(
            obj,
            {
                "kind",
                "reason",
                "rule",
                "info",
                "text",
                "target",
                "source",
                "level",
                "hook",
                "stage",
                "message",
                "fatal",
            },
            path,
        )
        if "kind" not in obj:
            raise fail(f"{path}.kind", "is required")
        return cls(
            kind=obj["kind"],
            reason=optional_str(obj.get("reason"), f"{path}.reason"),
            rule=optional_str(obj.get("rule"), f"{path}.rule"),
            info=optional_object(obj.get("info"), f"{path}.info"),
            text=optional_str(obj.get("text"), f"{path}.text"),
            target=optional_str(obj.get("target"), f"{path}.target"),
            source=optional_str(obj.get("source"), f"{path}.source"),
            level=optional_str(obj.get("level"), f"{path}.level"),
            hook=optional_str(obj.get("hook"), f"{path}.hook"),
            stage=optional_str(obj.get("stage"), f"{path}.stage"),
            message=optional_str(obj.get("message"), f"{path}.message"),
            fatal=obj.get("fatal", False),
        )

    # -- constructors -----------------------------------------------------

    @classmethod
    def deny(cls, reason: str, *, rule: str | None = None, info: Mapping[str, Any] | None = None) -> "Directive":
        """Refuse the tool call."""
        return cls(kind="deny", reason=reason, rule=rule, info=info)

    @classmethod
    def ask(cls, reason: str | None = None) -> "Directive":
        """Route the decision to the platform's approval surface."""
        return cls(kind="ask", reason=reason)

    @classmethod
    def cancel(cls) -> "Directive":
        """Abort without a policy denial."""
        return cls(kind="cancel")

    @classmethod
    def allow(cls) -> "Directive":
        """Explicitly permit the call."""
        return cls(kind="allow")

    @classmethod
    def inject(cls, text: str, *, target: str = "step", source: str = "pact") -> "Directive":
        """Deliver text into the agent's context."""
        return cls(kind="inject", text=text, target=target, source=source)

    @classmethod
    def notify(cls, text: str, *, level: str = "warning") -> "Directive":
        """Show text to the user."""
        return cls(kind="notify", text=text, level=level)

    @classmethod
    def block(cls, reason: str) -> "Directive":
        """Refuse the end of the turn."""
        return cls(kind="block", reason=reason)

    @classmethod
    def compact_instructions(cls, text: str) -> "Directive":
        """Hand instructions to the compaction model."""
        return cls(kind="compact_instructions", text=text)

    @classmethod
    def silent(cls) -> "Directive":
        """Produce no output."""
        return cls(kind="silent")

    @classmethod
    def hook_error(
        cls,
        hook: str,
        *,
        stage: str,
        message: str,
        fatal: bool = False,
    ) -> "Directive":
        """Report that a hook could not run or could not be decoded."""
        return cls(kind="hook_error", hook=hook, stage=stage, message=message, fatal=fatal)


@dataclass(frozen=True, slots=True)
class GateSkip:
    """A gate that declined to judge because its input was unavailable."""

    gate: str
    reason: str

    def __post_init__(self) -> None:
        require_str(self.gate, "skip.gate")
        require_str(self.reason, "skip.reason")

    def to_dict(self) -> dict[str, Any]:
        """The wire form."""
        return {"gate": self.gate, "reason": self.reason}

    @classmethod
    def from_dict(cls, value: Any, path: str = "skip") -> "GateSkip":
        obj = require_object(value, path)
        reject_unknown(obj, {"gate", "reason"}, path)
        if "gate" not in obj:
            raise fail(f"{path}.gate", "is required")
        if "reason" not in obj:
            raise fail(f"{path}.reason", "is required")
        return cls(gate=obj["gate"], reason=obj["reason"])


@dataclass(frozen=True, slots=True)
class EnvelopeMeta:
    """Provenance for one envelope: what ran, where, and how it went."""

    hook: str
    event: str
    adapter: str
    tier: Tier = Tier.T0
    exit: int | None = None
    elapsed_ms: int | None = None
    raw_stdout_bytes: int | None = None
    gate_skips: tuple[GateSkip, ...] = ()

    def __post_init__(self) -> None:
        require_str(self.hook, "meta.hook")
        require_str(self.event, "meta.event")
        require_str(self.adapter, "meta.adapter")
        if not isinstance(self.tier, Tier):
            raise fail("meta.tier", "expected a Tier member")
        if self.exit is not None:
            require_int(self.exit, "meta.exit")
        if self.elapsed_ms is not None:
            require_int(self.elapsed_ms, "meta.elapsed_ms", minimum=0)
        if self.raw_stdout_bytes is not None:
            require_int(self.raw_stdout_bytes, "meta.raw_stdout_bytes", minimum=0)
        for index, skip in enumerate(self.gate_skips):
            if not isinstance(skip, GateSkip):
                raise fail(f"meta.gate_skips[{index}]", "expected a GateSkip")

    def to_dict(self) -> dict[str, Any]:
        """The wire form, omitting absent fields."""
        out: dict[str, Any] = {
            "hook": self.hook,
            "event": self.event,
            "adapter": self.adapter,
            "tier": self.tier.value,
        }
        if self.exit is not None:
            out["exit"] = self.exit
        if self.elapsed_ms is not None:
            out["elapsed_ms"] = self.elapsed_ms
        if self.raw_stdout_bytes is not None:
            out["raw_stdout_bytes"] = self.raw_stdout_bytes
        if self.gate_skips:
            out["gate_skips"] = [skip.to_dict() for skip in self.gate_skips]
        return out

    @classmethod
    def from_dict(cls, value: Any, path: str = "meta") -> "EnvelopeMeta":
        obj = require_object(value, path)
        reject_unknown(
            obj,
            {
                "hook",
                "event",
                "adapter",
                "tier",
                "exit",
                "elapsed_ms",
                "raw_stdout_bytes",
                "gate_skips",
            },
            path,
        )
        for required in ("hook", "event", "adapter"):
            if required not in obj:
                raise fail(f"{path}.{required}", "is required")
        raw_tier = obj.get("tier", Tier.T0.value)
        tier = require_enum(raw_tier, frozenset(item.value for item in Tier), f"{path}.tier")
        raw_skips = obj.get("gate_skips", [])
        if not isinstance(raw_skips, list):
            raise fail(f"{path}.gate_skips", "expected a list")
        return cls(
            hook=obj["hook"],
            event=obj["event"],
            adapter=obj["adapter"],
            tier=Tier(tier),
            exit=obj.get("exit"),
            elapsed_ms=obj.get("elapsed_ms"),
            raw_stdout_bytes=obj.get("raw_stdout_bytes"),
            gate_skips=tuple(
                GateSkip.from_dict(item, f"{path}.gate_skips[{index}]")
                for index, item in enumerate(raw_skips)
            ),
        )


@dataclass(frozen=True, slots=True)
class EnvelopeV1:
    """One hook evaluation's worth of directives, plus provenance."""

    meta: EnvelopeMeta
    directives: tuple[Directive, ...] = ()
    v: int = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.v != CONTRACT_VERSION:
            raise fail("v", f"expected {CONTRACT_VERSION}, got {self.v!r}")
        if not isinstance(self.meta, EnvelopeMeta):
            raise fail("meta", "expected an EnvelopeMeta")
        for index, directive in enumerate(self.directives):
            if not isinstance(directive, Directive):
                raise fail(f"directives[{index}]", "expected a Directive")

    def to_dict(self) -> dict[str, Any]:
        """The wire form."""
        return {
            "v": self.v,
            "directives": [directive.to_dict() for directive in self.directives],
            "meta": self.meta.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Any, path: str = "envelope") -> "EnvelopeV1":
        obj = require_object(value, path)
        reject_unknown(obj, {"v", "directives", "meta"}, path)
        for required in ("v", "meta"):
            if required not in obj:
                raise fail(f"{path}.{required}", "is required")
        raw_directives = obj.get("directives", [])
        if not isinstance(raw_directives, list):
            raise fail(f"{path}.directives", "expected a list")
        return cls(
            meta=EnvelopeMeta.from_dict(obj["meta"], f"{path}.meta"),
            directives=tuple(
                Directive.from_dict(item, f"{path}.directives[{index}]")
                for index, item in enumerate(raw_directives)
            ),
            v=obj["v"],
        )
