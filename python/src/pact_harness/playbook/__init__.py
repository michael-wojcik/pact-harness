"""Compiling PACT's model-facing artifacts for another platform.

The vendored tree is written for Claude Code: agents are persona files,
commands are slash commands with $ARGUMENTS, skills are SKILL.md bundles, and
the prose reaches its references with plugin-relative paths. A platform that is
not Claude Code cannot use any of that verbatim, so this package extracts the
artifacts, rewrites what does not survive the move, and emits one document a
platform kit can consume.
"""

from __future__ import annotations

from .compile import compile_playbook, rewrite_text, split_frontmatter
from .model import Command, Persona, Playbook, SkillSummary

__all__ = [
    "Command",
    "Persona",
    "Playbook",
    "SkillSummary",
    "compile_playbook",
    "rewrite_text",
    "split_frontmatter",
]
