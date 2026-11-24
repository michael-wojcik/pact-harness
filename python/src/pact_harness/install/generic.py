"""Install PACT's advisory surface for a platform with no hooks.

Three things make PACT usable on a platform that only reads instruction files:
the model must be told the policy exists, told where the tools are, and given
the skills. So this installer writes one managed section into AGENTS.md and
links the vendored skill directories into a shared skills root.

The section is honest about the tier. It says the tools are advisory and that
the model must honour a refusal itself, because no hook on this platform will.

Nothing is copied. The links point at the vendored tree, so an upstream bump
changes what the model reads without reinstalling anything, and uninstall can
tell its own links from links the user made by checking where they point.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..errors import PactHarnessError

MD_BEGIN = "<!-- >>> pact-harness managed (pact install generic) -->"
MD_END = "<!-- <<< pact-harness managed -->"

_BLOCK_RE = re.compile(
    r"(?ms)^" + re.escape(MD_BEGIN) + r".*?^" + re.escape(MD_END) + r"\n?"
)

_SECTION = """__MD_BEGIN__
## PACT (advisory on this platform)

This project uses PACT. Here PACT cannot observe or veto a tool call, so its
policy is available as tools you call yourself. Start the server with
`pact mcp serve` and register it with your client:

__FENCE__json
{"mcpServers": {"pact": {"command": "pact", "args": ["mcp", "serve"]}}}
__FENCE__

Before a risky tool call, call `pact_gate`:

__FENCE__json
{"tool": {"canonical": "Bash", "native": "<platform tool name>", "input": {"command": "..."}}}
__FENCE__

It answers ALLOW, ASK or DENY with the rule that fired. Honour a DENY yourself:
nothing on this platform will enforce it for you. `pact_event` evaluates a
full pact.event.v1 document, and the pact_memory_* and pact_journal_* tools
reach PACT's memory and journal backends.

### Skills

PACT's skills are linked under the shared skills root. Load the one that matches
the task before starting it. Available: __SKILLS__.

Remove this section and the links with `pact uninstall generic --target .`
__MD_END__"""


class GenericInstallError(PactHarnessError):
    """The advisory surface could not be installed."""


@dataclass(frozen=True, slots=True)
class GenericInstallResult:
    """What the installer changed."""

    target: Path
    agents_md: Path
    skills_root: Path
    linked: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view."""
        return {
            "target": str(self.target),
            "agents_md": str(self.agents_md),
            "skills_root": str(self.skills_root),
            "linked": list(self.linked),
            "skipped": list(self.skipped),
            "notes": list(self.notes),
        }


def default_skills_root() -> Path:
    """The shared skills root the AGENTS.md ecosystem reads."""
    return Path.home() / ".agents" / "skills"


def vendored_skills(plugin_root: Path) -> list[Path]:
    """Every vendored skill directory, sorted by name."""
    skills = Path(plugin_root) / "skills"
    if not skills.is_dir():
        return []
    return sorted(
        (path for path in skills.iterdir() if (path / "SKILL.md").is_file()),
        key=lambda path: path.name,
    )


def render_section(skill_names: Iterable[str]) -> str:
    """Render the managed section, naming the skills it links."""
    listed = ", ".join(sorted(skill_names)) or "(none found)"
    fence = chr(96) * 3
    text = _SECTION.replace("__MD_BEGIN__", MD_BEGIN).replace("__MD_END__", MD_END)
    text = text.replace("__FENCE__", fence).replace("__SKILLS__", listed)
    return text


def _strip_block(text: str) -> str:
    stripped = _BLOCK_RE.sub("", text)
    trimmed = stripped.rstrip()
    return trimmed + chr(10) if trimmed else ""


def _write_block(text: str, block: str) -> str:
    if text and not text.endswith(chr(10)):
        text += chr(10)
    if _BLOCK_RE.search(text):
        replaced = _BLOCK_RE.sub(lambda _match: block + chr(10), text)
        return replaced if replaced.endswith(chr(10)) else replaced + chr(10)
    separator = chr(10) if text.strip() else ""
    return text + separator + block + chr(10)


def _is_ours(link: Path, plugin_root: Path) -> bool:
    if not link.is_symlink():
        return False
    try:
        resolved = link.resolve()
    except OSError:
        return False
    try:
        resolved.relative_to((Path(plugin_root) / "skills").resolve())
    except ValueError:
        return False
    return True


def install_generic(
    target: Path,
    plugin_root: Path,
    *,
    skills_root: Path | None = None,
    link_skills: bool = True,
) -> GenericInstallResult:
    """Write the managed section and link the skills."""
    root = Path(plugin_root)
    skills = vendored_skills(root)
    if not skills:
        raise GenericInstallError(
            f"no skills found under {root / 'skills'}; is the submodule checked out?"
        )
    target_dir = Path(target)
    target_dir.mkdir(parents=True, exist_ok=True)
    agents_md = target_dir / "AGENTS.md"
    existing = agents_md.read_text(encoding="utf-8") if agents_md.is_file() else ""
    agents_md.write_text(
        _write_block(existing, render_section(skill.name for skill in skills)),
        encoding="utf-8",
    )

    shared = Path(skills_root) if skills_root is not None else default_skills_root()
    linked: list[str] = []
    skipped: list[str] = []
    notes: list[str] = []
    if link_skills:
        shared.mkdir(parents=True, exist_ok=True)
        for skill in skills:
            destination = shared / skill.name
            if destination.is_symlink():
                if _is_ours(destination, root):
                    destination.unlink()
                else:
                    skipped.append(skill.name)
                    notes.append(f"{destination} is a link this installer does not own; left alone")
                    continue
            elif destination.exists():
                skipped.append(skill.name)
                notes.append(f"{destination} already exists and is not a link; left alone")
                continue
            destination.symlink_to(skill.resolve(), target_is_directory=True)
            linked.append(skill.name)
        notes.append("the MCP server is not started by this installer; run pact mcp serve")
    return GenericInstallResult(
        target=target_dir,
        agents_md=agents_md,
        skills_root=shared,
        linked=tuple(linked),
        skipped=tuple(skipped),
        notes=tuple(notes),
    )


def uninstall_generic(
    target: Path,
    plugin_root: Path,
    *,
    skills_root: Path | None = None,
) -> GenericInstallResult:
    """Remove the managed section and the links this installer made."""
    root = Path(plugin_root)
    target_dir = Path(target)
    agents_md = target_dir / "AGENTS.md"
    removed: list[str] = []
    if agents_md.is_file():
        existing = agents_md.read_text(encoding="utf-8")
        agents_md.write_text(_strip_block(existing), encoding="utf-8")
    shared = Path(skills_root) if skills_root is not None else default_skills_root()
    if shared.is_dir():
        for link in sorted(shared.iterdir()):
            if _is_ours(link, root):
                link.unlink()
                removed.append(link.name)
    return GenericInstallResult(
        target=target_dir,
        agents_md=agents_md,
        skills_root=shared,
        linked=tuple(removed),
    )
