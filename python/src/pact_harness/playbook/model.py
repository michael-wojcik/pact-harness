"""The compiled playbook: everything a platform kit needs from PACT's prose."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Persona:
    """One specialist or the orchestrator, body rewritten for the platform."""

    name: str
    description: str
    body: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view."""
        return {
            "name": self.name,
            "description": self.description,
            "body": self.body,
            "source": self.source,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class Command:
    """One slash command, with its platform name and argument placeholder."""

    name: str
    description: str
    argument_hint: str
    body: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view."""
        return {
            "name": self.name,
            "description": self.description,
            "argument_hint": self.argument_hint,
            "body": self.body,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class SkillSummary:
    """One skill: enough for a catalog entry; the body stays on disk."""

    name: str
    description: str
    path: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view."""
        return {
            "name": self.name,
            "description": self.description,
            "path": self.path,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class Playbook:
    """The whole compiled document for one platform."""

    platform: str
    pact_version: str
    commit: str
    plugin_root: str
    personas: tuple[Persona, ...] = ()
    commands: tuple[Command, ...] = ()
    skills: tuple[SkillSummary, ...] = ()
    protocols: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    platform_notes: str = ""
    warnings: tuple[str, ...] = ()
    v: int = 1

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view, deterministic key order."""
        return {
            "v": self.v,
            "platform": self.platform,
            "pact": {"version": self.pact_version, "commit": self.commit},
            "plugin_root": self.plugin_root,
            "personas": [persona.to_dict() for persona in self.personas],
            "commands": [command.to_dict() for command in self.commands],
            "skills": [skill.to_dict() for skill in self.skills],
            "protocols": list(self.protocols),
            "references": list(self.references),
            "platform_notes": self.platform_notes,
            "warnings": list(self.warnings),
            "counts": {
                "personas": len(self.personas),
                "commands": len(self.commands),
                "skills": len(self.skills),
                "protocols": len(self.protocols),
                "references": len(self.references),
            },
        }
