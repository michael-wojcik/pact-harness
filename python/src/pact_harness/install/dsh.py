"""Install the bridge into a DeepSeek Harness profile.

Two edits, and the split matters.

The bundle itself is registered by DSH's own tooling (`dsh plugin add`), which
initialises the profile from the host's template, writes the dependency and
runs pnpm. Reimplementing that would drift from the host's layout, so the
installer shells out and reports what it ran.

The enforcement policy is not a bundle concern: it is a deployment choice, so
it goes into the profile's own patch layer as a managed block. The block is
delimited and stripped before it is rewritten, which makes install idempotent
and uninstall honest — the installer never removes a line it does not own.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence

from ..capabilities import Tier
from ..errors import PactHarnessError

PACKAGE_NAME = "@pact-harness/dsh"

MANAGED_BEGIN = "# >>> pact-harness managed (pact install dsh)"
MANAGED_END = "# <<< pact-harness managed"

_DEFAULT_MACOS_CLI = (
    Path("/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh")
)

_DEFAULT_GATES: tuple[str, ...] = ("merge_guard_pre", "git_commit_check")

_MODES = frozenset({"off", "shadow", "enforce"})

_BLOCK_RE = re.compile(
    r"(?ms)^" + re.escape(MANAGED_BEGIN) + r".*?^" + re.escape(MANAGED_END) + r"\n?"
)


class InstallError(PactHarnessError):
    """The install could not be completed."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    """One external command the installer ran."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True, slots=True)
