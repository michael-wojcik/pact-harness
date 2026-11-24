"""The pinned PACT tree, and the checks that keep it pinned.

The harness never edits the vendored plugin. Two independent facts prove that
at any moment: the recorded pin in harness/pact-pin.json, and the gitlink the
superproject stores for the submodule. Inspecting both means a stray commit
inside the submodule or a moved gitlink is a reported problem rather than a
silently different PACT.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import VendorError

PIN_RELATIVE = Path("harness/pact-pin.json")
VENDOR_RELATIVE = Path("vendor/pact-plugin")


@dataclass(frozen=True, slots=True)
class PactPin:
    """The recorded upstream version the harness is built and tested against."""

    pact_version: str
    commit: str
    tag: str | None
    submodule_path: str
    repository: str

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view for the CLI."""
        return {
            "pact_version": self.pact_version,
            "commit": self.commit,
            "tag": self.tag,
            "submodule_path": self.submodule_path,
            "repository": self.repository,
        }


def load_pin(harness_root: Path) -> PactPin:
    """Read and validate harness/pact-pin.json."""
    path = harness_root / PIN_RELATIVE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VendorError(f"pin file missing: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise VendorError(f"pin file unreadable: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise VendorError(f"pin file must contain an object: {path}")
    try:
        return PactPin(
            pact_version=str(raw["pactVersion"]),
            commit=str(raw["commit"]),
            tag=(str(raw["tag"]) if raw.get("tag") else None),
            submodule_path=str(raw.get("submodulePath", VENDOR_RELATIVE.as_posix())),
            repository=str(raw.get("repository", "")),
        )
    except KeyError as exc:
        raise VendorError(f"pin file missing key {exc!s}: {path}") from exc


def _git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", str(exc)
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def _gitlink_commit(harness_root: Path, submodule_path: str) -> str | None:
    """Read the gitlink the superproject records for the submodule.

    HEAD is consulted first, then the index. The index fallback matters before
    the first commit: an unborn HEAD has no tree to read, yet the gitlink is
    already staged, and reporting "no gitlink" for a correctly staged
    superproject would fail a checkout that is in fact pinned.
    """
    code, out, _ = _git(["ls-tree", "HEAD", submodule_path], harness_root)
    if code == 0 and out:
        parts = out.split()
        if len(parts) >= 3 and parts[0] == "160000":
            return parts[2]
    code, out, _ = _git(["ls-files", "-s", "--", submodule_path], harness_root)
    if code != 0 or not out:
        return None
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) >= 4 and parts[0] == "160000":
            return parts[1]
    return None


@dataclass(frozen=True, slots=True)
class VendorState:
    """What the vendored tree currently is, and how it differs from the pin."""

    path: Path
    exists: bool
    commit: str | None = None
    describe: str | None = None
    dirty: bool = False
    gitlink_commit: str | None = None
    problems: tuple[str, ...] = field(default_factory=tuple)

    @property
    def at_pin(self) -> bool:
        """Whether the working tree is the pinned commit."""
        return bool(self.commit) and not self.problems

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view for the CLI."""
        return {
            "path": str(self.path),
            "exists": self.exists,
            "commit": self.commit,
            "describe": self.describe,
            "dirty": self.dirty,
            "gitlink_commit": self.gitlink_commit,
            "at_pin": self.at_pin,
            "problems": list(self.problems),
        }


def inspect_vendor(harness_root: Path, pin: PactPin) -> VendorState:
    """Compare the vendored tree against the recorded pin."""
    path = harness_root / pin.submodule_path
    if not path.is_dir():
        return VendorState(
            path=path,
            exists=False,
            problems=(f"vendored tree missing: {path}",),
        )

    problems: list[str] = []
    code, commit, error = _git(["rev-parse", "HEAD"], path)
    if code != 0 or not commit:
        return VendorState(
            path=path,
            exists=True,
            problems=(f"cannot read vendored commit: {error or 'git rev-parse failed'}",),
        )
    if commit != pin.commit:
        problems.append(f"vendored commit {commit} does not match pin {pin.commit}")

    describe: str | None = None
    tag_code, tag_out, _ = _git(["describe", "--tags", "--exact-match"], path)
    if tag_code == 0 and tag_out:
        describe = tag_out
    elif pin.tag:
        problems.append(f"vendored commit carries no exact tag; pin names {pin.tag}")

    status_code, status_out, _ = _git(["status", "--porcelain"], path)
    dirty = status_code == 0 and bool(status_out)
    if dirty:
        problems.append("vendored tree has uncommitted changes")

    gitlink = _gitlink_commit(harness_root, pin.submodule_path)
    if gitlink is None:
        problems.append("superproject stores no gitlink for the submodule path")
    elif gitlink != pin.commit:
        problems.append(f"gitlink {gitlink} does not match pin {pin.commit}")

    return VendorState(
        path=path,
        exists=True,
        commit=commit,
        describe=describe,
        dirty=dirty,
        gitlink_commit=gitlink,
        problems=tuple(problems),
    )
