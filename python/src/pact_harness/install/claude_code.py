"""Bridged mode for Claude Code: a verbatim tree whose hooks call the harness.

The bridge is a copy of the plugin with one file changed -- hooks/hooks.json now
points every command at the shim instead of at the module. Nothing else about
the tree moves, because everything else already is what Claude Code wants.

Two rules keep this honest.

The install refuses to happen unless byte parity currently holds: the shim must
reproduce the hook's stdout and exit code for every corpus cell. Parity is a
precondition, not a claim, so it is re-run at install time rather than trusted
from a document.

The copy is a real copy. An installed tree that referenced the vendored one
would keep working after the vendored tree moved, which is exactly the failure
mode a pin is meant to make impossible.
"""

from __future__ import annotations

import datetime as _datetime
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..errors import PactHarnessError

MANIFEST_NAME = ".pact-bridged.json"
SCHEMA_VERSION = 1

#: One hooks.json command: a python3 invocation of a script under the plugin root.
_COMMAND_RE = re.compile(
    r"^(?P<interpreter>python3?|python3\.\d+)"
    r"\s+\"?(?P<path>\$\{CLAUDE_PLUGIN_ROOT\}[^\"']*\.py)\"?$"
)


class ClaudeCodeInstallError(PactHarnessError):
    """A bridged install could not be created or verified."""


@dataclass(frozen=True, slots=True)
class BridgedInstallResult:
    """What a bridged install produced."""

    target: Path
    commands: int = 0
    parity_cells: int = 0
    unsupported: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view."""
        return {
            "target": str(self.target),
            "commands": self.commands,
            "parityCells": self.parity_cells,
            "unsupported": list(self.unsupported),
            "notes": list(self.notes),
        }


def bridged_command(script_name: str, plugin_reference: str = "${CLAUDE_PLUGIN_ROOT}") -> str:
    """The shim invocation that replaces one direct hook command."""
    module = Path(script_name).stem
    return (
        f'python3 -m pact_harness hook {module} --adapter claude-code --passthrough '
        f'--plugin-root "{plugin_reference}"'
    )


def bridged_hooks(document: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Rewrite every hook command in one hooks.json document.

    Returns the rewritten document and the commands left alone, so a tree with an
    unexpected invocation shape is reported instead of silently half-bridged.
    """
    hooks = document.get("hooks")
    if not isinstance(hooks, Mapping):
        raise ClaudeCodeInstallError("hooks.json has no hooks object")
    rewritten: dict[str, Any] = {**document, "hooks": {}}
    unsupported: list[str] = []
    for event, groups in hooks.items():
        new_groups: list[Any] = []
        for group in groups:
            new_group = {**group} if isinstance(group, Mapping) else group
            if isinstance(new_group, dict) and isinstance(new_group.get("hooks"), list):
                new_commands = []
                for entry in new_group["hooks"]:
                    command = entry.get("command") if isinstance(entry, Mapping) else None
                    match = _COMMAND_RE.match(command.strip()) if isinstance(command, str) else None
                    if match is None:
                        unsupported.append(json.dumps(entry)[:200])
                        new_commands.append(entry)
                        continue
                    updated = dict(entry)
                    updated["command"] = bridged_command(match.group("path"))
                    new_commands.append(updated)
                new_group["hooks"] = new_commands
            new_groups.append(new_group)
        rewritten["hooks"][event] = new_groups
    return rewritten, unsupported


def install_bridged(
    target: Path,
    plugin_root: Path,
    *,
    verify_parity: bool = True,
    python: str | None = None,
) -> BridgedInstallResult:
    """Copy the plugin and rewrite its hooks.json to call the shim."""
    from ..conformance.parity import run_parity

    source = Path(plugin_root)
    destination = Path(target).expanduser()
    if not (source / "hooks" / "hooks.json").is_file():
        raise ClaudeCodeInstallError(f"{source} is not a PACT plugin tree")
    if destination.exists() and any(destination.iterdir()):
        raise ClaudeCodeInstallError(
            f"{destination} is not empty; a bridged install owns its directory"
        )
    parity_cells = 0
    if verify_parity:
        report = run_parity(source, python=python)
        if not report.ok:
            raise ClaudeCodeInstallError(
                "byte parity does not hold, so bridged mode would change behavior: "
                + "; ".join(report.failures[:5])
            )
        parity_cells = len(report.cells)

    shutil.copytree(
        source,
        destination,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"),
    )
    hooks_path = destination / "hooks" / "hooks.json"
    document = json.loads(hooks_path.read_text(encoding="utf-8"))
    rewritten, unsupported = bridged_hooks(document)
    hooks_path.write_text(json.dumps(rewritten, indent=2) + chr(10), encoding="utf-8")

    commands = sum(
        len(group.get("hooks", []))
        for groups in rewritten["hooks"].values()
        for group in groups
        if isinstance(group, Mapping)
    )
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "sourcePlugin": str(source.resolve()),
        "generatedAt": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
        "commands": commands,
        "parityCells": parity_cells,
        "unsupported": list(unsupported),
    }
    (destination / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + chr(10), encoding="utf-8"
    )
    notes = [
        "point Claude Code at this plugin directory instead of the vendored one",
        "the shim needs pact_harness importable: pip install pact-harness",
    ]
    if unsupported:
        notes.append(f"{len(unsupported)} command(s) were left unbridged; see the manifest")
    return BridgedInstallResult(
        target=destination,
        commands=commands,
        parity_cells=parity_cells,
        unsupported=tuple(unsupported),
        notes=tuple(notes),
    )


def uninstall_bridged(target: Path) -> dict[str, Any]:
    """Remove a bridged install, refusing a directory this installer did not make."""
    destination = Path(target).expanduser()
    manifest_path = destination / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ClaudeCodeInstallError(
            f"{destination} has no {MANIFEST_NAME}; refusing to remove a directory that is not ours"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    shutil.rmtree(destination)
    return manifest if isinstance(manifest, dict) else {}
