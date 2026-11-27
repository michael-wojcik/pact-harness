"""Extract the upstream surface manifest.

Everything here is derived from the tree by reading it, never from a
hand-maintained list. A hand-maintained list of what the harness depends on is a
list that goes stale the first time upstream adds a hook, and the failure it
produces is silent: the harness keeps working until the day the unlisted thing
matters.

The extraction is deliberately shallow. It records the registry, the literal
tool-name sets the gates classify by, every environment read with its site, the
artifact inventory with hashes, and the counts of the decision tokens the
decoder knows. It does not try to understand the hooks.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..errors import PactHarnessError
from ..runtime.registry import HookRegistry

#: Files whose frozensets define the tool vocabulary the gates classify by.
_TOOL_SET_FILES = ("bootstrap_gate.py",)

#: The frozenset names to extract from those files.
_TOOL_SET_NAMES = ("_BLOCKED_TOOLS", "_READ_ONLY_TOOLS", "_DEGRADED_ASK_TOOLS")

#: Directories that hold model-facing artifacts.
_ARTIFACT_DIRS = ("agents", "commands", "protocols", "reference")

#: Decision tokens the decoder must understand.
_DECISION_TOKENS = (
    "permissionDecision",
    "additionalContext",
    "systemMessage",
    "custom_instructions",
    "suppressOutput",
    "hookSpecificOutput",
    "decision",
)


class InspectError(PactHarnessError):
    """The surface could not be extracted."""


@dataclass(frozen=True, slots=True)
class EnvRead:
    """One environment variable read, with the first site that reads it."""

    name: str
    file: str
    line: int

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view."""
        return {"name": self.name, "file": self.file, "line": self.line}


@dataclass(frozen=True, slots=True)
class ModuleHash:
    """One hook module and its content hash.

    Hooks are code, and code changes behavior even when the registry, matcher
    and tool-set surface is unchanged. Without this list a logic change would be
    graded L0, which is the one grade that releases without review.
    """

    path: str
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view."""
        return {"path": self.path, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class Artifact:
    """One model-facing artifact and its content hash."""

    kind: str
    path: str
    sha256: str
    bytes: int

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view."""
        return {
            "kind": self.kind,
            "path": self.path,
            "sha256": self.sha256,
            "bytes": self.bytes,
        }


