"""The pact command line.

Bare invocation is a passthrough. Upstream documents symlinking its own pact
launcher onto PATH, so a harness that claimed the name and then errored on a
bare invocation would break every existing user. A bare pact, or one carrying
only flags, therefore execs the same command upstream's launcher runs; the
harness's own verbs are new surface underneath.

The doctor is deliberately blunt. It reports what is wired, not what is
intended, and exits non-zero when a claim cannot be backed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import CONTRACT_VERSION, __version__
from .capabilities import CapabilitySet, Tier
from .errors import PactHarnessError, VendorError
from .paths import (
    find_harness_root,
    resolve_backlog_dir,
    resolve_memory_dir,
    resolve_plugin_root,
    resolve_python,
    resolve_state_root,
)
from .vendor import inspect_vendor, load_pin

EXIT_OK = 0
EXIT_PROBLEM = 1

PASSTHROUGH_AGENT = "PACT:pact-orchestrator"

#: Capabilities each adapter declares. Empty at M0: no adapter is wired yet,
#: and an empty set is the honest answer rather than a placeholder.
ADAPTER_CAPABILITIES: dict[str, CapabilitySet] = {}

_HARNESS_HELP_FLAGS = frozenset({"-h", "--help", "-V", "--version"})

_SCHEMA_DIR = Path(__file__).resolve().parent / "contract" / "schema"


def detect_adapter(env: Mapping[str, str] | None = None) -> str:
    """Guess the platform from the environment, refusing to prefer a guess."""
    environ = os.environ if env is None else env
    explicit = (environ.get("PACT_ADAPTER") or "").strip()
    if explicit:
        return explicit
    if (environ.get("DSH_SESSION_ID") or "").strip() or (environ.get("DSH_PROFILE") or "").strip():
        return "dsh"
    if (environ.get("CLAUDE_PLUGIN_ROOT") or "").strip():
        return "claude-code"
    return "generic"


def capabilities_for(adapter: str) -> CapabilitySet:
    """The capabilities a given adapter declares."""
    return ADAPTER_CAPABILITIES.get(adapter, CapabilitySet())


def _should_passthrough(argv: Sequence[str]) -> bool:
    """Whether this invocation belongs to the upstream launcher, not the harness."""
    if not argv:
        return True
    first = argv[0]
    if not first.startswith("-"):
        return False
    return first not in _HARNESS_HELP_FLAGS


def _launch_claude(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    which=shutil.which,
) -> int:
    """Exec the same command upstream's launcher runs, preserving usage."""
    executable = which("claude")
    if not executable:
        print(
            "pact: no subcommand given and no claude executable on PATH.\n"
            "Run pact --help for the harness commands.",
            file=sys.stderr,
        )
        return 127
    resolved_env = dict(os.environ if env is None else env)
    os.execvpe(executable, [executable, "--agent", PASSTHROUGH_AGENT, *argv], resolved_env)
    return 127  # unreachable; execvpe replaces the process


def _emit(payload: Mapping[str, Any], *, as_json: bool, text: str) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(text)


def _cmd_version(args: argparse.Namespace) -> int:
    root = find_harness_root()
    pin_text = "unknown"
    if root is not None:
        try:
            pin = load_pin(root)
            pin_text = f"v{pin.pact_version} ({pin.commit[:12]})"
        except VendorError as exc:
            pin_text = f"unknown ({exc})"
    payload = {
        "harness": __version__,
        "contract": CONTRACT_VERSION,
        "pact": pin_text,
    }
    _emit(
        payload,
        as_json=args.json,
        text=(
            f"pact-harness {__version__}\n"
            f"contract     v{CONTRACT_VERSION}\n"
            f"pact         {pin_text}"
        ),
    )
    return EXIT_OK


