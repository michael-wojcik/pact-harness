"""pact.directive.v1: construction rules and wire round-trips."""

from __future__ import annotations

import pytest

from pact_harness.capabilities import Tier
from pact_harness.contract import Directive, EnvelopeMeta, EnvelopeV1, GateSkip
from pact_harness.errors import ContractError

VALID = {
    "deny": Directive.deny("blocked"),
    "ask": Directive.ask("confirm"),
    "cancel": Directive.cancel(),
    "allow": Directive.allow(),
    "inject": Directive.inject("context"),
    "notify": Directive.notify("heads up"),
    "block": Directive.block("finish the handoff"),
    "compact_instructions": Directive.compact_instructions("summarise state"),
    "silent": Directive.silent(),
    "hook_error": Directive.hook_error("dispatch_gate", stage="launch", message="missing"),
}


@pytest.mark.parametrize("kind", sorted(VALID))
def test_every_directive_round_trips(kind: str) -> None:
    directive = VALID[kind]
    assert Directive.from_dict(directive.to_dict()) == directive


def test_deny_requires_reason() -> None:
    with pytest.raises(ContractError, match="requires"):
        Directive(kind="deny")


def test_inject_requires_target_and_source() -> None:
    with pytest.raises(ContractError, match="requires"):
        Directive(kind="inject", text="body")


def test_notify_rejects_unknown_level() -> None:
    with pytest.raises(ContractError, match="level"):
        Directive.notify("body", level="shout")


def test_field_for_kind_is_rejected() -> None:
    with pytest.raises(ContractError, match="do not belong to kind"):
        Directive(kind="allow", text="surprise")


def test_unknown_directive_kind_is_rejected() -> None:
    with pytest.raises(ContractError, match="is not one of"):
        Directive(kind="permit")


def test_fatal_only_serialises_for_hook_error() -> None:
    fatal = Directive.hook_error("h", stage="decode", message="bad", fatal=True)
    assert fatal.to_dict()["fatal"] is True
    assert "fatal" not in Directive.deny("no").to_dict()


def test_fatal_is_rejected_on_other_kinds() -> None:
    with pytest.raises(ContractError, match="do not belong to kind"):
        Directive(kind="allow", fatal=True)


def test_envelope_round_trips_with_meta_and_skips() -> None:
    envelope = EnvelopeV1(
        meta=EnvelopeMeta(
            hook="worktree_guard",
            event="tool.pre",
            adapter="dsh",
            tier=Tier.T2,
            exit=2,
            elapsed_ms=31,
            raw_stdout_bytes=64,
            gate_skips=(GateSkip(gate="worktree_guard", reason="no file_path"),),
        ),
        directives=(Directive.deny("outside worktree", rule="worktree_guard"),),
    )
    assert EnvelopeV1.from_dict(envelope.to_dict()) == envelope


def test_empty_directives_is_allowed() -> None:
    envelope = EnvelopeV1(meta=EnvelopeMeta(hook="h", event="tool.pre", adapter="test"))
    assert EnvelopeV1.from_dict(envelope.to_dict()) == envelope


def test_unknown_envelope_field_is_rejected() -> None:
    payload = EnvelopeV1(
        meta=EnvelopeMeta(hook="h", event="tool.pre", adapter="test")
    ).to_dict()
    payload["extra"] = 1
    with pytest.raises(ContractError, match="unknown field"):
        EnvelopeV1.from_dict(payload)


def test_meta_tier_must_be_known() -> None:
    with pytest.raises(ContractError, match="meta.tier"):
        EnvelopeMeta.from_dict({"hook": "h", "event": "tool.pre", "adapter": "test", "tier": "T9"})


def test_gate_skip_requires_both_fields() -> None:
    with pytest.raises(ContractError, match="skip.reason"):
        GateSkip.from_dict({"gate": "g"})