@dataclass(frozen=True, slots=True)
class Surface:
    """The extracted surface manifest."""

    plugin_root: str
    registrations: tuple[dict[str, Any], ...] = ()
    matchers: tuple[str, ...] = ()
    tool_sets: dict[str, tuple[str, ...]] = field(default_factory=dict)
    env_reads: tuple[EnvRead, ...] = ()
    modules: tuple[ModuleHash, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    decision_tokens: dict[str, int] = field(default_factory=dict)
    pact_version: str = ""
    commit: str = ""
    tag: str = ""

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view, deterministic."""
        return {
            "schemaVersion": 1,
            "plugin_root": self.plugin_root,
            "pact": {
                "version": self.pact_version,
                "commit": self.commit,
                "tag": self.tag,
            },
            "registry": {
                "registrations": len(self.registrations),
                "events": len({item["event"] for item in self.registrations}),
                "modules": len({item["hook"] for item in self.registrations}),
                "items": list(self.registrations),
            },
            "matchers": list(self.matchers),
            "tool_sets": {name: list(values) for name, values in sorted(self.tool_sets.items())},
            "env_reads": [read.to_dict() for read in self.env_reads],
            "modules": [module.to_dict() for module in self.modules],
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "decision_tokens": dict(sorted(self.decision_tokens.items())),
        }


def _hooks_files(plugin_root: Path) -> tuple[Path, ...]:
    hooks = plugin_root / "hooks"
    return tuple(sorted(hooks.glob("*.py"))) + tuple(sorted((hooks / "shared").glob("*.py")))


def _relative(path: Path, plugin_root: Path) -> str:
    try:
        return path.relative_to(plugin_root).as_posix()
    except ValueError:
        return path.as_posix()


def _extract_tool_sets(plugin_root: Path) -> dict[str, tuple[str, ...]]:
    """Read the literal tool-name frozensets the gates classify by."""
    result: dict[str, tuple[str, ...]] = {}
    for name in _TOOL_SET_FILES:
        path = plugin_root / "hooks" / name
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            raise InspectError(f"cannot parse {path}: {exc}") from exc
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Name) or target.id not in _TOOL_SET_NAMES:
                    continue
                values = _literal_strings(node.value)
                if values:
                    result[target.id] = tuple(sorted(values))
    return result


def _literal_strings(node: ast.AST) -> list[str]:
    """Collect string literals from frozenset({...}) or a bare set/list."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "frozenset" and node.args:
            return _literal_strings(node.args[0])
        return []
    elements: Iterable[ast.AST]
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        elements = node.elts
    else:
        return []
    values: list[str] = []
    for element in elements:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            values.append(element.value)
    return values


def _module_string_constants(tree: ast.AST) -> dict[str, str]:
    """Collect module-level NAME = "literal" assignments.

    Upstream stores some variable names in a constant and reads the name
    through it -- CLOUD_SESSION_ENV = "CLAUDE_CODE_REMOTE" followed by
    os.environ.get(CLOUD_SESSION_ENV). Without this map the scan sees a Name
    and misses the read entirely, which is how a plain env scan under-reports.
    """
    constants: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                constants[target.id] = node.value.value
    return constants


def _extract_env_reads(plugin_root: Path) -> tuple[EnvRead, ...]:
    """Find every environment variable the hooks read, with its first site."""
    found: dict[str, EnvRead] = {}
    for path in _hooks_files(plugin_root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            raise InspectError(f"cannot parse {path}: {exc}") from exc
        constants = _module_string_constants(tree)
        relative = _relative(path, plugin_root)
        for node in ast.walk(tree):
            name = _env_name(node, constants)
            if name and name not in found:
                found[name] = EnvRead(name=name, file=relative, line=getattr(node, "lineno", 0))
    return tuple(found[name] for name in sorted(found))


def _first_string(node: ast.AST, constants: dict[str, str]) -> str | None:
    """Resolve an argument that is either a string literal or a known constant."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _env_name(node: ast.AST, constants: dict[str, str] | None = None) -> str | None:
    """Return the variable name for an os.environ.get / os.getenv / os.environ[...] node."""
    lookup = constants or {}
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id == "os" and func.attr == "getenv" and node.args:
                return _first_string(node.args[0], lookup)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Attribute):
            inner = func.value
            if (
                isinstance(inner.value, ast.Name)
                and inner.value.id == "os"
                and inner.attr == "environ"
                and func.attr == "get"
                and node.args
            ):
                return _first_string(node.args[0], lookup)
    if isinstance(node, ast.Subscript):
        value = node.value
        if (
            isinstance(value, ast.Attribute)
            and isinstance(value.value, ast.Name)
            and value.value.id == "os"
            and value.attr == "environ"
        ):
            return _first_string(node.slice, lookup)
    return None


def _artifact_files(plugin_root: Path) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for kind in _ARTIFACT_DIRS:
        directory = plugin_root / kind
        if directory.is_dir():
            files.extend((kind, path) for path in sorted(directory.glob("*.md")))
    skills = plugin_root / "skills"
    if skills.is_dir():
        files.extend(("skills", path) for path in sorted(skills.glob("*/SKILL.md")))
    return files


def _module_hashes(plugin_root: Path) -> tuple[ModuleHash, ...]:
    """Hash every hook module: the code whose behavior the harness executes."""
    result: list[ModuleHash] = []
    for path in _hooks_files(plugin_root):
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise InspectError(f"cannot read hook module {path}: {exc}") from exc
        result.append(
            ModuleHash(
                path=_relative(path, plugin_root),
                sha256=hashlib.sha256(data).hexdigest(),
            )
        )
    return tuple(result)


def _decision_token_counts(plugin_root: Path) -> dict[str, int]:
    counts = {token: 0 for token in _DECISION_TOKENS}
    for path in _hooks_files(plugin_root):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for token in _DECISION_TOKENS:
            counts[token] += text.count(token)
    return counts


def inspect_surface(
    plugin_root: Path,
    *,
    pact_version: str = "",
    commit: str = "",
    tag: str = "",
) -> Surface:
    """Extract the surface manifest from one PACT tree."""
    root = Path(plugin_root).resolve()
    if not (root / "hooks" / "hooks.json").is_file():
        raise InspectError(f"not a PACT plugin tree: {root}")
    registry = HookRegistry.from_plugin_root(root)
    artifacts: list[Artifact] = []
    for kind, path in _artifact_files(root):
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise InspectError(f"cannot read artifact {path}: {exc}") from exc
        artifacts.append(
            Artifact(
                kind=kind,
                path=_relative(path, root),
                sha256=hashlib.sha256(data).hexdigest(),
                bytes=len(data),
            )
        )
    matchers = sorted(
        {
            registration.matcher
            for registration in registry.registrations
            if registration.matcher
        }
    )
    return Surface(
        plugin_root=str(root),
        registrations=tuple(registration.to_dict() for registration in registry.registrations),
        matchers=tuple(matchers),
        tool_sets=_extract_tool_sets(root),
        env_reads=_extract_env_reads(root),
        modules=_module_hashes(root),
        artifacts=tuple(artifacts),
        decision_tokens=_decision_token_counts(root),
        pact_version=pact_version,
        commit=commit,
        tag=tag,
    )