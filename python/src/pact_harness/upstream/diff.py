"""Classify what changed between two surfaces.

The classification exists so an upstream bump produces a work item rather than a
feeling. Five levels, and the level is the maximum severity of any difference:

  L0  no surface change; the bump is a version string
  L1  artifacts only (skills, commands, protocols, prose); recompile the playbook
  L2  additive ABI (a new registration, tool name, environment read); the new
      thing runs, but the harness must map it or the build fails naming it
  L3  breaking ABI (a removal, or a changed timeout or async flag); a compat shim
  L4  structural (no registry, or a required artifact kind emptied)

Two decisions are baked in and worth stating.

Registrations are keyed by (event, hook, matcher), not by their position in the
manifest. Inserting a group shifts every later index, and an index-based key
would report a reordering as a wave of removals.

A changed timeout is L3 even though it is a number: a hook that used to be
allowed ten minutes and is now allowed ten seconds is a behaviour change the
harness cannot absorb by mapping anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class Level(str, Enum):
    """How much work an upstream change implies."""

    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"

    @property
    def rank(self) -> int:
        """Numeric severity, for taking the maximum."""
        return int(self.value[1:])


@dataclass(frozen=True, slots=True)
class Difference:
    """One classified change."""

    level: Level
    kind: str
    identity: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view."""
        return {
            "level": self.level.value,
            "kind": self.kind,
            "identity": self.identity,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class DiffReport:
    """The whole comparison."""

    level: Level
    differences: tuple[Difference, ...] = ()
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def automatic(self) -> bool:
        """Whether the harness can absorb this without a code change."""
        return self.level.rank <= Level.L1.rank

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view."""
        return {
            "level": self.level.value,
            "automatic": self.automatic,
            "differences": [difference.to_dict() for difference in self.differences],
            "facts": dict(self.facts),
        }


_REQUIRED_ARTIFACT_KINDS = ("agents", "commands", "skills", "protocols")


def _registration_key(item: Mapping[str, Any]) -> str:
    return f"{item.get('event')}::{item.get('hook')}::{item.get('matcher') or ''}"


def _registrations(snapshot: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    registry = snapshot.get("registry") or {}
    items = registry.get("items") or []
    return {
        _registration_key(item): item
        for item in items
        if isinstance(item, Mapping)
    }


def _matcher_tokens(snapshot: Mapping[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for matcher in snapshot.get("matchers") or []:
        if isinstance(matcher, str):
            tokens.update(part.strip() for part in matcher.split("|") if part.strip())
    return tokens


def _tool_names(snapshot: Mapping[str, Any]) -> dict[str, set[str]]:
    sets = snapshot.get("tool_sets") or {}
    return {
        name: {str(value) for value in values}
        for name, values in sets.items()
        if isinstance(values, list)
    }


def _env_names(snapshot: Mapping[str, Any]) -> set[str]:
    return {
        str(read.get("name"))
        for read in snapshot.get("env_reads") or []
        if isinstance(read, Mapping) and read.get("name")
    }


def _modules(snapshot: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(module.get("path")): str(module.get("sha256"))
        for module in snapshot.get("modules") or []
        if isinstance(module, Mapping) and module.get("path")
    }


def _artifacts(snapshot: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(artifact.get("path")): str(artifact.get("sha256"))
        for artifact in snapshot.get("artifacts") or []
        if isinstance(artifact, Mapping) and artifact.get("path")
    }


def _artifact_kinds(snapshot: Mapping[str, Any]) -> dict[str, int]:
    kinds: dict[str, int] = {}
    for artifact in snapshot.get("artifacts") or []:
        if isinstance(artifact, Mapping):
            kind = str(artifact.get("kind"))
            kinds[kind] = kinds.get(kind, 0) + 1
    return kinds


def diff_surfaces(old: Mapping[str, Any], new: Mapping[str, Any]) -> DiffReport:
    """Compare two surface manifests."""
    differences: list[Difference] = []

    old_registry = _registrations(old)
    new_registry = _registrations(new)
    if not new_registry or not old_registry:
        differences.append(
            Difference(Level.L4, "registry", "*", "one side has no hook registry")
        )

    for key in sorted(new_registry.keys() - old_registry.keys()):
        item = new_registry[key]
        differences.append(
            Difference(
                Level.L2,
                "registration-added",
                key,
                f"new {item.get('hook')} on {item.get('event')}",
            )
        )
    for key in sorted(old_registry.keys() - new_registry.keys()):
        item = old_registry[key]
        differences.append(
            Difference(
                Level.L3,
                "registration-removed",
                key,
                f"gone: {item.get('hook')} on {item.get('event')}",
            )
        )
    for key in sorted(old_registry.keys() & new_registry.keys()):
        before, after = old_registry[key], new_registry[key]
        for attribute in ("timeout_s", "async"):
            if before.get(attribute) != after.get(attribute):
                differences.append(
                    Difference(
                        Level.L3,
                        "registration-changed",
                        key,
                        f"{attribute}: {before.get(attribute)!r} -> {after.get(attribute)!r}",
                    )
                )

    # Matcher strings and matcher tokens are both compared, because they answer
    # different questions. A token is the tool vocabulary the adapters must
    # cover. A string is a grouping: turning "Edit|Write" into "Edit" leaves
    # every token in place while silently stopping those hooks from running on
    # Write, so a token-only comparison would report no change.
    old_matchers = {str(matcher) for matcher in (old.get("matchers") or [])}
    new_matchers = {str(matcher) for matcher in (new.get("matchers") or [])}
    for matcher in sorted(new_matchers - old_matchers):
        differences.append(Difference(Level.L2, "matcher-group-added", matcher))
    for matcher in sorted(old_matchers - new_matchers):
        differences.append(Difference(Level.L3, "matcher-group-removed", matcher))

    old_tokens = _matcher_tokens(old)
    new_tokens = _matcher_tokens(new)
    for token in sorted(new_tokens - old_tokens):
        differences.append(Difference(Level.L2, "matcher-added", token))
    for token in sorted(old_tokens - new_tokens):
        differences.append(Difference(Level.L3, "matcher-removed", token))

    old_sets = _tool_names(old)
    new_sets = _tool_names(new)
    for name in sorted(new_sets.keys() - old_sets.keys()):
        differences.append(Difference(Level.L2, "tool-set-added", name))
    for name in sorted(old_sets.keys() - new_sets.keys()):
        differences.append(Difference(Level.L3, "tool-set-removed", name))
    for name in sorted(old_sets.keys() & new_sets.keys()):
        for value in sorted(new_sets[name] - old_sets[name]):
            differences.append(Difference(Level.L2, "tool-added", f"{name}:{value}"))
        for value in sorted(old_sets[name] - new_sets[name]):
            differences.append(Difference(Level.L3, "tool-removed", f"{name}:{value}"))

    old_env = _env_names(old)
    new_env = _env_names(new)
    for name in sorted(new_env - old_env):
        differences.append(Difference(Level.L2, "env-added", name))
    for name in sorted(old_env - new_env):
        differences.append(Difference(Level.L3, "env-removed", name))

    # Hook code is behavior. A body edit can change what a gate decides while the
    # registry, matchers and tool sets all stay identical, so it is graded L2:
    # nothing needs mapping, but the release needs the upstream suite and
    # conformance to prove the behavior. Removal is L3.
    old_modules = _modules(old)
    new_modules = _modules(new)
    for path in sorted(new_modules.keys() - old_modules.keys()):
        differences.append(Difference(Level.L2, "module-added", path))
    for path in sorted(old_modules.keys() - new_modules.keys()):
        differences.append(Difference(Level.L3, "module-removed", path))
    for path in sorted(old_modules.keys() & new_modules.keys()):
        if old_modules[path] != new_modules[path]:
            differences.append(Difference(Level.L2, "module-changed", path))

    old_artifacts = _artifacts(old)
    new_artifacts = _artifacts(new)
    for path in sorted(new_artifacts.keys() - old_artifacts.keys()):
        differences.append(Difference(Level.L1, "artifact-added", path))
    for path in sorted(old_artifacts.keys() - new_artifacts.keys()):
        differences.append(Difference(Level.L1, "artifact-removed", path))
    for path in sorted(old_artifacts.keys() & new_artifacts.keys()):
        if old_artifacts[path] != new_artifacts[path]:
            differences.append(Difference(Level.L1, "artifact-changed", path))

    new_kinds = _artifact_kinds(new)
    for kind in _REQUIRED_ARTIFACT_KINDS:
        if new_kinds.get(kind, 0) == 0:
            differences.append(
                Difference(Level.L4, "artifact-kind-empty", kind, "required kind is empty")
            )

    old_counts = old.get("decision_tokens") or {}
    new_counts = new.get("decision_tokens") or {}
    for token in sorted(set(old_counts) | set(new_counts)):
        before = int(old_counts.get(token) or 0)
        after = int(new_counts.get(token) or 0)
        if before == 0 and after > 0:
            differences.append(Difference(Level.L2, "decision-token-added", token))
        elif before > 0 and after == 0:
            differences.append(Difference(Level.L3, "decision-token-removed", token))

    level = Level.L0
    for difference in differences:
        if difference.level.rank > level.rank:
            level = difference.level

    facts = {
        "registrations": {
            "old": len(old_registry),
            "new": len(new_registry),
        },
        "modules": {"old": len(old_modules), "new": len(new_modules)},
        "artifacts": {"old": len(old_artifacts), "new": len(new_artifacts)},
        "pact": {
            "old": (old.get("pact") or {}).get("version"),
            "new": (new.get("pact") or {}).get("version"),
        },
    }
    return DiffReport(level=level, differences=tuple(differences), facts=facts)
