"""Run one hook as a child process.

Hooks are invoked exactly as Claude Code invokes them: a fresh interpreter, the
frame on stdin, the decision on stdout or in the exit code. Running them in
process would be faster and wrong, because a hook calls sys.exit, reads stdin
directly, and holds module-level state that assumes one frame per process.

Latency is the accepted cost. On the machine this harness was specified on, a
representative hook ran in 0.31 s and the slowest measured in 0.63 s.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True, slots=True)
class HookResult:
    """The complete outcome of one hook process."""

    hook: str
    script: Path
    exit: int | None
    stdout: bytes
    stderr: bytes
    duration_ms: int
    timed_out: bool = False
    launch_error: str | None = None

    @property
    def stdout_text(self) -> str:
        """stdout decoded for diagnostics."""
        return self.stdout.decode("utf-8", errors="replace")

    @property
    def stderr_text(self) -> str:
        """stderr decoded for diagnostics."""
        return self.stderr.decode("utf-8", errors="replace")


def launch_hook(
    script: Path,
    stdin: bytes,
    *,
    env: Mapping[str, str],
    python: str = "python3",
    timeout_s: float | None = None,
    cwd: str | None = None,
) -> HookResult:
    """Execute one hook and capture everything it produced."""
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [python, str(script)],
            input=stdin,
            capture_output=True,
            env=dict(env),
            cwd=cwd,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        return HookResult(
            hook=script.stem,
            script=script,
            exit=None,
            stdout=exc.stdout or b"",
            stderr=exc.stderr or b"",
            duration_ms=elapsed,
            timed_out=True,
        )
    except OSError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        return HookResult(
            hook=script.stem,
            script=script,
            exit=None,
            stdout=b"",
            stderr=b"",
            duration_ms=elapsed,
            launch_error=str(exc),
        )
    elapsed = int((time.monotonic() - started) * 1000)
    return HookResult(
        hook=script.stem,
        script=script,
        exit=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_ms=elapsed,
    )
