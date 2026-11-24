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
from .adapters.registry import (
    NotImplementedAdapterError,
    available_adapters,
    detect_adapter,
    get_adapter,
)
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
from .runtime.registry import HookRegistry, RegistryError
from .runtime.transport import warm_supported
from .vendor import inspect_vendor, load_pin

EXIT_OK = 0
EXIT_PROBLEM = 1

PASSTHROUGH_AGENT = "PACT:pact-orchestrator"

_HARNESS_HELP_FLAGS = frozenset({"-h", "--help", "-V", "--version"})

_SCHEMA_DIR = Path(__file__).resolve().parent / "contract" / "schema"


def capabilities_for(adapter: str) -> CapabilitySet:
    """The capabilities an adapter declares, or nothing when it is not shipped."""
    try:
        return get_adapter(adapter).capabilities
    except NotImplementedAdapterError:
        return CapabilitySet()


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
        "adapters": list(available_adapters()),
    }
    _emit(
        payload,
        as_json=args.json,
        text=(
            f"pact-harness {__version__}\n"
            f"contract     v{CONTRACT_VERSION}\n"
            f"pact         {pin_text}\n"
            f"adapters     {', '.join(available_adapters())}"
        ),
    )
    return EXIT_OK


def _cmd_paths(args: argparse.Namespace) -> int:
    adapter = args.adapter or detect_adapter()
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
    adapter = args.adapter or detect_adapter()
    declared = capabilities_for(adapter)
    payload = {
        "adapter": adapter,
        "tier": declared.tier.value,
        "capabilities": list(declared.sorted_names()),
        "implemented": adapter in available_adapters(),
    }
    body = (
        f"adapter      : {adapter}\n"
        f"tier         : {declared.tier.value}\n"
        f"capabilities : {len(declared.capabilities)} declared"
    )
    if not payload["implemented"]:
        body += (
            "\nnote         : this adapter is not implemented in this build, so nothing is claimed"
            f" (available: {', '.join(available_adapters())})"
        )
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
            problems.append(
                f"contract schema {name} has $id {raw.get('$id')!r}, expected {schema_id!r}"
            )
    return problems


def _cmd_doctor(args: argparse.Namespace) -> int:
    adapter = args.adapter or detect_adapter()
    declared = capabilities_for(adapter)
    tier = declared.tier
    implemented = adapter in available_adapters()
    errors: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []

    if not implemented:
        notes.append(
            f"adapter {adapter!r} is not implemented in this build; "
            f"available: {', '.join(available_adapters())}"
        )

    root = find_harness_root()
    pin_payload: dict[str, Any] | None = None
    vendor_payload: dict[str, Any] | None = None
    plugin = resolve_plugin_root(harness_root=root)
    registry_payload: dict[str, Any] | None = None
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

    if plugin is None:
        errors.append("plugin root unresolved: no PACT_PLUGIN_ROOT and no vendored tree")
    else:
        try:
            registry = HookRegistry.from_plugin_root(plugin)
            registry_payload = {
                "registrations": len(registry),
                "events": len(registry.events),
                "modules": len(registry.modules()),
            }
        except RegistryError as exc:
            errors.append(str(exc))

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
        notes.append(
            f"memory directory not created yet: {memory} (upstream creates it on first memory use)"
        )

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
        "adapter_implemented": implemented,
        "available_adapters": list(available_adapters()),
        "tier": tier.value,
        "capabilities": list(declared.sorted_names()),
        "python": python.runtime.to_dict() if python.runtime else None,
        "state_root": state.to_dict(),
        "memory_dir": str(memory),
        "backlog_dir": str(resolve_backlog_dir()),
        "plugin_root": str(plugin) if plugin else None,
        "pin": pin_payload,
        "vendor": vendor_payload,
        "registry": registry_payload,
        "transport": {"cold": True, "warm": warm_supported()},
        "errors": errors,
        "warnings": warnings,
        "notes": notes,
    }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"pact-harness doctor  {__version__} (contract v{CONTRACT_VERSION})")
        label = adapter if implemented else f"{adapter} (not implemented in this build)"
        print(f"adapter      : {label}")
        print(f"tier         : {tier.value}")
        runtime = python.runtime
        if runtime:
            note = "" if runtime.certified else " (uncertified; upstream certifies 3.12)"
            print(f"python       : {runtime.version_text} at {runtime.executable} via {runtime.source}{note}")
        else:
            print("python       : none resolved")
        print(f"state root   : {state.path} ({state.source})")
        print(f"memory       : {memory} (upstream-pinned)")
        print(f"plugin root  : {plugin}")
        if registry_payload:
            print(
                "hook registry: "
                f"{registry_payload['registrations']} registrations, "
                f"{registry_payload['events']} events, "
                f"{registry_payload['modules']} modules"
            )
        if vendor_payload:
            describe = vendor_payload.get("describe") or "no exact tag"
            suffix = "" if vendor_payload.get("at_pin") else ", not at pin"
            print(
                "vendor       : "
                f"{(vendor_payload.get('commit') or 'unknown')[:12]} ({describe}){suffix}"
            )
        print(
            "contract     : "
            + ("event.v1 + directive.v1 OK" if not schema_problems else "schema problems (see errors)")
        )
        print(
            "transport    : cold"
            + ("; warm available" if warm_supported() else "; warm unavailable on this platform")
        )
        print(
            f"checks       : {len(errors)} error(s), {len(warnings)} warning(s), {len(notes)} note(s)"
        )
        for label_kind, items in (("error", errors), ("warning", warnings), ("note", notes)):
            for item in items:
                print(f"  {label_kind}: {item}")

    return EXIT_PROBLEM if errors else EXIT_OK


