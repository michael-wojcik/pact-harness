"""Conformance: the corpus, the judgement, and coverage."""

from __future__ import annotations

from pathlib import Path

import pytest

from pact_harness import paths
from pact_harness.conformance.suite import CheckResult, ConformanceReport, _judge, run_conformance
from pact_harness.contract import Directive
from pact_harness.runtime.abi import HookRun
from pact_harness.runtime.decode import DecodedHook
from pact_harness.runtime.launch import HookResult
from pact_harness.runtime.registry import HookRegistration


def plugin_root() -> Path:
    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    candidate = paths.resolve_plugin_root(env={}, harness_root=root)
    if candidate is None or not candidate.is_dir():
        pytest.skip("vendored submodule not checked out")
    return candidate


@pytest.fixture(scope="module")
def report() -> ConformanceReport:
    return run_conformance(plugin_root())


def run_of(
    *,
    exit_code: int | None = 0,
    problem: str | None = None,
    directives: tuple[Directive, ...] = (Directive.allow(),),
    launch_error: str | None = None,
    timed_out: bool = False,
) -> HookRun:
    registration = HookRegistration(
        event="PreToolUse",
        group_index=0,
        command_index=0,
        matcher=None,
        script=Path("/tmp/h.py"),
        hook="h",
        timeout_s=None,
        async_=False,
    )
    result = HookResult(
        hook="h",
        script=registration.script,
        exit=exit_code,
        stdout=b"",
        stderr=b"",
        duration_ms=1,
        timed_out=timed_out,
        launch_error=launch_error,
    )
    return HookRun(
        registration=registration,
        result=result,
        decoded=DecodedHook(directives=directives, problem=problem),
    )


def test_the_corpus_exercises_every_registration(report: ConformanceReport) -> None:
    assert report.uncovered == ()
    assert report.exercised == report.total_registrations
    assert report.total_registrations == 34


def test_every_hook_output_is_understood(report: ConformanceReport) -> None:
    assert report.failures == ()
    assert report.checks
    assert all(check.ok for check in report.checks)


def test_the_report_serialises(report: ConformanceReport) -> None:
    payload = report.to_dict()
    assert payload["ok"] is True
    assert payload["counts"]["total_registrations"] == 34
    assert payload["counts"]["uncovered"] == 0


def test_a_refusal_without_a_refusal_directive_fails() -> None:
    check = _judge("frame", run_of(exit_code=2, directives=(Directive.silent(),)))
    assert check.ok is False
    assert "no deny or ask" in check.detail


def test_a_decode_problem_fails() -> None:
    check = _judge("frame", run_of(problem="hook stdout was not a JSON object"))
    assert check.ok is False
    assert "decode problem" in check.detail


def test_a_fatal_decode_error_fails() -> None:
    fatal = Directive.hook_error("h", stage="decode", message="unknown", fatal=True)
    check = _judge("frame", run_of(problem=None, directives=(fatal,)))
    assert check.ok is False
    assert "fatal decode error" in check.detail


def test_an_unexpected_exit_code_fails() -> None:
    check = _judge("frame", run_of(exit_code=1))
    assert check.ok is False
    assert "unexpected exit code" in check.detail


def test_a_launch_failure_fails() -> None:
    check = _judge("frame", run_of(exit_code=None, launch_error="no python"))
    assert check.ok is False
    assert "launch failed" in check.detail


def test_a_timeout_fails() -> None:
    check = _judge("frame", run_of(exit_code=None, timed_out=True))
    assert check.ok is False
    assert "timed out" in check.detail


def test_a_refusal_with_a_deny_passes() -> None:
    check = _judge("frame", run_of(exit_code=2, directives=(Directive.deny("no"),)))
    assert check.ok is True


def test_an_uncovered_registration_is_reported() -> None:
    report = ConformanceReport(checks=(), uncovered=("PreToolUse:0:0",), failures=())
    assert report.ok is False
    assert report.to_dict()["uncovered"] == ["PreToolUse:0:0"]
    assert isinstance(report.checks[0] if report.checks else None, type(None))
    assert isinstance(CheckResult, type)
