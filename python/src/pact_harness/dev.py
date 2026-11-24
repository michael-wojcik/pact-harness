"""The development link: exercise a working checkout instead of the pin.

Upstream work needs the harness to read an uncommitted tree. The environment
variable does that for one command; a link does it until it is removed, and it
is persisted so a later session does not have to remember the path.

A link is not a trust change. It records a directory, and resolution only uses
it while that directory still looks like a plugin tree, so a deleted or moved
checkout degrades to the pin instead of breaking every command.
"""

from __future__ import annotations

import datetime as _datetime
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import PactHarnessError
from .paths import dev_link_file, read_dev_link, plugin_dir

SCHEMA_VERSION = 1


class DevLinkError(PactHarnessError):
    """The link could not be created or read."""


@dataclass(frozen=True, slots=True)
class DevLink:
    """One recorded checkout."""

    plugin_root: Path
    linked_at: str = ""
    version: str = ""
    dirty: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view."""
        return {
            "plugin_root": str(self.plugin_root),
            "linked_at": self.linked_at,
            "version": self.version,
            "dirty": self.dirty,
        }


def tree_version(plugin_root: Path) -> str:
    """Read the version from the plugin manifest, if it states one."""
    for relative in (".claude-plugin/plugin.json", "plugin.json"):
        manifest = Path(plugin_root) / relative
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        version = data.get("version") if isinstance(data, dict) else None
        if isinstance(version, str):
            return version
    return ""


def git_dirty(plugin_root: Path) -> bool | None:
    """Whether the tree has uncommitted changes; None when it is not a repo."""
    try:
        inside = subprocess.run(
            ["git", "-C", str(plugin_root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return None
    try:
        status = subprocess.run(
            ["git", "-C", str(plugin_root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if status.returncode != 0:
        return None
    return bool(status.stdout.strip())


def link(plugin_root: Path, *, env: Mapping[str, str] | None = None) -> DevLink:
    """Record a checkout as the tree to exercise."""
    candidate = plugin_dir(Path(plugin_root).expanduser())
    if not (candidate / "hooks" / "hooks.json").is_file():
        raise DevLinkError(
            f"{candidate} is not a PACT plugin tree: no hooks/hooks.json"
        )
    resolved = candidate.resolve()
    record = {
        "schemaVersion": SCHEMA_VERSION,
        "pluginRoot": str(resolved),
        "linkedAt": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
        "version": tree_version(resolved),
    }
    destination = dev_link_file(env=env)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(record, indent=2, sort_keys=True) + chr(10), encoding="utf-8")
    return DevLink(
        plugin_root=resolved,
        linked_at=record["linkedAt"],
        version=record["version"],
        dirty=git_dirty(resolved),
    )


def unlink(*, env: Mapping[str, str] | None = None) -> DevLink | None:
    """Remove the recorded checkout, returning what was removed."""
    destination = dev_link_file(env=env)
    current = read_dev_link(env=env)
    existed = destination.is_file()
    if existed:
        try:
            destination.unlink()
        except OSError as exc:
            raise DevLinkError(f"cannot remove {destination}: {exc}") from exc
    if current is None:
        return None
    return DevLink(plugin_root=current, version=tree_version(current), dirty=git_dirty(current))


def status(*, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Report the link and whether its tree is present and clean."""
    environ = os.environ if env is None else env
    destination = dev_link_file(env=environ)
    record: dict[str, Any] = {}
    if destination.is_file():
        try:
            loaded = json.loads(destination.read_text(encoding="utf-8"))
            record = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            record = {}
    current = read_dev_link(env=environ)
    payload: dict[str, Any] = {
        "link_file": str(destination),
        "recorded": bool(record),
        "plugin_root": str(current) if current is not None else record.get("pluginRoot"),
        "present": current is not None,
        "version": tree_version(current) if current is not None else "",
        "dirty": git_dirty(current) if current is not None else None,
        "linked_at": record.get("linkedAt", ""),
    }
    return payload
