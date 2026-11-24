"""The runtime that executes vendored PACT hooks.

The hook protocol is the harness's internal ABI. A platform adapter normalises
its event, the runtime synthesises the stdin and environment the hooks read,
runs them, and decodes their stdout and exit code back into directives.
"""

from __future__ import annotations

from .decode import decode_hook_output
from .env import build_hook_env
from .launch import HookResult, launch_hook
from .registry import HookGroup, HookRegistration, HookRegistry, matcher_matches

__all__ = [
    "HookGroup",
    "HookRegistration",
    "HookRegistry",
    "HookResult",
    "build_hook_env",
    "decode_hook_output",
    "launch_hook",
    "matcher_matches",
]