def _cmd_paths(args: argparse.Namespace) -> int:
    adapter = detect_adapter()
    state = resolve_state_root(adapter=adapter)
    root = find_harness_root()
    plugin = resolve_plugin_root(harness_root=root)
    cwd = Path.cwd()
    payload = {
        "adapter": adapter,
        "harness_root": str(root) if root else None,
        "plugin_root": str(plugin) if plugin else None,
        "state_root": str(state.path),
        "state_root_source": state.source,
        "memory_dir": str(resolve_memory_dir()),
        "backlog_dir": str(resolve_backlog_dir()),
        "project_dir": str(cwd),
    }
    _emit(
        payload,
        as_json=args.json,
        text=(
            f"adapter      : {adapter}\n"
            f"harness root : {payload['harness_root']}\n"
            f"plugin root  : {payload['plugin_root']}\n"
            f"state root   : {payload['state_root']} ({state.source})\n"
            f"memory       : {payload['memory_dir']} (upstream-pinned)\n"
            f"backlog      : {payload['backlog_dir']} (upstream-pinned)\n"
            f"project dir  : {payload['project_dir']}"
        ),
    )
    return EXIT_OK


def _cmd_capabilities(args: argparse.Namespace) -> int:
    adapter = detect_adapter()
    declared = capabilities_for(adapter)
    payload = {
        "adapter": adapter,
        "tier": declared.tier.value,
        "capabilities": list(declared.sorted_names()),
    }
    body = (
        f"adapter      : {adapter}\n"
        f"tier         : {declared.tier.value}\n"
        f"capabilities : {len(declared.capabilities)} declared"
    )
    if not declared.capabilities:
        body += "\nnote         : no adapter is wired in this build, so nothing is claimed"
    _emit(payload, as_json=args.json, text=body)
    return EXIT_OK


def _schema_problems() -> list[str]:
    problems: list[str] = []
    expected = {
        "event.v1.json": "pact.event.v1",
        "directive.v1.json": "pact.directive.v1",
    }
    for name, schema_id in expected.items():
        path = _SCHEMA_DIR / name
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            problems.append(f"contract schema missing: {path}")
            continue
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"contract schema unreadable: {path}: {exc}")
            continue
        if raw.get("$id") != schema_id:
            problems.append(f"contract schema {name} has $id {raw.get('$id')!r}, expected {schema_id!r}")
    return problems


