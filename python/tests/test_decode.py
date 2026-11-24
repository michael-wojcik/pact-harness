"""Decoding hook processes into directives: the complete measured shape set."""

from __future__ import annotations

import json

from pact_harness.contract import Directive
from pact_harness.runtime.decode import decode_hook_output


def run(
    *,
    exit_code: int | None = 0,
    stdout: object = None,
    stderr: bytes = b"",
    timed_out: bool = False,
    launch_error: str | None = None,
    inject_target: str = "step",
):
    if stdout is None:
        data = b""
    elif isinstance(stdout, bytes):
        data = stdout
    else:
        data = json.dumps(stdout).encode("utf-8")
    return decode_hook_output(
        hook="sample_hook",
        exit_code=exit_code,
        stdout=data,
        stderr=stderr,
        timed_out=timed_out,
        launch_error=launch_error,
        inject_target=inject_target,
    )


def kinds(decoded) -> list[str]:
    return [directive.kind for directive in decoded.directives]


def test_suppress_output_is_silent() -> None:
    decoded = run(stdout={"suppressOutput": True})
    assert kinds(decoded) == ["silent"]


def test_empty_stdout_clean_exit_is_silent() -> None:
    assert kinds(run()) == ["silent"]


def test_suppress_with_hook_specific_output_is_silent() -> None:
    decoded = run(stdout={"suppressOutput": True, "hookSpecificOutput": {"hookEventName": "PreToolUse"}})
    assert kinds(decoded) == ["silent"]


def test_deny_carries_reason_and_rule() -> None:
    decoded = run(
        exit_code=2,
        stdout={
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "outside the worktree",
            }
        },
    )
    directive = decoded.directives[0]
    assert directive.kind == "deny"
    assert directive.reason == "outside the worktree"
    assert directive.rule == "sample_hook"


def test_ask_is_an_approval_request() -> None:
    decoded = run(
        stdout={
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": "confirm",
            }
        }
    )
    assert kinds(decoded) == ["ask"]
    assert decoded.directives[0].reason == "confirm"


def test_defer_allows_and_surfaces_the_reason() -> None:
    decoded = run(
        stdout={
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "defer",
                "permissionDecisionReason": "degraded",
            }
        }
    )
    assert kinds(decoded) == ["allow", "notify"]
    assert decoded.directives[1].text == "degraded"


def test_additional_context_becomes_an_injection() -> None:
    decoded = run(
        stdout={"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "do the thing"}},
        inject_target="user",
    )
    directive = decoded.directives[0]
    assert directive.kind == "inject"
    assert directive.target == "user"
    assert directive.source == "sample_hook"


def test_system_message_becomes_a_warning() -> None:
    decoded = run(stdout={"systemMessage": "heads up"})
    assert kinds(decoded) == ["notify"]
    assert decoded.directives[0].level == "warning"


def test_block_decision_is_a_turn_refusal() -> None:
    decoded = run(stdout={"decision": "block", "reason": "finish the handoff"})
    assert kinds(decoded) == ["block"]
    assert decoded.directives[0].reason == "finish the handoff"


def test_custom_instructions_is_the_compaction_channel() -> None:
    decoded = run(stdout={"custom_instructions": "summarise state"})
    assert kinds(decoded) == ["compact_instructions"]


def test_exit_two_with_stderr_only_denies() -> None:
    decoded = run(exit_code=2, stderr=b"secret detected")
    assert kinds(decoded) == ["deny"]
    assert decoded.directives[0].reason == "secret detected"


def test_exit_two_without_any_reason_still_denies() -> None:
    decoded = run(exit_code=2)
    assert kinds(decoded) == ["deny"]


def test_exit_two_with_non_decision_json_still_denies() -> None:
    decoded = run(exit_code=2, stdout={"suppressOutput": True}, stderr=b"nope")
    assert kinds(decoded) == ["deny"]
    assert decoded.directives[0].reason == "nope"


def test_malformed_json_on_a_clean_exit_is_a_decode_error() -> None:
    decoded = run(stdout=b"{not json")
    assert kinds(decoded) == ["hook_error"]
    assert decoded.directives[0].stage == "decode"
    assert decoded.directives[0].fatal is False


def test_malformed_json_on_a_refusal_still_denies() -> None:
    decoded = run(exit_code=2, stdout=b"{not json", stderr=b"refused")
    assert kinds(decoded) == ["deny"]
    assert decoded.directives[0].reason == "refused"


def test_unknown_permission_decision_fails_closed() -> None:
    decoded = run(
        stdout={"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "maybe"}}
    )
    assert kinds(decoded) == ["hook_error"]
    assert decoded.directives[0].fatal is True


def test_unknown_top_level_decision_fails_closed() -> None:
    decoded = run(stdout={"decision": "shrug"})
    assert kinds(decoded) == ["hook_error"]
    assert decoded.directives[0].fatal is True


def test_crash_is_an_exit_error_not_a_refusal() -> None:
    decoded = run(exit_code=1)
    assert kinds(decoded) == ["hook_error"]
    assert decoded.directives[0].stage == "exit"
    assert decoded.directives[0].fatal is False


def test_timeout_is_an_exit_error() -> None:
    decoded = run(exit_code=None, timed_out=True)
    assert kinds(decoded) == ["hook_error"]
    assert decoded.directives[0].fatal is False


def test_launch_failure_is_fatal() -> None:
    decoded = run(exit_code=None, launch_error="python3 not found")
    assert kinds(decoded) == ["hook_error"]
    assert decoded.directives[0].fatal is True
    assert decoded.directives[0].stage == "launch"


def test_unknown_top_level_keys_are_ignored() -> None:
    decoded = run(
        stdout={"suppressOutput": True, "pactGateHealth": {"hook": "x", "status": "failed"}}
    )
    assert kinds(decoded) == ["silent"]


def test_deny_and_context_together_keep_both() -> None:
    decoded = run(
        exit_code=2,
        stdout={
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "no",
                "additionalContext": "why",
            },
            "systemMessage": "blocked",
        },
    )
    assert kinds(decoded) == ["deny", "inject", "notify"]
    assert all(isinstance(directive, Directive) for directive in decoded.directives)
