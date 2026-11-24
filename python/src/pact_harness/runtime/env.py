"""The environment a vendored hook runs under.

Every variable here exists because a measured hook reads it. The harness sets
the ones it can derive from the event and deliberately drops the ones it cannot
honour, so a hook sees an absence it already degrades on rather than a
plausible-looking value that is wrong.

CLAUDE_ENV_FILE is the clearest case. It is Claude Code's channel for exporting
variables into later Bash tool environments, and there is no cross-platform
equivalent. Dropping it is honest: session_init writes the export only when the
variable is present, so nothing is written rather than written somewhere the
next platform will never read.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

#: Variables the harness derives and always sets.
DERIVED = ("CLAUDE_CONFIG_DIR", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PID")

#: Variables the harness removes when it cannot honour them.
SUPPRESSED = ("CLAUDE_ENV_FILE", "CLAUDE_CODE_TASK_LIST_ID")


@dataclass(frozen=True, slots=True)
class HookEnv:
    """A child-process environment, plus what was deliberately withheld."""

    values: Mapping[str, str]
    suppressed: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, str]:
        """The plain environment mapping."""
        return dict(self.values)

    @property
    def withheld(self) -> tuple[str, ...]:
        """Variables the harness removed rather than guessing a value."""
        return self.suppressed


def build_hook_env(
    *,
    state_root: Path,
    plugin_root: Path,
    session_id: str | None = None,
    project_dir: str | None = None,
    harness_pid: int | None = None,
    base_env: Mapping[str, str] | None = None,
) -> HookEnv:
    """Build the child environment for one hook invocation."""
    values: dict[str, str] = dict(os.environ if base_env is None else base_env)

    for name in SUPPRESSED:
        values.pop(name, None)

    values["CLAUDE_CONFIG_DIR"] = str(state_root)
    values["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
    values["CLAUDE_PID"] = str(os.getpid() if harness_pid is None else harness_pid)

    if project_dir:
        values["CLAUDE_PROJECT_DIR"] = project_dir
    if session_id:
        values["CLAUDE_CODE_SESSION_ID"] = session_id

    return HookEnv(values=values, suppressed=SUPPRESSED)
