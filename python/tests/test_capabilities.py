"""Capability declaration and tier derivation."""

from __future__ import annotations

import pytest

from pact_harness.capabilities import (
    T1_REQUIREMENTS,
    T2_REQUIREMENTS,
    T3_REQUIREMENTS,
    ApplicabilityRow,
    Applies,
    Capability,
    CapabilitySet,
    Tier,
    derive_tier,
)


def test_nothing_declared_is_t0() -> None:
    assert derive_tier([]) is Tier.T0
    assert CapabilitySet().tier is Tier.T0


def test_t1_requires_tool_events() -> None:
    assert derive_tier(T1_REQUIREMENTS) is Tier.T1


def test_t2_requires_veto() -> None:
    assert derive_tier(T2_REQUIREMENTS) is Tier.T2


def test_t3_requires_the_full_set() -> None:
    assert derive_tier(T3_REQUIREMENTS) is Tier.T3


def test_one_missing_capability_drops_the_tier() -> None:
    partial = T2_REQUIREMENTS - {Capability.CONTROL_TOOL_VETO}
    assert derive_tier(partial) is Tier.T1


def test_has_all_and_missing() -> None:
    declared = CapabilitySet(frozenset({Capability.EVENT_TOOL_PRE}))
    assert declared.has(Capability.EVENT_TOOL_PRE)
    assert not declared.has_all(T1_REQUIREMENTS)
    assert declared.missing(T1_REQUIREMENTS) == frozenset({Capability.EVENT_TOOL_POST})


def test_unknown_capability_member_is_rejected() -> None:
    with pytest.raises(TypeError):
        CapabilitySet(frozenset({"not.a.capability"}))  # type: ignore[arg-type]


def test_capability_set_serialises_sorted() -> None:
    declared = CapabilitySet(
        frozenset({Capability.EVENT_TOOL_POST, Capability.EVENT_TOOL_PRE})
    )
    assert declared.to_dict() == {
        "tier": "T1",
        "capabilities": ["event.tool.post", "event.tool.pre"],
    }


def test_applicability_row_validates_its_action() -> None:
    row = ApplicabilityRow(
        hook="bootstrap_gate",
        module="hooks/bootstrap_gate.py",
        events=("tool.pre",),
        applies=Applies.RUN,
        required=frozenset({Capability.CONTROL_TOOL_VETO}),
    )
    assert row.to_dict()["applies"] == "run"
    with pytest.raises(TypeError):
        ApplicabilityRow(hook="h", module="m", events=(), applies="run")  # type: ignore[arg-type]
