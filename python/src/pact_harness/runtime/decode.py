"""Decode a hook process into directives.

The shapes below are the complete set the vendored modules emit, measured from
all 28 of them rather than guessed:

  {"suppressOutput": true}
  {"hookSpecificOutput": {"hookEventName": X}}
  {"hookSpecificOutput": {"hookEventName": X, "permissionDecision": "deny" | "ask" | "defer",
                          "permissionDecisionReason": ...}}
  {"hookSpecificOutput": {"hookEventName": X, "additionalContext": ...}}
  {"systemMessage": ...}
  {"decision": "block", "reason": ...}            (Stop and SubagentStop only)
  {"custom_instructions": ...}                     (PreCompact only)
  exit 2 with stderr only

Two rules are load-bearing.

A hook that exits 2 always yields a refusal. Upstream contains a documented
disagreement about which channel survives exit 2 -- one module says stdout is
ignored and stderr is read, another says the opposite was measured -- so the
decoder accepts either and the adapter renders both.

An unknown permissionDecision is fatal rather than treatable as allow. A value
the decoder does not understand is a value whose refusal semantics are unknown,
and silently allowing it is the one failure direction a security gate must not
take.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..contract import Directive

EXIT_REFUSED = 2

_KNOWN_DECISIONS = frozenset({"deny", "ask", "defer"})


@dataclass(frozen=True, slots=True)
class DecodedHook:
    """Directives plus the parsed payload, for diagnostics."""

    directives: tuple[Directive, ...]
    payload: dict[str, Any] | None = None
    problem: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view for conformance output."""
        return {
            "directives": [directive.to_dict() for directive in self.directives],
            "payload": self.payload,
            "problem": self.problem,
        }


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").strip()


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _parse(stdout: bytes) -> tuple[dict[str, Any] | None, bool]:
    """Return (payload, parsed). parsed is False only for malformed JSON."""
    text = _text(stdout)
    if not text:
        return None, True
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None, False
    if not isinstance(parsed, dict):
        return None, False
    return parsed, True


def decode_hook_output(
    *,
    hook: str,
    exit_code: int | None,
    stdout: bytes,
    stderr: bytes,
    timed_out: bool = False,
    launch_error: str | None = None,
    inject_target: str = "step",
) -> DecodedHook:
    """Translate one hook process into directives."""
    stderr_text = _text(stderr)

    if launch_error:
        return DecodedHook(
            directives=(
                Directive.hook_error(hook, stage="launch", message=launch_error, fatal=True),
            ),
            problem=launch_error,
        )
    if timed_out:
        message = "hook timed out"
        return DecodedHook(
            directives=(Directive.hook_error(hook, stage="exit", message=message),),
            problem=message,
        )
    if exit_code not in (0, EXIT_REFUSED):
        message = f"hook exited {exit_code!r}"
        return DecodedHook(
            directives=(Directive.hook_error(hook, stage="exit", message=message),),
            problem=message,
        )

    payload, parsed_ok = _parse(stdout)

    if not parsed_ok:
        if exit_code == EXIT_REFUSED:
            return DecodedHook(
                directives=(Directive.deny(stderr_text or "hook refused without a reason", rule=hook),),
                problem="malformed stdout on a refusing hook",
            )
        message = "hook stdout was not a JSON object"
        return DecodedHook(
            directives=(Directive.hook_error(hook, stage="decode", message=message),),
            problem=message,
        )

    if payload is None:
        if exit_code == EXIT_REFUSED:
            return DecodedHook(
                directives=(Directive.deny(stderr_text or "hook refused without a reason", rule=hook),),
            )
        return DecodedHook(directives=(Directive.silent(),))

    hook_specific = payload.get("hookSpecificOutput")
    specific = hook_specific if isinstance(hook_specific, dict) else {}

    decision = specific.get("permissionDecision")
    reason = _string(specific.get("permissionDecisionReason")) or stderr_text or None
    directives: list[Directive] = []
    problem: str | None = None

    if decision is not None and decision not in _KNOWN_DECISIONS:
        message = f"unknown permissionDecision {decision!r}"
        directives.append(Directive.hook_error(hook, stage="decode", message=message, fatal=True))
        problem = message
    elif decision == "deny":
        directives.append(
            Directive.deny(reason or "PACT gate denied the call", rule=hook)
        )
    elif decision == "ask":
        directives.append(Directive.ask(reason))
    elif decision == "defer":
        directives.append(Directive.allow())
        if reason:
            directives.append(Directive.notify(reason))

    top_decision = payload.get("decision")
    if top_decision == "block":
        directives.append(Directive.block(_string(payload.get("reason")) or "PACT refused the turn end"))
    elif top_decision is not None and not problem:
        message = f"unknown decision {top_decision!r}"
        directives.append(Directive.hook_error(hook, stage="decode", message=message, fatal=True))
        problem = message

    context = _string(specific.get("additionalContext")) or _string(payload.get("additionalContext"))
    if context:
        directives.append(Directive.inject(context, target=inject_target, source=hook))

    system_message = _string(payload.get("systemMessage"))
    if system_message:
        directives.append(Directive.notify(system_message))

    instructions = _string(payload.get("custom_instructions"))
    if instructions:
        directives.append(Directive.compact_instructions(instructions))

    if exit_code == EXIT_REFUSED and not any(
        directive.kind in ("deny", "ask") for directive in directives
    ):
        directives.append(Directive.deny(reason or "hook refused without a reason", rule=hook))

    if not directives:
        directives.append(Directive.silent())

    return DecodedHook(directives=tuple(directives), payload=payload, problem=problem)
