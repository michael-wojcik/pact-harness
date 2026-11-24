"""The adapter contract.

An adapter does four things and nothing else:

  normalize    native event  -> pact.event.v1
  legacy_stdin pact.event.v1 -> the bytes a vendored hook reads on stdin
  hook_env     pact.event.v1 -> the child environment that hook runs under
  render       directives    -> the platform's own decision shape

Everything a platform needs to know about PACT lives behind those four calls.
Everything PACT needs to know about a platform is its capability declaration and
its applicability table, both of which are data.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from ..capabilities import CapabilitySet
from ..contract import Directive, EventV1
from ..runtime.env import HookEnv


@dataclass(frozen=True, slots=True)
class AdapterContext:
    """Everything an adapter needs that is not in the event itself."""

    plugin_root: Path
    state_root: Path
    python: str = "python3"
    harness_pid: int = field(default_factory=os.getpid)
    base_env: Mapping[str, str] = field(default_factory=dict)

    def env_values(self) -> Mapping[str, str]:
        """The base environment to build a hook environment from."""
        return self.base_env if self.base_env else os.environ


@dataclass(frozen=True, slots=True)
class LegacyFrame:
    """The stdin a vendored hook will read, plus how it was produced."""

    data: bytes
    source: str


@runtime_checkable
class PlatformAdapter(Protocol):
    """One platform's translation layer."""

    id: str
    capabilities: CapabilitySet

    def normalize(self, native: Mapping[str, Any]) -> EventV1:
        """Turn one native event into the harness contract."""
        ...

    def legacy_stdin(self, event: EventV1) -> LegacyFrame:
        """Produce the bytes the vendored hooks read."""
        ...

    def hook_env(self, event: EventV1, context: AdapterContext) -> HookEnv:
        """Produce the child environment the hooks run under."""
        ...

    def render(
        self, directives: Sequence[Directive], event: EventV1
    ) -> list[Directive]:
        """Adjust directives for this platform's decision model."""
        ...
