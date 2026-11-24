"""Evaluate one platform event end to end.

This is the surface a platform bridge calls: give it a normalised event, get
back one envelope of directives. Everything between those two points -- the
registry, the legacy frame, the child environment, the processes, and the fold
that turns several hooks into one decision -- is this module's job.

The fold is where a harness can quietly become weaker than it claims. Several
hooks run per event; three of them may refuse. The rules here are ordered so a
refusal can never be diluted by a later hook that merely advises, and so a gate
that could not run at all is treated as a refusal on the events where a refusal
is meaningful.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .capabilities import Tier
from .contract import Directive, EnvelopeMeta, EnvelopeV1, EventV1
from .errors import PactHarnessError
from .paths import resolve_plugin_root, resolve_state_root
from .runtime.abi import HookRun, run_event
from .runtime.env import build_hook_env
from .runtime.registry import HookRegistry
from .runtime.transport import DEFAULT_TRANSPORT, HookTransport
from .runtime.vocabulary import inject_target_for

#: Events where a refusal is meaningful. Elsewhere a gate failure is advice.
VETO_CAPABLE: frozenset[str] = frozenset({"tool.pre", "prompt.submit", "turn.stop"})

#: Directives that must survive the fold even when a refusal is present.
_ADDITIVE = frozenset({"inject", "notify", "compact_instructions"})


class EngineError(PactHarnessError):
    """The engine could not evaluate an event."""


@dataclass(frozen=True, slots=True)
class EngineConfig:
    """Everything needed to evaluate an event, resolved once."""

    adapter_id: str
    plugin_root: Path
    state_root: Path
    python: str
    transport: HookTransport = DEFAULT_TRANSPORT
    base_env: Mapping[str, str] = field(default_factory=dict)
    tier: Tier = Tier.T0


def config_for(
    *,
    adapter_id: str,
    harness_root: Path | None = None,
    plugin_root: Path | None = None,
    state_root: Path | None = None,
    transport: HookTransport | None = None,
    base_env: Mapping[str, str] | None = None,
    python: str | None = None,
) -> EngineConfig:
    """Resolve an engine configuration, failing loudly on anything missing.

    The child interpreter defaults to the one running the harness. That process
    cannot be below the floor -- the package requires 3.12 -- so probing for an
    interpreter here would add a subprocess to the hot path to re-derive a fact
    already established at launch.
    """
    resolved_plugin = plugin_root or resolve_plugin_root(harness_root=harness_root)
    if resolved_plugin is None:
        raise EngineError("plugin root unresolved: set PACT_PLUGIN_ROOT or vendor the tree")
    resolved_state = (state_root or resolve_state_root(adapter=adapter_id).path)
    if python is None:
        python = sys.executable
    if not python:
        raise EngineError("no interpreter available for the hook children")
    tier = Tier.T0
    try:
        from .adapters.registry import get_adapter

        tier = get_adapter(adapter_id).capabilities.tier
    except Exception:  # noqa: BLE001 -- an unknown adapter simply claims nothing
        tier = Tier.T0
    return EngineConfig(
        adapter_id=adapter_id,
        plugin_root=resolved_plugin,
        state_root=resolved_state,
        python=python,
        transport=transport or DEFAULT_TRANSPORT,
        base_env=dict(base_env or {}),
        tier=tier,
    )


def fold(runs: tuple[HookRun, ...], event: EventV1) -> tuple[Directive, ...]:
    """Turn several hook outcomes into one ordered directive list.

    Ordering: a refusal first, then a turn refusal, then an approval request,
    then everything additive in hook order. A silent directive is dropped as
    soon as anything else is present.
    """
    refusals: list[Directive] = []
    blocks: list[Directive] = []
    asks: list[Directive] = []
    cancels: list[Directive] = []
    additive: list[Directive] = []
    veto_capable = event.kind in VETO_CAPABLE

    for run in runs:
        for directive in run.decoded.directives:
            if directive.kind == "silent":
                continue
            if directive.kind == "deny":
                refusals.append(directive)
            elif directive.kind == "block":
                blocks.append(directive)
            elif directive.kind == "ask":
                asks.append(directive)
            elif directive.kind == "cancel":
                cancels.append(directive)
            elif directive.kind == "allow":
                continue
            elif directive.kind == "hook_error":
                if directive.fatal:
                    if veto_capable:
                        refusals.append(
                            Directive.deny(
                                f"guard unavailable: {directive.message}",
                                rule=directive.hook,
                                info={"stage": directive.stage},
                            )
                        )
                    else:
                        additive.append(
                            Directive.notify(
                                f"PACT guard unavailable: {directive.hook}: {directive.message}"
                            )
                        )
                else:
                    additive.append(
                        Directive.notify(
                            f"PACT guard did not run: {directive.hook}: {directive.message}"
                        )
                    )
            elif directive.kind in _ADDITIVE:
                additive.append(directive)
            else:
                additive.append(directive)

    if refusals:
        return tuple(refusals + additive)
    if blocks:
        return tuple(blocks + additive)
    if asks:
        return tuple(asks + additive)
    if cancels:
        return tuple(cancels + additive)
    if additive:
        return tuple(additive)
    return (Directive.silent(),)


_REGISTRY_CACHE: dict[Path, HookRegistry] = {}


def registry_for(plugin_root: Path) -> HookRegistry:
    """Load the registry at most once per plugin root per process."""
    cached = _REGISTRY_CACHE.get(plugin_root)
    if cached is None:
        cached = HookRegistry.from_plugin_root(plugin_root)
        _REGISTRY_CACHE[plugin_root] = cached
    return cached


def evaluate(
    event: EventV1,
    config: EngineConfig,
    *,
    adapter: Any = None,
) -> EnvelopeV1:
    """Run every hook for one event and fold the result."""
    from .adapters.registry import get_adapter

    resolved_adapter = adapter if adapter is not None else get_adapter(config.adapter_id)

    registry = registry_for(config.plugin_root)
    started = time.monotonic()

    legacy = resolved_adapter.legacy_stdin(event)
    env = build_hook_env(
        state_root=config.state_root,
        plugin_root=config.plugin_root,
        session_id=event.session.id,
        project_dir=event.session.project_dir or event.session.cwd,
        base_env=config.base_env or None,
    )
    runs = run_event(
        registry,
        manifest_event_for(event.kind),
        stdin=legacy.data,
        env=env.values,
        python=config.python,
        tool_name=tool_name_for(event),
        cwd=event.session.cwd,
        inject_target=inject_target_for(event.kind),
        transport=config.transport,
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    directives = resolved_adapter.render(fold(runs, event), event)

    skips = tuple(
        _skip_from(run)
        for run in runs
        if run.decoded.problem and "no file_path" in run.decoded.problem
    )
    meta = EnvelopeMeta(
        hook=",".join(run.registration.hook for run in runs) or "none",
        event=event.kind,
        adapter=config.adapter_id,
        tier=config.tier,
        elapsed_ms=elapsed_ms,
        gate_skips=skips,
    )
    return EnvelopeV1(meta=meta, directives=tuple(directives))


def manifest_event_for(kind: str) -> str:
    """Map a contract kind to the manifest event name to execute."""
    return _KIND_TO_MANIFEST[kind]


def tool_name_for(event: EventV1) -> str | None:
    """The tool name a matcher should see, in canonical vocabulary."""
    if event.kind == "agent.spawn.pre":
        return "Agent"
    if event.tool is not None:
        return event.tool.canonical
    return None


def _skip_from(run: HookRun) -> Any:
    from .contract import GateSkip

    return GateSkip(gate=run.registration.hook, reason=str(run.decoded.problem))


_KIND_TO_MANIFEST: dict[str, str] = {
    "session.start": "SessionStart",
    "session.end": "SessionEnd",
    "prompt.submit": "UserPromptSubmit",
    "tool.pre": "PreToolUse",
    "tool.post": "PostToolUse",
    "tool.fail": "PostToolUseFailure",
    "agent.spawn.pre": "PreToolUse",
    "agent.start": "SubagentStart",
    "agent.stop": "SubagentStop",
    "agent.idle": "TeammateIdle",
    "task.completed": "TaskCompleted",
    "compact.pre": "PreCompact",
    "compact.post": "PostCompact",
    "turn.stop": "Stop",
}
