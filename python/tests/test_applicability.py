"""Applicability rows: derived from the registry and the capability set, nothing more."""

from __future__ import annotations

import json

import pytest

from pact_harness import paths
from pact_harness.adapters.registry import get_adapter
from pact_harness.capabilities import Applies, applicability_for


def pin_plugin_root():
    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    candidate = paths.resolve_plugin_root(env={}, harness_root=root)
    if candidate is None or not candidate.is_dir():
        pytest.skip("vendored submodule not checked out")
    return candidate


def test_every_module_gets_exactly_one_row() -> None:
    rows = applicability_for(pin_plugin_root(), get_adapter("claude-code").capabilities)
    assert len(rows) == 28
    assert len({row.hook for row in rows}) == 28
    assert all(row.events for row in rows)
    assert all(row.module.endswith(".py") for row in rows)


def test_a_native_adapter_runs_every_module() -> None:
    rows = applicability_for(pin_plugin_root(), get_adapter("claude-code").capabilities)
    assert {row.applies for row in rows} == {Applies.RUN}


def test_an_advisory_adapter_disables_every_module_with_the_reason() -> None:
    rows = applicability_for(pin_plugin_root(), get_adapter("mcp").capabilities)
    assert {row.applies for row in rows} == {Applies.DISABLE}
    assert all("event not surfaced" in row.reason for row in rows)
    assert all(row.required for row in rows)


def test_the_doctor_prints_the_table(monkeypatch, capsys) -> None:
    from pact_harness import cli

    code = cli.main(["doctor", "--adapter", "dsh", "--json"])
    assert code in (0, 1)
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["applicability"]) == 28
    assert {row["applies"] for row in payload["applicability"]} == {"run"}
