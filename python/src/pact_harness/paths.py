"""Where the harness keeps things, and which Python runs PACT.

Two rules are worth stating because they look like details and are not.

Memory is not resolved from the state root. Upstream pins the memory database
to the home directory (its config module records that the pin is deliberate and
must not be migrated), so the harness must not "fix" it. The state root governs
sessions, teams, tasks, journals and context files; memory is reached through
the backend seam and reported by the memory backend itself.

The interpreter is resolved, never assumed. Upstream requires 3.12 and
certifies only 3.12 in CI, so a newer interpreter is allowed with a warning and
an older one is refused. The order is PACT_PYTHON, then python3, then the
interpreter DeepSeek Harness ships, which is the one we measured at 3.12.14.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

#: The upstream floor. Mirrors requires-python in PACT's pyproject.
MIN_PYTHON: tuple[int, int, int] = (3, 12, 0)

#: The interpreter version upstream CI certifies.
CERTIFIED_PYTHON: tuple[int, int] = (3, 12)

#: The DSH application bundle's own interpreter, when installed.
DSH_BUNDLED_PYTHON = (
    Path("/Applications/DeepSeek Harness.app/Contents/Resources/runtime")
    / "primary-runtime/dependencies/python/bin/python3"
)

_PIN_RELATIVE = Path("harness/pact-pin.json")
_VENDOR_RELATIVE = Path("vendor/pact-plugin")


def _expanded(value: str, home: Path) -> Path:
    text = value.strip()
    if text == "~":
        return home
    if text.startswith("~/"):
        return home / text[2:]
    return Path(text)


def find_harness_root(start: Path | None = None) -> Path | None:
    """Walk upward for the directory that holds the pin and the vendored tree."""
    current = (start or Path(__file__)).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / _PIN_RELATIVE).is_file():
            return candidate
    return None


def resolve_plugin_root(
    *,
    env: Mapping[str, str] | None = None,
    harness_root: Path | None = None,
) -> Path | None:
    """Resolve the PACT tree the hooks are read from.

    PACT_PLUGIN_ROOT wins so a working checkout can be exercised before it is
    tagged. Otherwise the vendored submodule is used.
    """
    environ = os.environ if env is None else env
    override = (environ.get("PACT_PLUGIN_ROOT") or "").strip()
    if override:
        return _expanded(override, Path.home())
    root = harness_root if harness_root is not None else find_harness_root()
    if root is None:
        return None
    candidate = root / _VENDOR_RELATIVE
    return candidate if candidate.is_dir() else None


@dataclass(frozen=True, slots=True)
class StateRoot:
    """The resolved root for sessions, teams, tasks and journals."""

    path: Path
    source: str

    def to_dict(self) -> dict[str, str]:
        """A JSON-shaped view for the CLI."""
        return {"path": str(self.path), "source": self.source}


def resolve_state_root(
    *,
    adapter: str | None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> StateRoot:
    """Resolve the state root for one adapter.

    Precedence: PACT_HOME, then (Claude Code only) CLAUDE_CONFIG_DIR, then the
    adapter default. Separate roots are the confirmed policy: Claude Code keeps
    ~/.claude so its Agent Teams integration keeps working, and every other
    adapter gets ~/.pact.
    """
    environ = os.environ if env is None else env
    resolved_home = Path.home() if home is None else home
    override = (environ.get("PACT_HOME") or "").strip()
    if override:
        return StateRoot(path=_expanded(override, resolved_home), source="PACT_HOME")
    if adapter == "claude-code":
        config_override = (environ.get("CLAUDE_CONFIG_DIR") or "").strip()
        if config_override:
            return StateRoot(
                path=_expanded(config_override, resolved_home),
                source="CLAUDE_CONFIG_DIR",
            )
        return StateRoot(path=resolved_home / ".claude", source="claude-code default")
    return StateRoot(path=resolved_home / ".pact", source="adapter default")


def resolve_memory_dir(*, home: Path | None = None) -> Path:
    """The memory database's directory, which upstream pins to the home.

    Deliberately independent of the state root and of the adapter; see this
    module's docstring.
    """
    resolved_home = Path.home() if home is None else home
    return resolved_home / ".claude" / "pact-memory"


def resolve_backlog_dir(*, home: Path | None = None) -> Path:
    """The backlog's directory, also pinned upstream to the home."""
    resolved_home = Path.home() if home is None else home
    return resolved_home / ".claude" / "pact-backlog"


@dataclass(frozen=True, slots=True)
class PythonRuntime:
    """One interpreter, resolved and versioned."""

    executable: str
    version: tuple[int, int, int]
    source: str

    @property
    def version_text(self) -> str:
        """The dotted version string."""
        return ".".join(str(part) for part in self.version)

    @property
    def meets_floor(self) -> bool:
        """Whether this interpreter satisfies upstream's declared floor."""
        return self.version >= MIN_PYTHON

    @property
    def certified(self) -> bool:
        """Whether upstream CI certifies this major.minor pair."""
        return self.version[:2] == CERTIFIED_PYTHON

    def to_dict(self) -> dict[str, object]:
        """A JSON-shaped view for the CLI."""
        return {
            "executable": self.executable,
            "version": self.version_text,
            "source": self.source,
            "meets_floor": self.meets_floor,
            "certified": self.certified,
        }


def _probe(executable: str) -> tuple[int, int, int] | None:
    """Ask one interpreter for its version, returning None when it cannot answer."""
    try:
        completed = subprocess.run(
            [executable, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    parts = completed.stdout.strip().split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return (int(parts[0]), int(parts[1]), int(parts[2]))


@dataclass(frozen=True, slots=True)
class PythonResolution:
    """The interpreter the harness would use, plus why it chose it."""

    runtime: PythonRuntime | None
    considered: tuple[tuple[str, str, str], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        """A JSON-shaped view for the CLI."""
        return {
            "chosen": self.runtime.to_dict() if self.runtime is not None else None,
            "considered": [
                {"source": source, "executable": executable, "result": result}
                for source, executable, result in self.considered
            ],
        }


def resolve_python(
    *,
    env: Mapping[str, str] | None = None,
    probe: Callable[[str], tuple[int, int, int] | None] | None = None,
) -> PythonResolution:
    """Resolve the interpreter, in the documented order."""
    environ = os.environ if env is None else env
    probe_fn = _probe if probe is None else probe
    candidates: list[tuple[str, str]] = []

    configured = (environ.get("PACT_PYTHON") or "").strip()
    if configured:
        candidates.append(("PACT_PYTHON", configured))
    candidates.append(("python3", "python3"))
    if DSH_BUNDLED_PYTHON.exists():
        candidates.append(("dsh-bundled", str(DSH_BUNDLED_PYTHON)))

    considered: list[tuple[str, str, str]] = []
    for source, executable in candidates:
        version = probe_fn(executable)
        if version is None:
            considered.append((source, executable, "unavailable"))
            continue
        considered.append((source, executable, ".".join(str(part) for part in version)))
        runtime = PythonRuntime(executable=executable, version=version, source=source)
        if runtime.meets_floor:
            return PythonResolution(runtime=runtime, considered=tuple(considered))
    return PythonResolution(runtime=None, considered=tuple(considered))


def current_python() -> PythonRuntime:
    """The interpreter running this process, for diagnostics."""
    version = tuple(sys.version_info[:3])
    return PythonRuntime(
        executable=sys.executable,
        version=(int(version[0]), int(version[1]), int(version[2])),
        source="running-process",
    )
