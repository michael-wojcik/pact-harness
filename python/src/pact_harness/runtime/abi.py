"""Execute the hooks registered for one event.

Groups run sequentially and the commands inside a group run concurrently. That
mirrors the host contract the registrations were written against, and it keeps
a three-command Bash event at the cost of its slowest hook rather than their
sum.

Concurrency is safe here because each command is a separate process with its
own stdin and its own files; the hooks that share state coordinate through
upstream's own locks. Ordering is preserved in the returned tuple so a
conformance report reads the same as hooks.json.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Iterable, Mapping

from .decode import DecodedHook, decode_hook_output
from .launch import HookResult
from .registry import HookRegistration, HookRegistry
from .transport import DEFAULT_TRANSPORT, HookTransport

#: How many hooks of one group may run at once.
MAX_PARALLEL_PER_GROUP = 8


@dataclass(frozen=True, slots=True)
class HookRun:
    """One registration's result and its decoded meaning."""

    registration: HookRegistration
    result: HookResult
    decoded: DecodedHook

    def to_dict(self) -> dict[str, object]:
        """A JSON-shaped view for conformance output."""
        return {
            "registration": self.registration.to_dict(),
            "exit": self.result.exit,
            "duration_ms": self.result.duration_ms,
            "timed_out": self.result.timed_out,
            "launch_error": self.result.launch_error,
            "stdout": self.result.stdout_text,
            "stderr": self.result.stderr_text,
            "directives": [directive.to_dict() for directive in self.decoded.directives],
            "problem": self.decoded.problem,
        }


def run_hook(
    registration: HookRegistration,
    stdin: bytes,
    *,
    env: Mapping[str, str],
    python: str,
    cwd: str | None = None,
    inject_target: str = "step",
    transport: HookTransport = DEFAULT_TRANSPORT,
) -> HookRun:
    """Run one registration and decode it."""
    result = transport.run(
        registration.script,
        stdin,
        env=env,
        python=python,
        timeout_s=registration.timeout_s,
        cwd=cwd,
    )
    decoded = decode_hook_output(
        hook=registration.hook,
        exit_code=result.exit,
        stdout=result.stdout,
        stderr=result.stderr,
        timed_out=result.timed_out,
        launch_error=result.launch_error,
        inject_target=inject_target,
    )
    return HookRun(registration=registration, result=result, decoded=decoded)


def _grouped(
    registrations: Iterable[HookRegistration],
) -> tuple[tuple[HookRegistration, ...], ...]:
    groups: dict[int, list[HookRegistration]] = {}
    order: list[int] = []
    for registration in registrations:
        if registration.group_index not in groups:
            groups[registration.group_index] = []
            order.append(registration.group_index)
        groups[registration.group_index].append(registration)
    return tuple(tuple(groups[index]) for index in order)


def run_event(
    registry: HookRegistry,
    event: str,
    *,
    stdin: bytes,
    env: Mapping[str, str],
    python: str,
    tool_name: str | None = None,
    cwd: str | None = None,
    inject_target: str = "step",
    transport: HookTransport = DEFAULT_TRANSPORT,
) -> tuple[HookRun, ...]:
    """Run every registration for one event, in manifest order."""
    registrations = registry.for_event(event, tool_name=tool_name)
    runs: list[HookRun] = []
    for group in _grouped(registrations):
        if len(group) == 1:
            runs.append(
                run_hook(
                    group[0],
                    stdin,
                    env=env,
                    python=python,
                    cwd=cwd,
                    inject_target=inject_target,
                    transport=transport,
                )
            )
            continue
        workers = min(len(group), MAX_PARALLEL_PER_GROUP)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(
                    run_hook,
                    registration,
                    stdin,
                    env=env,
                    python=python,
                    cwd=cwd,
                    inject_target=inject_target,
                    transport=transport,
                )
                for registration in group
            ]
            runs.extend(future.result() for future in futures)
    return tuple(runs)
