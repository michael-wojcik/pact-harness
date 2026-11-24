"""The memory backend seam.

The harness never opens the memory database, never composes its path from an
environment variable, and keeps no cache. It asks a backend, and the only
backend that ships shells out to the memory skill's own CLI -- which is the
route that skill declares as the only one: a caller does not choose the store,
the CLI resolves it.

The one thing the harness does resolve itself is the store's *location*, so
status can report it. That resolution runs the skill's own config module in an
isolated interpreter rather than importing it here, because importing a vendored
module into the harness process would freeze a path at import time -- exactly
the defect that module's docstring says it was rewritten to avoid.

Status stats the file and does not open it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .errors import PactHarnessError


class MemoryBackendError(PactHarnessError):
    """The backend refused or failed."""


@dataclass(frozen=True, slots=True)
class MemoryStatus:
    """What the backend says about itself."""

    backend: str
    location: str
    exists: bool
    size_bytes: int | None = None
    store: str = "sqlite"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view."""
        return {
            "backend": self.backend,
            "location": self.location,
            "exists": self.exists,
            "size_bytes": self.size_bytes,
            "store": self.store,
            "detail": self.detail,
        }


class MemoryBackend(Protocol):
    """Everything the harness is allowed to ask about memory."""

    def status(self) -> MemoryStatus:
        ...

    def save(self, memory: Mapping[str, Any]) -> dict[str, Any]:
        ...

    def search(self, query: str, limit: int = 10) -> dict[str, Any]:
        ...


@dataclass
class CliMemoryBackend:
    """The v1 backend: the memory skill's own CLI, invoked as a subprocess."""

    plugin_root: Path
    python: str = ""
    env: Mapping[str, str] | None = None

    @property
    def scripts_dir(self) -> Path:
        return Path(self.plugin_root) / "skills" / "pact-memory" / "scripts"

    @property
    def interpreter(self) -> str:
        return self.python or sys.executable

    def _run(self, argv: list[str], *, stdin: str | None = None) -> dict[str, Any]:
        environment = dict(os.environ if self.env is None else self.env)
        try:
            completed = subprocess.run(
                [self.interpreter, "-I", str(self.scripts_dir / "cli.py"), *argv],
                input=stdin,
                capture_output=True,
                text=True,
                env=environment,
                timeout=600,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise MemoryBackendError(f"memory backend unreachable: {exc}") from exc
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        if completed.returncode != 0:
            envelope: Any = None
            try:
                envelope = json.loads(stderr.splitlines()[-1]) if stderr else None
            except (json.JSONDecodeError, IndexError):
                envelope = None
            if isinstance(envelope, dict):
                raise MemoryBackendError(
                    f"{envelope.get('error') or 'MEMORY_ERROR'}: {envelope.get('message') or stderr}"
                )
            raise MemoryBackendError(stderr or f"memory CLI exited {completed.returncode}")
        try:
            parsed = json.loads(stdout) if stdout else {}
        except json.JSONDecodeError as exc:
            raise MemoryBackendError(f"memory CLI produced unreadable output: {exc}") from exc
        return parsed if isinstance(parsed, dict) else {"result": parsed}

    def _resolve_location(self) -> Path:
        code = (
            "import sys; sys.path.insert(0, %r); from config import get_memory_dir; "
            "print(get_memory_dir())" % str(self.scripts_dir)
        )
        environment = dict(os.environ if self.env is None else self.env)
        try:
            completed = subprocess.run(
                [self.interpreter, "-I", "-c", code],
                capture_output=True,
                text=True,
                env=environment,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise MemoryBackendError(f"cannot resolve the memory location: {exc}") from exc
        if completed.returncode != 0:
            raise MemoryBackendError(
                f"cannot resolve the memory location: {completed.stderr.strip() or completed.returncode}"
            )
        return Path(completed.stdout.strip())

    def status(self) -> MemoryStatus:
        """Report the backend's location and whether a store is present.

        The file is stat-ed, never opened: reading a SQLite store read-write
        would create journals beside it and change what is being measured.
        """
        location = self._resolve_location()
        store_file = location / "memory.db"
        size: int | None = None
        exists = store_file.is_file()
        if exists:
            try:
                size = store_file.stat().st_size
            except OSError:
                size = None
        return MemoryStatus(
            backend="pact-memory-cli",
            location=str(location),
            exists=exists,
            size_bytes=size,
            detail=(
                "store present"
                if exists
                else "no store yet; the backend creates one on first save"
            ),
        )

    def save(self, memory: Mapping[str, Any]) -> dict[str, Any]:
        """Save one memory object through the CLI."""
        return self._run(["save", "--stdin"], stdin=json.dumps(dict(memory)))

    def search(self, query: str, limit: int = 10) -> dict[str, Any]:
        """Search through the CLI."""
        return self._run(["search", query, "--limit", str(int(limit))])