def _cmd_install(args: argparse.Namespace) -> int:
    """Install the bridge into a host platform."""
    from .install.dsh import InstallError, install_dsh

    if args.platform != "dsh":
        print(f"pact: install for {args.platform!r} is not implemented yet", file=sys.stderr)
        return EXIT_PROBLEM
    try:
        result = install_dsh(
            profile=args.profile,
            dsh_home=Path(args.dsh_home) if args.dsh_home else None,
            mode=args.mode,
            enforce_gates=args.enforce_gate,
            cli=args.dsh_cli,
            register=not args.no_register,
        )
    except (InstallError, PactHarnessError) as exc:
        print(f"pact: {exc}", file=sys.stderr)
        return EXIT_PROBLEM
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"installed {args.platform} into profile {result.profile!r}")
        print(f"profile dir : {result.profile_dir}")
        print(f"patch       : {result.patch_path}")
        print(f"mode        : {result.mode}")
        print(f"enforcing   : {', '.join(result.enforce_gates) or 'nothing'}")
        for note in result.notes:
            print(f"note        : {note}")
        print("next        : dsh --profile " + result.profile + " --dump-config | grep pact-dsh")
    return EXIT_OK


def _cmd_uninstall(args: argparse.Namespace) -> int:
    """Remove the bridge from a host platform."""
    from .install.dsh import InstallError, uninstall_dsh

    if args.platform != "dsh":
        print(f"pact: uninstall for {args.platform!r} is not implemented yet", file=sys.stderr)
        return EXIT_PROBLEM
    try:
        result = uninstall_dsh(
            profile=args.profile,
            dsh_home=Path(args.dsh_home) if args.dsh_home else None,
            cli=args.dsh_cli,
            unregister=not args.no_unregister,
        )
    except (InstallError, PactHarnessError) as exc:
        print(f"pact: {exc}", file=sys.stderr)
        return EXIT_PROBLEM
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"uninstalled {args.platform} from profile {result.profile!r}")
        print(f"patch       : {result.patch_path}")
        print(f"block       : {'removed' if result.block_removed else 'not present'}")
        for note in result.notes:
            print(f"note        : {note}")
    return EXIT_OK


def _cmd_event(args: argparse.Namespace) -> int:
    """Evaluate one pact.event.v1 document from stdin and print its envelope."""
    from .adapters.registry import get_adapter
    from .contract import EventV1
    from .engine import config_for, evaluate

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"pact: event stdin is not JSON: {exc}", file=sys.stderr)
        return EXIT_PROBLEM
    event = EventV1.from_dict(payload)
    adapter = get_adapter(args.adapter)
    config = config_for(
        adapter_id=args.adapter,
        plugin_root=Path(args.plugin_root) if args.plugin_root else None,
        state_root=Path(args.state_root) if args.state_root else None,
        python=args.python,
        base_env=dict(os.environ),
    )
    envelope = evaluate(event, config, adapter=adapter)
    print(json.dumps(envelope.to_dict(), ensure_ascii=False, separators=(",", ":")))
    return EXIT_OK


