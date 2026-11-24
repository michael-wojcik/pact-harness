"""How a hook process is created.

Two transports exist in the design. The cold one starts a fresh interpreter per
hook, which is exactly how Claude Code invokes them and costs one startup plus
one import of the shared tree per call. The warm one keeps a process alive with
the shared modules already imported and forks a child per hook, which is the
same isolation with the import cost paid once.

The measured difference on this machine, for the five hooks on a PreToolUse
Edit, was 4.8 s cold against 261 ms warm plus a 0.9 s one-time warm import. That
is large enough that the warm transport is a requirement rather than an
optimization for any platform whose tool calls are interactive.

Only the cold transport ships so far. The protocol exists now so the warm one
can be added without changing any caller.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, runtime_checkable

from .launch import HookResult, launch_hook


@runtime_checkable
class HookTransport(Protocol):
    """Create one hook process and collect its outcome."""

    name: str

    def run(
        self,
        registration_script: Path,
        stdin: bytes,
        *,
        env: Mapping[str, str],
        python: str,
        timeout_s: float | None = None,
        cwd: str | None = None,
    ) -> HookResult:
        ...


@dataclass(frozen=True, slots=True)
class ColdTransport:
    """One fresh interpreter per hook: the native Claude Code behaviour."""

    name: str = "cold"

    def run(
        self,
        registration_script: Path,
        stdin: bytes,
        *,
        env: Mapping[str, str],
        python: str,
        timeout_s: float | None = None,
        cwd: str | None = None,
    ) -> HookResult:
        return launch_hook(
            registration_script,
            stdin,
            env=env,
            python=python,
            timeout_s=timeout_s,
            cwd=cwd,
        )


#: The transport used unless a caller supplies one.
DEFAULT_TRANSPORT: HookTransport = ColdTransport()


def warm_supported() -> bool:
    """Whether this platform can host the warm transport.

    The warm transport forks, so it is POSIX-only. Windows needs either a
    process pool with re-import or a different design, and neither is in scope.
    """
    return hasattr(os, "fork")
