"""How far the pin is from the latest upstream release.

The harness follows every upstream release, so which one is next is a question
it should be able to answer without a browser. The answer comes from the
repository's own tags, read with git, and only strict version tags count: a
prerelease is not a release, and a moving branch is not one either.

Being behind is information, not a failure. The command exits non-zero only when
it could not find out.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

from ..errors import PactHarnessError
from ..vendor import PactPin

_TAG_RE = re.compile(r"^refs/tags/v?(\d+)\.(\d+)\.(\d+)$")


class CheckError(PactHarnessError):
    """The release distance could not be determined."""


@dataclass(frozen=True, slots=True)
class ReleaseCheck:
    """The pin's position relative to upstream's tags."""

    repository: str
    pinned_version: str
    pinned_commit: str
    latest_version: str | None = None
    latest_tag: str | None = None
    behind: int | None = None
    newer_tags: tuple[str, ...] = ()
    checked: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view."""
        return {
            "repository": self.repository,
            "pinned_version": self.pinned_version,
            "pinned_commit": self.pinned_commit,
            "latest_version": self.latest_version,
            "latest_tag": self.latest_tag,
            "behind": self.behind,
            "newer_tags": list(self.newer_tags),
            "checked": self.checked,
            "error": self.error,
        }


def parse_release_tags(output: str) -> list[tuple[tuple[int, int, int], str, str]]:
    """Parse git ls-remote output into strict release tags.

    Returns (version tuple, tag ref, sha), sorted by version. Annotated tags
    appear twice, once as the tag object and once as a dereference; the
    dereference is skipped and the direct ref kept.
    """
    seen: dict[str, tuple[tuple[int, int, int], str, str]] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        sha, ref = parts[0], parts[1]
        match = _TAG_RE.match(ref)
        if match is None:
            continue
        version = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        seen[ref] = (version, ref, sha)
    return sorted(seen.values(), key=lambda item: item[0])


def fetch_tags(
    repository: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout: float = 30.0,
) -> str:
    """List the repository's tags with git."""
    try:
        completed = runner(
            ["git", "ls-remote", "--tags", "--refs", repository],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CheckError(f"cannot reach {repository}: {exc}") from exc
    if completed.returncode != 0:
        raise CheckError(
            f"git ls-remote failed for {repository}: "
            f"{(completed.stderr or completed.stdout).strip() or completed.returncode}"
        )
    return completed.stdout


def check_release(
    pin: PactPin,
    *,
    repository: str | None = None,
    tags_output: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> ReleaseCheck:
    """Compare the pinned version with the latest release tag."""
    url = repository or pin.repository
    if not url:
        return ReleaseCheck(
            repository="",
            pinned_version=pin.pact_version,
            pinned_commit=pin.commit,
            error="no upstream repository is recorded in the pin",
        )
    try:
        output = tags_output if tags_output is not None else fetch_tags(url, runner=runner)
    except CheckError as exc:
        return ReleaseCheck(
            repository=url,
            pinned_version=pin.pact_version,
            pinned_commit=pin.commit,
            error=str(exc),
        )
    tags = parse_release_tags(output)
    if not tags:
        return ReleaseCheck(
            repository=url,
            pinned_version=pin.pact_version,
            pinned_commit=pin.commit,
            error="no release tags found",
        )
    try:
        pinned = tuple(int(part) for part in pin.pact_version.split(".")[:3])
    except ValueError:
        pinned = ()
    if len(pinned) != 3:
        return ReleaseCheck(
            repository=url,
            pinned_version=pin.pact_version,
            pinned_commit=pin.commit,
            error=f"pinned version {pin.pact_version!r} is not a release version",
        )
    latest_version, latest_ref, _ = tags[-1]
    newer = [ref for version, ref, _ in tags if version > pinned]
    return ReleaseCheck(
        repository=url,
        pinned_version=pin.pact_version,
        pinned_commit=pin.commit,
        latest_version=".".join(str(part) for part in latest_version),
        latest_tag=latest_ref.removeprefix("refs/tags/"),
        behind=len(newer),
        newer_tags=tuple(ref.removeprefix("refs/tags/") for ref in newer),
        checked=True,
    )
