"""Decide whether an upstream tree still fits the harness.

The check is against the harness's declared contract, not against a recorded
snapshot. A snapshot would say "this tree is not what it was"; the harness needs
"this tree asks for something I do not do", because only the second is
actionable.

Three classes of finding.

Coverage gaps are errors: a matcher or gate tool-set name outside the canonical
vocabulary means a gate classifies a tool the adapters cannot map, which is a
silent bypass. An environment read the harness neither sets, withholds nor
documents means a hook is reading something it will never see here.

Documented absences are not findings: some variables are read by upstream and
deliberately absent on another platform, and the hooks degrade.

Inventory is a warning when a kind is empty and an error when the tree is
structurally wrong (no registry, no personas).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..runtime.vocabulary import CANONICAL_TOOLS
from .inspect import Surface

#: Variables the harness sets for every hook invocation.
SATISFIED_ENV: frozenset[str] = frozenset(
    {
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_PLUGIN_ROOT",
        "CLAUDE_PROJECT_DIR",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_PID",
    }
)

#: Variables the harness deliberately removes rather than guessing a value.
WITHHELD_ENV: frozenset[str] = frozenset({"CLAUDE_ENV_FILE", "CLAUDE_CODE_TASK_LIST_ID"})

#: Variables upstream reads that do not exist off Claude Code. Hooks must
#: already degrade on their absence; they are recorded here so a verify run
#: distinguishes "known absent" from "newly required".
KNOWN_UNSET_ENV: frozenset[str] = frozenset(
    {
        "CLAUDE_AGENT_TYPE",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_REMOTE",
        "CLAUDE_CODE_TEAM_NAME",
        "CLAUDE_LEAD",
        "CLAUDE_MD_CORPUS",
        "CLAUDE_MD_LOCK",
        "CLAUDE_MD_REFUSALS",
        "CLAUDE_ORCHESTRATOR",
        "CLAUDE_RELATIVE",
        "CLAUDE_SESSION_ID",
        "CLAUDE_SKILL_DIR",
    }
)

#: Variables owned by other tools or the operating system.
PASSTHROUGH_ENV: frozenset[str] = frozenset(
    {
        "CI",
        "GH_REPO",
        "GIT_CEILING_DIRECTORIES",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "HOME",
        "LANG",
        "PATH",
        "PWD",
        "PYTEST_CURRENT_TEST",
        "SHELL",
        "TERM",
        "TMPDIR",
        "USER",
    }
)

#: Prefixes that belong to PACT itself, Python or the host.
ALLOWED_ENV_PREFIXES: tuple[str, ...] = ("PACT_", "GIT_", "PYTHON", "LC_", "DSH_")

#: Artifact kinds the playbook compiler requires.
REQUIRED_ARTIFACT_KINDS: tuple[str, ...] = ("agents", "commands", "skills", "protocols")


@dataclass(frozen=True, slots=True)
class VerifyReport:
    """What a tree is missing, and what it merely looks like."""

    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Whether the tree still fits the harness."""
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view."""
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "facts": dict(self.facts),
        }


def _env_known(name: str) -> bool:
    if name in SATISFIED_ENV or name in WITHHELD_ENV or name in KNOWN_UNSET_ENV:
        return True
    if name in PASSTHROUGH_ENV:
        return True
    return any(name.startswith(prefix) for prefix in ALLOWED_ENV_PREFIXES)


def verify_surface(surface: Surface, *, allow_unknown_env: bool = False) -> VerifyReport:
    """Check one extracted surface against the harness's contract."""
    errors: list[str] = []
    warnings: list[str] = []

    if not surface.registrations:
        errors.append("the hook registry is empty")

    for registration in surface.registrations:
        script = registration.get("script")
        if not script:
            errors.append(f"registration {registration.get('event')} names no script")

    for matcher in surface.matchers:
        for token in (part.strip() for part in matcher.split("|")):
            if token and token not in CANONICAL_TOOLS:
                errors.append(
                    f"matcher names {token!r}, which is outside the canonical tool vocabulary"
                )

    if not surface.tool_sets:
        errors.append("no gate tool sets were found (bootstrap_gate's frozensets)")
    for name, values in sorted(surface.tool_sets.items()):
        if not values:
            errors.append(f"{name} is empty")
        for value in values:
            if value not in CANONICAL_TOOLS:
                errors.append(
                    f"{name} names {value!r}, which is outside the canonical tool vocabulary"
                )

    unknown_env = [read for read in surface.env_reads if not _env_known(read.name)]
    for read in unknown_env:
        message = (
            f"environment read {read.name!r} at {read.file}:{read.line} is not classified "
            "by the harness (not set, not withheld, not documented)"
        )
        if allow_unknown_env:
            warnings.append(message)
        else:
            errors.append(message)

    counts: dict[str, int] = {}
    for artifact in surface.artifacts:
        counts[artifact.kind] = counts.get(artifact.kind, 0) + 1
    for kind in REQUIRED_ARTIFACT_KINDS:
        if counts.get(kind, 0) == 0:
            errors.append(f"no {kind} artifacts found")

    for token, count in sorted(surface.decision_tokens.items()):
        if count == 0:
            warnings.append(f"decision token {token!r} no longer appears in the hooks")

    facts = {
        "registrations": len(surface.registrations),
        "events": len({item["event"] for item in surface.registrations}),
        "modules": len({item["hook"] for item in surface.registrations}),
        "artifacts": counts,
        "env_reads": len(surface.env_reads),
        "env_unclassified": [read.name for read in unknown_env],
        "tool_sets": {name: len(values) for name, values in sorted(surface.tool_sets.items())},
    }
    return VerifyReport(errors=tuple(errors), warnings=tuple(warnings), facts=facts)
