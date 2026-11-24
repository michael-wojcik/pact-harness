"""The memory CLI's venv.

The hooks are stdlib-only, so the harness never needs a virtual environment. The
memory skill is different: its CLI installs dependencies, and on an
externally-managed interpreter (PEP 668) that install is refused. The plan's
answer is a venv the memory CLI can use, created once, at a known place.

The venv is created, not adopted: if it already exists the install reports it
rather than replacing an environment that may hold downloaded models.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from ..errors import PactHarnessError

MARKER_NAME = ".pact-venv.json"


class VenvError(PactHarnessError):
    """The venv could not be created."""


@dataclass(frozen=True, slots=True)
class VenvResult:
    """Where the venv is and how it was made."""

    path: Path
    created: bool
    interpreter: str
    python_version: str = ""
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view."""
        return {
            "path": str(self.path),
            "created": self.created,
            "interpreter": self.interpreter,
            "pythonVersion": self.python_version,
            "notes": list(self.notes),
        }


def venv_root(state_root: Path | None = None, *, home: Path | None = None) -> Path:
    """The conventional venv location: inside the harness state home."""
    if state_root is not None:
        return Path(state_root) / "venv"
    base = Path(home) if home is not None else Path.home()
    return base / ".pact" / "venv"


def ensure_venv(
    path: Path,
    *,
    interpreter: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> VenvResult:
    """Create the venv if it is missing, and report what happened."""
    destination = Path(path).expanduser()
    python = interpreter or sys.executable
    notes: list[str] = []
    if (destination / MARKER_NAME).is_file():
        marker = json.loads((destination / MARKER_NAME).read_text(encoding="utf-8"))
        return VenvResult(
            path=destination,
            created=False,
            interpreter=str(marker.get("interpreter") or python),
            python_version=str(marker.get("pythonVersion") or ""),
            notes=("venv already exists; left in place",),
        )
    try:
        completed = runner(
            [python, "-m", "venv", str(destination)],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VenvError(f"cannot create {destination}: {exc}") from exc
    if completed.returncode != 0:
        raise VenvError(
            f"venv creation failed: {(completed.stderr or completed.stdout).strip() or completed.returncode}"
        )
    version = ""
    try:
        version_result = runner(
            [str(destination / "bin" / "python"), "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if version_result.returncode == 0:
            version = version_result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        version = ""
    marker = {
        "schemaVersion": 1,
        "interpreter": python,
        "pythonVersion": version,
        "path": str(destination),
    }
    (destination / MARKER_NAME).write_text(
        json.dumps(marker, indent=2, sort_keys=True) + chr(10), encoding="utf-8"
    )
    notes.append("dependencies installed into this venv do not touch the system interpreter")
    return VenvResult(
        path=destination,
        created=True,
        interpreter=python,
        python_version=version,
        notes=tuple(notes),
    )
