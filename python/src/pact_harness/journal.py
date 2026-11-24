"""The journal backend seam.

PACT's session journal is append-only and lives in a session directory that
belongs to a PACT session. A foreign agent reached over MCP has no such session,
so this backend gives it a namespace of its own under the harness state root
instead of impersonating one of PACT's session directories.

That choice is the whole design. Writing into a PACT session directory would
make harness events indistinguishable from a real session's history, and every
consumer of that journal would start reading rows nothing asked for.

The backend is a proxy: event types and their required fields belong to PACT,
and the CLI is the authority on them. The tool does not invent a vocabulary, it
reports the CLI's refusal when a caller supplies a type that does not fit.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .errors import PactHarnessError

_SLUG_INVALID = re.compile(r"[^A-Za-z0-9_-]+")


class JournalBackendError(PactHarnessError):
    """The journal backend refused or failed."""


def sanitize_session_id(value: str, fallback: str = "mcp") -> str:
    """Force a session id into a safe path component."""
    text = _SLUG_INVALID.sub("_", str(value or "")).strip("_")[:64]
    return text or fallback


class JournalBackend(Protocol):
    """Everything the harness is allowed to ask about the journal."""

    def append(
        self, event_type: str, data: Mapping[str, Any] | None, session_id: str
    ) -> dict[str, Any]:
        ...

    def read(
        self, session_id: str, *, event_type: str | None = None, since: str | None = None
    ) -> list[dict[str, Any]]:
        ...


@dataclass
class CliJournalBackend:
    """The v1 backend: PACT's own journal CLI, invoked as a subprocess."""

    plugin_root: Path
    state_root: Path
    python: str = ""

    @property
    def script(self) -> Path:
        return Path(self.plugin_root) / "hooks" / "shared" / "session_journal.py"

    @property
    def interpreter(self) -> str:
        return self.python or sys.executable

    def session_dir(self, session_id: str) -> Path:
        """The harness-owned journal directory for one foreign session."""
        return Path(self.state_root) / "pact-harness" / "journal" / sanitize_session_id(session_id)

    def _run(self, argv: list[str], *, stdin: str | None = None) -> str:
        try:
            completed = subprocess.run(
                [self.interpreter, "-I", str(self.script), *argv],
                input=stdin,
                capture_output=True,
                text=True,
                env=dict(os.environ),
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise JournalBackendError(f"journal backend unreachable: {exc}") from exc
        if completed.returncode != 0:
            raise JournalBackendError(
                (completed.stderr or completed.stdout).strip()
                or f"journal CLI exited {completed.returncode}"
            )
        return completed.stdout

    def append(
        self, event_type: str, data: Mapping[str, Any] | None, session_id: str
    ) -> dict[str, Any]:
        """Append one event through the CLI."""
        directory = self.session_dir(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        payload = dict(data or {})
        payload.setdefault("session_id", sanitize_session_id(session_id))
        stdout = self._run(
            [
                "write",
                "--type",
                event_type,
                "--session-dir",
                str(directory),
                "--data",
                json.dumps(payload),
            ]
        )
        text = stdout.strip()
        if not text:
            return {"ok": True, "type": event_type, "session_dir": str(directory)}
        try:
            parsed = json.loads(text.splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            return {"ok": True, "type": event_type, "output": text}
        return parsed if isinstance(parsed, dict) else {"ok": True, "result": parsed}

    def read(
        self, session_id: str, *, event_type: str | None = None, since: str | None = None
    ) -> list[dict[str, Any]]:
        """Read events through the CLI."""
        directory = self.session_dir(session_id)
        argv = ["read", "--session-dir", str(directory)]
        if event_type:
            argv.extend(["--type", event_type])
        if since:
            argv.extend(["--since", since])
        stdout = self._run(argv)
        text = stdout.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise JournalBackendError(f"journal CLI produced unreadable output: {exc}") from exc
        if not isinstance(parsed, list):
            raise JournalBackendError("journal CLI returned a non-list")
        return [item for item in parsed if isinstance(item, dict)]