def _cmd_doctor(args: argparse.Namespace) -> int:
    adapter = detect_adapter()
    declared = capabilities_for(adapter)
    tier = declared.tier
    errors: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []

    root = find_harness_root()
    pin_payload: dict[str, Any] | None = None
    vendor_payload: dict[str, Any] | None = None
    if root is None:
        errors.append("harness root not found (no harness/pact-pin.json above this package)")
    else:
        try:
            pin = load_pin(root)
            pin_payload = pin.to_dict()
            vendor = inspect_vendor(root, pin)
            vendor_payload = vendor.to_dict()
            if not vendor.exists:
                errors.append(f"vendored PACT tree missing: {vendor.path}")
            for problem in vendor.problems:
                if "uncommitted changes" in problem:
                    warnings.append(problem)
                else:
                    errors.append(problem)
        except VendorError as exc:
            errors.append(str(exc))

    plugin = resolve_plugin_root(harness_root=root)
    if plugin is None:
        errors.append("plugin root unresolved: no PACT_PLUGIN_ROOT and no vendored tree")

    python = resolve_python()
    if python.runtime is None:
        considered = ", ".join(
            f"{source}={result}" for source, _, result in python.considered
        )
        errors.append(f"no Python >= 3.12 found (considered: {considered or 'none'})")
    elif not python.runtime.certified:
        warnings.append(
            f"Python {python.runtime.version_text} is above the certified 3.12; upstream CI does not cover it"
        )

    state = resolve_state_root(adapter=adapter)
    memory = resolve_memory_dir()
    if not memory.is_dir():
        notes.append(f"memory directory not created yet: {memory} (upstream creates it on first memory use)")

    schema_problems = _schema_problems()
    errors.extend(schema_problems)

    if args.require_tier:
        try:
            required = Tier(args.require_tier)
        except ValueError:
            errors.append(f"--require-tier must be one of {[item.value for item in Tier]}")
        else:
            order = [Tier.T0, Tier.T1, Tier.T2, Tier.T3]
            if order.index(tier) < order.index(required):
                errors.append(
                    f"adapter {adapter!r} is {tier.value}, below the required {required.value}"
                )

    payload = {
        "harness": __version__,
        "contract": CONTRACT_VERSION,
        "adapter": adapter,
        "tier": tier.value,
        "capabilities": list(declared.sorted_names()),
        "python": python.runtime.to_dict() if python.runtime else None,
        "state_root": state.to_dict(),
        "memory_dir": str(memory),
        "backlog_dir": str(resolve_backlog_dir()),
        "plugin_root": str(plugin) if plugin else None,
        "pin": pin_payload,
        "vendor": vendor_payload,
        "errors": errors,
        "warnings": warnings,
        "notes": notes,
    }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"pact-harness doctor  {__version__} (contract v{CONTRACT_VERSION})")
        print(f"adapter      : {adapter}{'' if declared.capabilities else ' (no adapter wired in this build)'}")
        print(f"tier         : {tier.value}")
        runtime = python.runtime
        print(
            "python       : "
            + (
                f"{runtime.version_text} at {runtime.executable} via {runtime.source}"
                + ("" if runtime.certified else " (uncertified; upstream certifies 3.12)")
                if runtime
                else "none resolved"
            )
        )
        print(f"state root   : {state.path} ({state.source})")
        print(f"memory       : {memory} (upstream-pinned)")
        print(f"plugin root  : {plugin}")
        if vendor_payload:
            describe = vendor_payload.get("describe") or "no exact tag"
            print(
                "vendor       : "
                f"{(vendor_payload.get('commit') or 'unknown')[:12]} ({describe})"
                + ("" if vendor_payload.get("at_pin") else ", not at pin")
            )
        print(
            "contract     : "
            + ("event.v1 + directive.v1 OK" if not schema_problems else "schema problems (see errors)")
        )
        print(f"checks       : {len(errors)} error(s), {len(warnings)} warning(s), {len(notes)} note(s)")
        for label, items in (("error", errors), ("warning", warnings), ("note", notes)):
            for item in items:
                print(f"  {label}: {item}")

    return EXIT_PROBLEM if errors else EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Build the harness argument parser."""
    parser = argparse.ArgumentParser(
        prog="pact",
        description=(
            "Run PACT on any agentic coding platform. With no subcommand, pact "
            "execs the upstream Claude Code launcher."
        ),
    )
    parser.add_argument("--version", action="version", version=f"pact-harness {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    version = subparsers.add_parser("version", help="print harness, contract and PACT versions")
    version.add_argument("--json", action="store_true", help="print JSON")
    version.set_defaults(func=_cmd_version)

    paths = subparsers.add_parser("paths", help="print every resolved path")
    paths.add_argument("--json", action="store_true", help="print JSON")
    paths.set_defaults(func=_cmd_paths)

    capabilities = subparsers.add_parser("capabilities", help="print the adapter's declared capabilities")
    capabilities.add_argument("--json", action="store_true", help="print JSON")
    capabilities.set_defaults(func=_cmd_capabilities)

    doctor = subparsers.add_parser("doctor", help="report what is actually wired")
    doctor.add_argument("--json", action="store_true", help="print JSON")
    doctor.add_argument(
        "--require-tier",
        metavar="TIER",
        help="fail unless the adapter reaches this tier (T0, T1, T2, T3)",
    )
    doctor.set_defaults(func=_cmd_doctor)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if _should_passthrough(arguments):
        return _launch_claude(arguments)
    parser = build_parser()
    args = parser.parse_args(arguments)
    try:
        return int(args.func(args))
    except PactHarnessError as exc:
        print(f"pact: {exc}", file=sys.stderr)
        return EXIT_PROBLEM


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