def _cmd_conformance(args: argparse.Namespace) -> int:
    """Run every registration against the corpus and report."""
    from .conformance.suite import run_conformance

    plugin_root = Path(args.plugin_root) if args.plugin_root else resolve_plugin_root()
    if plugin_root is None:
        print("pact: plugin root unresolved; pass --plugin-root or set PACT_PLUGIN_ROOT", file=sys.stderr)
        return EXIT_PROBLEM
    try:
        report = run_conformance(
            plugin_root,
            adapter_id=args.adapter,
            python=args.python,
            state_root=Path(args.state_root) if args.state_root else None,
        )
    except PactHarnessError as exc:
        print(f"pact: {exc}", file=sys.stderr)
        return EXIT_PROBLEM
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"conformance  : {len(report.checks)} checks across {len(report.frames)} frames")
        print(
            "registrations: "
            f"{report.exercised}/{report.total_registrations} exercised"
        )
        print(f"failures     : {len(report.failures)}")
        for failure in report.failures:
            print(f"  failed: {failure}")
        for key in report.uncovered:
            print(f"  uncovered: {key}")
    return EXIT_OK if report.ok else EXIT_PROBLEM


def _cmd_playbook_build(args: argparse.Namespace) -> int:
    """Compile PACT's artifacts for one platform."""
    from .playbook.compile import PlaybookError, compile_playbook
    from .vendor import VendorError, load_pin

    plugin_root = Path(args.plugin_root) if args.plugin_root else resolve_plugin_root()
    if plugin_root is None:
        print("pact: plugin root unresolved; pass --plugin-root or set PACT_PLUGIN_ROOT", file=sys.stderr)
        return EXIT_PROBLEM
    pact_version = ""
    commit = ""
    harness = find_harness_root()
    if harness is not None:
        try:
            pin = load_pin(harness)
            pact_version = pin.pact_version
            commit = pin.commit
        except VendorError:
            pass
    try:
        playbook = compile_playbook(
            plugin_root,
            platform=args.platform,
            pact_version=pact_version,
            commit=commit,
        )
    except PlaybookError as exc:
        print(f"pact: {exc}", file=sys.stderr)
        return EXIT_PROBLEM

    destination = Path(args.out)
    if destination.is_dir() or destination.suffix != ".json":
        destination.mkdir(parents=True, exist_ok=True)
        destination = destination / "playbook.json"
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(playbook.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if args.json:
        print(
            json.dumps(
                {
                    "out": str(destination),
                    "platform": playbook.platform,
                    "counts": playbook.to_dict()["counts"],
                    "warnings": list(playbook.warnings),
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        counts = playbook.to_dict()["counts"]
        print(f"playbook     : {destination}")
        print(f"platform     : {playbook.platform}")
        print(
            "contents     : "
            f"{counts['personas']} personas, {counts['commands']} commands, "
            f"{counts['skills']} skills, {counts['protocols']} protocols"
        )
        for warning in playbook.warnings:
            print(f"warning      : {warning}")
    return EXIT_OK


def _cmd_mcp_serve(args: argparse.Namespace) -> int:
    """Serve PACT's advisory tools over MCP stdio."""
    from .mcp.server import McpServer
    from .mcp.tools import ToolOptions

    plugin_root = Path(args.plugin_root) if args.plugin_root else resolve_plugin_root()
    if plugin_root is None:
        print("pact: plugin root unresolved; pass --plugin-root or set PACT_PLUGIN_ROOT", file=sys.stderr)
        return EXIT_PROBLEM
    options = ToolOptions(
        adapter_id=args.adapter,
        plugin_root=plugin_root,
        state_root=Path(args.state_root) if args.state_root else None,
        python=args.python,
    )
    return McpServer(options=options).serve()


def _cmd_serve(args: argparse.Namespace) -> int:
    """Run the warm hook worker until the process is terminated."""
    from .runtime.warm import WarmTransportError, serve

    plugin_root = Path(args.plugin_root) if args.plugin_root else resolve_plugin_root()
    if plugin_root is None:
        print(
            "pact: no plugin root: pass --plugin-root, set PACT_PLUGIN_ROOT, "
            "or run from a checkout with the vendored tree",
            file=sys.stderr,
        )
        return EXIT_PROBLEM
    try:
        serve(Path(args.socket), plugin_root)
    except WarmTransportError as exc:
        print(f"pact: {exc}", file=sys.stderr)
        return EXIT_PROBLEM
    return EXIT_OK


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
    paths.add_argument("--adapter", metavar="ID", help="resolve paths for this adapter")
    paths.set_defaults(func=_cmd_paths)

    capabilities = subparsers.add_parser(
        "capabilities", help="print the adapter's declared capabilities"
    )
    capabilities.add_argument("--json", action="store_true", help="print JSON")
    capabilities.add_argument("--adapter", metavar="ID", help="report this adapter")
    capabilities.set_defaults(func=_cmd_capabilities)

    doctor = subparsers.add_parser("doctor", help="report what is actually wired")
    doctor.add_argument("--json", action="store_true", help="print JSON")
    doctor.add_argument("--adapter", metavar="ID", help="report this adapter")
    doctor.add_argument(
        "--require-tier",
        metavar="TIER",
        help="fail unless the adapter reaches this tier (T0, T1, T2, T3)",
    )
    doctor.set_defaults(func=_cmd_doctor)

    install = subparsers.add_parser("install", help="install the bridge into a host platform")
    install.add_argument("platform", choices=["dsh"], help="platform to install into")
    install.add_argument("--profile", default="pact", help="DSH profile to create or update")
    install.add_argument("--dsh-home", metavar="DIR", help="DSH configuration root")
    install.add_argument("--dsh-cli", metavar="PATH", help="dsh executable to use")
    install.add_argument("--mode", default="shadow", choices=["off", "shadow", "enforce"])
    install.add_argument(
        "--enforce-gate",
        action="append",
        metavar="GATE",
        help="gate to enforce in shadow mode (repeatable; defaults to the two command gates)",
    )
    install.add_argument(
        "--no-register",
        action="store_true",
        help="skip dsh plugin add and only write the patch block",
    )
    install.add_argument("--json", action="store_true", help="print JSON")
    install.set_defaults(func=_cmd_install)

    uninstall = subparsers.add_parser("uninstall", help="remove the bridge from a host platform")
    uninstall.add_argument("platform", choices=["dsh"], help="platform to uninstall from")
    uninstall.add_argument("--profile", default="pact", help="DSH profile to update")
    uninstall.add_argument("--dsh-home", metavar="DIR", help="DSH configuration root")
    uninstall.add_argument("--dsh-cli", metavar="PATH", help="dsh executable to use")
    uninstall.add_argument(
        "--no-unregister",
        action="store_true",
        help="skip dsh plugin remove and only remove the patch block",
    )
    uninstall.add_argument("--json", action="store_true", help="print JSON")
    uninstall.set_defaults(func=_cmd_uninstall)

    event = subparsers.add_parser("event", help="evaluate one pact.event.v1 document from stdin")
    event.add_argument("--adapter", required=True, metavar="ID", help="adapter to evaluate for")
    event.add_argument("--plugin-root", metavar="DIR", help="PACT tree to run")
    event.add_argument("--state-root", metavar="DIR", help="state root for this evaluation")
    event.add_argument("--python", metavar="PATH", help="interpreter for the hook children")
    event.set_defaults(func=_cmd_event)

    playbook = subparsers.add_parser("playbook", help="compile PACT's artifacts")
    playbook_actions = playbook.add_subparsers(dest="action", required=True)
    playbook_build = playbook_actions.add_parser("build", help="build the playbook for a platform")
    playbook_build.add_argument("--platform", default="dsh", metavar="ID")
    playbook_build.add_argument("--plugin-root", metavar="DIR")
    playbook_build.add_argument(
        "--out", required=True, metavar="PATH", help="file to write, or a directory"
    )
    playbook_build.add_argument("--json", action="store_true", help="print JSON")
    playbook_build.set_defaults(func=_cmd_playbook_build)

    conformance = subparsers.add_parser(
        "conformance", help="run every hook against the corpus and report"
    )
    conformance.add_argument("--adapter", default="claude-code", metavar="ID")
    conformance.add_argument("--plugin-root", metavar="DIR")
    conformance.add_argument("--state-root", metavar="DIR")
    conformance.add_argument("--python", metavar="PATH")
    conformance.add_argument("--json", action="store_true", help="print JSON")
    conformance.set_defaults(func=_cmd_conformance)

    mcp = subparsers.add_parser("mcp", help="advisory tools over the Model Context Protocol")
    mcp_actions = mcp.add_subparsers(dest="action", required=True)
    mcp_serve = mcp_actions.add_parser("serve", help="serve MCP over stdio")
    mcp_serve.add_argument("--adapter", default="mcp", metavar="ID")
    mcp_serve.add_argument("--plugin-root", metavar="DIR")
    mcp_serve.add_argument("--state-root", metavar="DIR")
    mcp_serve.add_argument("--python", metavar="PATH")
    mcp_serve.set_defaults(func=_cmd_mcp_serve)

    serve = subparsers.add_parser("serve", help="run the warm hook worker")
    serve.add_argument("--socket", required=True, metavar="PATH", help="unix socket to bind")
    serve.add_argument(
        "--plugin-root", metavar="DIR", help="vendored PACT tree to serve (default: resolve)"
    )
    serve.set_defaults(func=_cmd_serve)

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