class InstallResult:
    """What the install did, and what it could not do."""

    profile: str
    profile_dir: Path
    patch_path: Path
    mode: str
    enforce_gates: tuple[str, ...]
    commands: tuple[CommandResult, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """A JSON-shaped view for the CLI."""
        return {
            "profile": self.profile,
            "profile_dir": str(self.profile_dir),
            "patch_path": str(self.patch_path),
            "mode": self.mode,
            "enforce_gates": list(self.enforce_gates),
            "commands": [
                {"argv": list(command.argv), "returncode": command.returncode}
                for command in self.commands
            ],
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class UninstallResult:
    """What the uninstall removed."""

    profile: str
    profile_dir: Path
    patch_path: Path
    block_removed: bool
    commands: tuple[CommandResult, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """A JSON-shaped view for the CLI."""
        return {
            "profile": self.profile,
            "profile_dir": str(self.profile_dir),
            "patch_path": str(self.patch_path),
            "block_removed": self.block_removed,
            "commands": [
                {"argv": list(command.argv), "returncode": command.returncode}
                for command in self.commands
            ],
            "notes": list(self.notes),
        }


def _as_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def resolve_dsh_home(env: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    """Resolve the DSH configuration root."""
    import os

    environ = os.environ if env is None else env
    override = (environ.get("DSH_HOME") or "").strip()
    resolved_home = Path.home() if home is None else home
    if override == "~":
        return resolved_home
    if override.startswith("~/"):
        return resolved_home / override[2:]
    if override:
        return Path(override)
    return resolved_home / ".dsh"


def resolve_dsh_cli(env: Mapping[str, str] | None = None) -> str | None:
    """Find the dsh CLI, preferring an explicit override."""
    import os

    environ = os.environ if env is None else env
    override = (environ.get("DSH_CLI") or "").strip()
    if override:
        return override
    found = shutil.which("dsh")
    if found:
        return found
    if _DEFAULT_MACOS_CLI.is_file():
        return str(_DEFAULT_MACOS_CLI)
    return None


def resolve_adapter_dir(harness_root: Path | None = None) -> Path:
    """The bridged package directory inside this checkout."""
    from ..paths import find_harness_root

    root = harness_root if harness_root is not None else find_harness_root()
    if root is None:
        raise InstallError("harness root not found; cannot locate adapters/dsh")
    candidate = root / "adapters" / "dsh"
    if not (candidate / "package.json").is_file():
        raise InstallError(f"bridge package not found: {candidate}")
    return candidate


def managed_block(
    mode: str, enforce_gates: Sequence[str], playbook_path: Path | None = None
) -> str:
    """Render the profile patch entry the installer owns."""
    lines = [
        MANAGED_BEGIN,
        "- id: pact-dsh",
        "  config:",
        f"    mode: {mode}",
        "    enforceGates:",
    ]
    for gate in enforce_gates:
        lines.append(f"      - {gate}")
    if playbook_path is not None:
        lines.append(f"    playbook: {playbook_path}")
    lines.append(MANAGED_END)
    return "\n".join(lines) + "\n"


def _strip_managed(text: str) -> str:
    return _BLOCK_RE.sub("", text)


def _content_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def _is_empty_sequence(text: str) -> bool:
    lines = _content_lines(text)
    return not lines or lines == ["[]"]


def apply_managed_block(text: str, block: str) -> str:
    """Insert the managed block into a profile patch, idempotently."""
    stripped = _strip_managed(text)
    if _is_empty_sequence(stripped):
        kept = [
            line
            for line in stripped.splitlines()
            if line.strip().startswith("#") or (line.strip() and line.strip() != "[]")
        ]
        remainder = "\n".join(kept).rstrip()
        return (remainder + "\n" if remainder else "") + block
    remainder = stripped.rstrip()
    return remainder + "\n" + block


def _write_playbook(profile_dir: Path, adapter_dir: Path, notes: list[str]) -> Path | None:
    """Compile PACT's artifacts next to the profile, for the bridge to read.

    A failure here does not fail the install: the enforcement side still works
    without the personas, and a user who only wants the gates should not be
    blocked by a doc-compilation problem. The reason is reported as a note.
    """
    try:
        from ..paths import resolve_plugin_root
        from ..playbook.compile import PlaybookError, compile_playbook
        from ..vendor import VendorError, load_pin

        plugin_root = resolve_plugin_root()
        if plugin_root is None:
            notes.append("playbook not built: plugin root unresolved")
            return None
        pact_version = ""
        commit = ""
        try:
            pin = load_pin(Path(adapter_dir).parents[1])
            pact_version = pin.pact_version
            commit = pin.commit
        except (VendorError, IndexError):
            pass
        playbook = compile_playbook(
            plugin_root, platform="dsh", pact_version=pact_version, commit=commit
        )
    except Exception as exc:  # noqa: BLE001 -- reported, never fatal to the install
        notes.append(f"playbook not built: {type(exc).__name__}: {exc}")
        return None
    destination = profile_dir / "pact-playbook.json"
    destination.write_text(
        json.dumps(playbook.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    notes.append(f"playbook written: {destination}")
    for warning in playbook.warnings:
        notes.append(f"playbook warning: {warning}")
    return destination


def _run(argv: Sequence[str], *, runner: Callable[..., subprocess.CompletedProcess[str]]) -> CommandResult:
    completed = runner(list(argv), capture_output=True, text=True, check=False)
    return CommandResult(
        argv=tuple(argv),
        returncode=int(completed.returncode),
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def install_dsh(
    *,
    profile: str = "pact",
    dsh_home: Path | None = None,
    adapter_dir: Path | None = None,
    mode: str = "shadow",
    enforce_gates: Sequence[str] | None = None,
    cli: str | None = None,
    register: bool = True,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    env: Mapping[str, str] | None = None,
) -> InstallResult:
    """Install the bridge into one profile."""
    if mode not in _MODES:
        raise InstallError(f"mode must be one of {sorted(_MODES)}, got {mode!r}")
    gates = tuple(_DEFAULT_GATES if enforce_gates is None else enforce_gates)
    if mode == "off" and gates:
        gates = ()

    home = dsh_home if dsh_home is not None else resolve_dsh_home(env)
    adapter = adapter_dir if adapter_dir is not None else resolve_adapter_dir()
    commands: list[CommandResult] = []
    notes: list[str] = []

    if register:
        executable = cli if cli is not None else resolve_dsh_cli(env)
        if executable is None:
            raise InstallError(
                "dsh CLI not found: pass --dsh-cli, set DSH_CLI, or use --no-register"
            )
        result = _run(
            [executable, "plugin", "--profile", profile, "add", str(adapter)],
            runner=runner,
        )
        commands.append(result)
        if result.returncode != 0:
            raise InstallError(
                f"dsh plugin add failed ({result.returncode}): {(result.stderr or result.stdout).strip()}"
            )
    else:
        notes.append("skipped dsh plugin add; the profile must be registered separately")

    profile_dir = home / "profiles" / profile
    profile_dir.mkdir(parents=True, exist_ok=True)
    if not (profile_dir / "package.json").is_file():
        notes.append(
            f"profile {profile!r} has no package.json; the patch was written but the profile is not composed yet"
        )

    playbook_path = _write_playbook(profile_dir, adapter, notes)

    patch_path = profile_dir / "cordis.patch.yml"
    existing = patch_path.read_text(encoding="utf-8") if patch_path.is_file() else "[]\n"
    patch_path.write_text(
        apply_managed_block(existing, managed_block(mode, gates, playbook_path)),
        encoding="utf-8",
    )

    return InstallResult(
        profile=profile,
        profile_dir=profile_dir,
        patch_path=patch_path,
        mode=mode,
        enforce_gates=gates,
        commands=tuple(commands),
        notes=tuple(notes),
    )


def uninstall_dsh(
    *,
    profile: str = "pact",
    dsh_home: Path | None = None,
    cli: str | None = None,
    unregister: bool = True,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    env: Mapping[str, str] | None = None,
) -> UninstallResult:
    """Remove the managed block and, optionally, the bundle."""
    home = dsh_home if dsh_home is not None else resolve_dsh_home(env)
    profile_dir = home / "profiles" / profile
    patch_path = profile_dir / "cordis.patch.yml"
    commands: list[CommandResult] = []
    notes: list[str] = []
    removed = False

    if patch_path.is_file():
        existing = patch_path.read_text(encoding="utf-8")
        stripped = _strip_managed(existing)
        removed = stripped != existing
        patch_path.write_text(stripped if not _is_empty_sequence(stripped) else "[]\n", encoding="utf-8")

    # The compiled playbook is generated by this installer, so uninstall removes
    # it too. Leaving a 600 KB artifact behind would make "uninstalled" a lie.
    playbook_path = profile_dir / "pact-playbook.json"
    if playbook_path.is_file():
        playbook_path.unlink()

    if unregister:
        executable = cli if cli is not None else resolve_dsh_cli(env)
        if executable is None:
            notes.append("dsh CLI not found; the bundle was left registered")
        else:
            result = _run(
                [executable, "plugin", "--profile", profile, "remove", PACKAGE_NAME],
                runner=runner,
            )
            commands.append(result)
            if result.returncode != 0:
                notes.append(
                    f"dsh plugin remove exited {result.returncode}: "
                    f"{(result.stderr or result.stdout).strip()}"
                )

    return UninstallResult(
        profile=profile,
        profile_dir=profile_dir,
        patch_path=patch_path,
        block_removed=removed,
        commands=tuple(commands),
        notes=tuple(notes),
    )
