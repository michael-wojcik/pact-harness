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
    plugin_dir,
    plugin_source,
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
        "plugin_source": plugin_source(),
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
            f"plugin root  : {payload['plugin_root']} ({payload['plugin_source']})\n"
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
    from .dev import git_dirty

    source = plugin_source()
    plugin_dirty = git_dirty(plugin) if plugin is not None else None
    if source == "dev-link":
        notes.append("plugin root is a development link; remove it with: pact dev unlink")
    if plugin_dirty:
        warnings.append("the tree in use has uncommitted changes (development link or PACT_PLUGIN_ROOT)")
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

    modules_payload: list[dict[str, object]] | None = None
    if plugin is not None:
        try:
            modules: dict[str, dict[str, object]] = {}
            for registration in HookRegistry.from_plugin_root(plugin).registrations:
                entry = modules.setdefault(
                    registration.hook,
                    {"hook": registration.hook, "registrations": 0, "events": [], "async": False},
                )
                entry["registrations"] = int(entry["registrations"]) + 1
                entry["async"] = bool(entry["async"]) or registration.async_
                events = entry["events"]
                if isinstance(events, list) and registration.event not in events:
                    events.append(registration.event)
            modules_payload = [modules[name] for name in sorted(modules)]
        except RegistryError:
            modules_payload = None

    applicability_payload: list[dict[str, object]] | None = None
    if plugin is not None and implemented:
        try:
            from .capabilities import applicability_for

            applicability_payload = [
                row.to_dict() for row in applicability_for(plugin, declared)
            ]
        except PactHarnessError:
            applicability_payload = None

    activation_payload: dict[str, object] | None = None
    activation_error: str | None = None
    activation_path = state.path / "pact-harness" / "dsh-activation.json"
    if activation_path.is_file():
        try:
            loaded = json.loads(activation_path.read_text(encoding="utf-8"))
            activation_payload = loaded if isinstance(loaded, dict) else {"unreadable": True}
        except (OSError, ValueError) as exc:
            activation_error = f"cannot read {activation_path}: {exc}"

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
        "plugin_source": source,
        "plugin_dirty": plugin_dirty,
        "pin": pin_payload,
        "vendor": vendor_payload,
        "registry": registry_payload,
        "modules": modules_payload,
        "applicability": applicability_payload,
        "activation": activation_payload,
        "activation_error": activation_error,
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
        source_suffix = "" if source == "vendored" else f" ({source})"
        dirty_suffix = ", dirty" if plugin_dirty else ""
        print(f"plugin root  : {plugin}{source_suffix}{dirty_suffix}")
        if registry_payload:
            print(
                "hook registry: "
                f"{registry_payload['registrations']} registrations, "
                f"{registry_payload['events']} events, "
                f"{registry_payload['modules']} modules"
            )
        if applicability_payload:
            counts: dict[str, int] = {}
            for row in applicability_payload:
                action = str(row.get("applies"))
                counts[action] = counts.get(action, 0) + 1
            summary = ", ".join(f"{value} {name}" for name, value in sorted(counts.items()))
            print(f"applicability: {summary}")
        if modules_payload:
            print(f"hook modules : {len(modules_payload)}")
            if activation_payload:
                print(
                    "activation   : "
                    f"mode {activation_payload.get('mode')} at {activation_payload.get('activatedAt')}"
                )
            else:
                print("activation   : none recorded for this adapter")
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


def _cmd_install_generic(args: argparse.Namespace, *, uninstall: bool = False) -> int:
    """Write or remove the advisory surface for an AGENTS.md platform."""
    from .install.generic import (
        GenericInstallError,
        install_generic,
        uninstall_generic,
    )

    plugin_root = _plugin_root_from(getattr(args, "plugin_root", None))
    if plugin_root is None:
        print("pact: plugin root unresolved; pass --plugin-root or set PACT_PLUGIN_ROOT", file=sys.stderr)
        return EXIT_PROBLEM
    target = Path(args.target).expanduser()
    skills_root = Path(args.skills_root).expanduser() if getattr(args, "skills_root", None) else None
    try:
        if uninstall:
            result = uninstall_generic(target, plugin_root, skills_root=skills_root)
        else:
            result = install_generic(
                target,
                plugin_root,
                skills_root=skills_root,
                link_skills=not getattr(args, "no_skills", False),
            )
    except (GenericInstallError, PactHarnessError) as exc:
        print(f"pact: {exc}", file=sys.stderr)
        return EXIT_PROBLEM
    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return EXIT_OK
    verb = "uninstalled" if uninstall else "installed"
    print(f"{verb} generic advisory surface")
    print(f"target      : {result.target}")
    print(f"agents.md   : {result.agents_md}")
    print(f"skills root : {result.skills_root}")
    print(f"skills      : {len(result.linked)} linked")
    for name in result.skipped:
        print(f"skipped     : {name}")
    for note in result.notes:
        print(f"note        : {note}")
    if not uninstall:
        print("next        : start the server with: pact mcp serve")
    return EXIT_OK


def _cmd_uninstall_generic(args: argparse.Namespace) -> int:
    """Remove the advisory surface."""
    return _cmd_install_generic(args, uninstall=True)


def _cmd_install(args: argparse.Namespace) -> int:
    """Install the bridge into a host platform."""
    from .install.dsh import InstallError, install_dsh

    if getattr(args, "venv", False):
        from .install.venv import VenvError, ensure_venv, venv_root

        state = resolve_state_root(adapter=args.platform)
        try:
            venv = ensure_venv(venv_root(state_root=state.path))
        except VenvError as exc:
            print(f"pact: {exc}", file=sys.stderr)
            return EXIT_PROBLEM
        state_word = "created" if venv.created else "reused"
        line = f"venv {state_word}: {venv.path} ({venv.python_version or 'unknown'})"
        print(line, file=sys.stderr if args.json else sys.stdout)

    if args.platform == "generic":
        return _cmd_install_generic(args)
    if args.platform == "claude-code":
        return _cmd_install_claude_code(args)
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
            base_profile=None if args.no_base_profile else args.base_profile,
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

    if args.platform == "generic":
        return _cmd_uninstall_generic(args)
    if args.platform == "claude-code":
        return _cmd_uninstall_claude_code(args)
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

    plugin_root = _plugin_root_from(args.plugin_root)
    if plugin_root is None:
        print("pact: plugin root unresolved; pass --plugin-root or set PACT_PLUGIN_ROOT", file=sys.stderr)
        return EXIT_PROBLEM
    extra_frames = ()
    recorded = getattr(args, "upstream_corpus", None)
    if recorded:
        from .conformance.upstream_corpus import UpstreamCorpusError, load_recorded_frames

        try:
            extra_frames = load_recorded_frames(Path(recorded))
        except UpstreamCorpusError as exc:
            print(f"pact: {exc}", file=sys.stderr)
            return EXIT_PROBLEM
    try:
        report = run_conformance(
            plugin_root,
            adapter_id=args.adapter,
            python=args.python,
            state_root=Path(args.state_root) if args.state_root else None,
            extra_frames=extra_frames,
        )
    except PactHarnessError as exc:
        print(f"pact: {exc}", file=sys.stderr)
        return EXIT_PROBLEM
    parity = None
    if getattr(args, "parity", False):
        from .conformance.parity import run_parity

        try:
            parity = run_parity(
                plugin_root,
                adapter_id=args.adapter,
                python=args.python,
                extra_frames=extra_frames,
            )
        except PactHarnessError as exc:
            print(f"pact: {exc}", file=sys.stderr)
            return EXIT_PROBLEM
    if args.json:
        payload = report.to_dict()
        if parity is not None:
            payload["parity"] = parity.to_dict()
        print(json.dumps(payload, indent=2, sort_keys=True))
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
        if parity is not None:
            print(
                f"byte parity  : {len(parity.cells)} cells across {parity.frames} frames, "
                f"{parity.mismatched} mismatch(es)"
            )
            for failure in parity.failures:
                print(f"  diverged: {failure}")
    if not report.ok:
        return EXIT_PROBLEM
    if parity is not None and not parity.ok:
        return EXIT_PROBLEM
    return EXIT_OK


def _cmd_playbook_build(args: argparse.Namespace) -> int:
    """Compile PACT's artifacts for one platform."""
    from .playbook.compile import PlaybookError, compile_playbook
    from .vendor import VendorError, load_pin

    plugin_root = _plugin_root_from(args.plugin_root)
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


def _plugin_root_from(value: str | None) -> Path | None:
    """Resolve an explicit --plugin-root, accepting a repository or a plugin dir.

    A caller who clones upstream points at the repository, whose plugin lives in
    a directory of the same name. Accepting both means the flag matches what the
    caller sees in their editor.
    """
    if value:
        return plugin_dir(Path(value))
    return resolve_plugin_root()


def _load_surface_document(reference: str) -> dict[str, Any]:
    """Load a manifest from a JSON file or extract one from a tree."""
    from .upstream.inspect import inspect_surface
    from .vendor import VendorError, load_pin

    path = Path(reference)
    if path.is_file():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{reference}: manifest is not an object")
        return raw
    plugin_root = plugin_dir(path)
    version = commit = tag = ""
    harness = find_harness_root()
    if harness is not None:
        try:
            pin = load_pin(harness)
            version, commit, tag = pin.pact_version, pin.commit, pin.tag or ""
        except VendorError:
            pass
    return inspect_surface(plugin_root, pact_version=version, commit=commit, tag=tag).to_dict()


def _cmd_upstream_check(args: argparse.Namespace) -> int:
    """Report how far the pin is from the latest upstream release."""
    from .upstream.check import check_release
    from .vendor import VendorError, load_pin

    harness = find_harness_root()
    if harness is None:
        print("pact: harness root not found", file=sys.stderr)
        return EXIT_PROBLEM
    try:
        pin = load_pin(harness)
    except VendorError as exc:
        print(f"pact: {exc}", file=sys.stderr)
        return EXIT_PROBLEM
    report = check_release(pin, repository=args.repository)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    elif not report.checked:
        print(f"upstream check failed: {report.error}", file=sys.stderr)
    else:
        print(f"pinned       : {report.pinned_version} ({report.pinned_commit[:12]})")
        print(f"latest       : {report.latest_version} ({report.latest_tag})")
        print(f"behind       : {report.behind}")
        for tag in report.newer_tags[:10]:
            print(f"  newer: {tag}")
    return EXIT_OK if report.checked or args.json else EXIT_PROBLEM


def _cmd_upstream_diff(args: argparse.Namespace) -> int:
    """Classify the difference between two surface documents."""
    from .upstream.diff import Level, diff_surfaces

    try:
        old = _load_surface_document(args.old)
        new = _load_surface_document(args.new)
    except (OSError, ValueError, PactHarnessError) as exc:
        print(f"pact: {exc}", file=sys.stderr)
        return EXIT_PROBLEM
    report = diff_surfaces(old, new)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"level        : {report.level.value} ({'automatic' if report.automatic else 'needs work'})")
        print(f"differences  : {len(report.differences)}")
        for difference in report.differences:
            detail = f" — {difference.detail}" if difference.detail else ""
            print(f"  {difference.level.value} {difference.kind}: {difference.identity}{detail}")
        facts = report.facts
        print(
            "registrations: "
            f"{facts['registrations']['old']} -> {facts['registrations']['new']}"
        )
    return EXIT_PROBLEM if report.level is Level.L4 else EXIT_OK


def _cmd_upstream(args: argparse.Namespace) -> int:
    """Inspect or verify the upstream surface."""
    from .upstream.inspect import InspectError, inspect_surface
    from .upstream.verify import verify_surface
    from .vendor import VendorError, load_pin

    plugin_root = _plugin_root_from(args.plugin_root)
    if plugin_root is None:
        print("pact: plugin root unresolved; pass --plugin-root or set PACT_PLUGIN_ROOT", file=sys.stderr)
        return EXIT_PROBLEM
    pact_version = ""
    commit = ""
    tag = ""
    harness = find_harness_root()
    if harness is not None:
        try:
            pin = load_pin(harness)
            pact_version, commit, tag = pin.pact_version, pin.commit, pin.tag or ""
        except VendorError:
            pass
    try:
        surface = inspect_surface(
            plugin_root, pact_version=pact_version, commit=commit, tag=tag
        )
    except InspectError as exc:
        print(f"pact: {exc}", file=sys.stderr)
        return EXIT_PROBLEM

    if args.action == "inspect":
        document = surface.to_dict()
        if getattr(args, "portable", False):
            # A committed manifest should not carry the machine it was built on.
            document["plugin_root"] = "<plugin>"
        if args.out:
            destination = Path(args.out)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(f"surface      : {destination}")
            registry = document["registry"]
            print(
                "contents     : "
                f"{registry['registrations']} registrations, {registry['events']} events, "
                f"{registry['modules']} modules, {len(document['env_reads'])} env reads, "
                f"{len(document['artifacts'])} artifacts"
            )
        else:
            print(json.dumps(document, indent=2, sort_keys=True))
        return EXIT_OK

    report = verify_surface(surface, allow_unknown_env=getattr(args, "allow_unknown_env", False))
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        facts = report.facts
        print(f"registrations: {facts.get('registrations')}")
        print(f"env reads    : {facts.get('env_reads')}")
        print(f"artifacts    : {facts.get('artifacts')}")
        print(f"errors       : {len(report.errors)}")
        for item in report.errors:
            print(f"  error: {item}")
        for item in report.warnings:
            print(f"  warning: {item}")
    return EXIT_OK if report.ok else EXIT_PROBLEM


def _cmd_hook(args: argparse.Namespace) -> int:
    """Run one hook against one frame and print what it did."""
    from .adapters.base import AdapterContext
    from .adapters.registry import get_adapter
    from .contract import EventV1
    from .runtime.abi import run_hook
    from .runtime.registry import HookRegistry
    from .runtime.vocabulary import inject_target_for

    raw = sys.stdin.read()
    try:
        frame = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"pact: stdin is not JSON: {exc}", file=sys.stderr)
        return EXIT_PROBLEM
    if not isinstance(frame, dict):
        print("pact: stdin must be a JSON object", file=sys.stderr)
        return EXIT_PROBLEM
    adapter = get_adapter(args.adapter)
    if "kind" in frame and "adapter" in frame:
        try:
            event = EventV1.from_dict(frame)
        except PactHarnessError as exc:
            print(f"pact: not a pact.event.v1 document: {exc}", file=sys.stderr)
            return EXIT_PROBLEM
    else:
        try:
            event = adapter.normalize(frame)
        except Exception as exc:  # noqa: BLE001 -- a bad frame is a usage error
            print(f"pact: frame is neither pact.event.v1 nor a native event: {exc}", file=sys.stderr)
            return EXIT_PROBLEM

    plugin_root = _plugin_root_from(args.plugin_root)
    if plugin_root is None:
        print("pact: plugin root unresolved; pass --plugin-root or set PACT_PLUGIN_ROOT", file=sys.stderr)
        return EXIT_PROBLEM
    from .engine import manifest_event_for, tool_name_for

    manifest_event = manifest_event_for(event.kind)
    registry = HookRegistry.from_plugin_root(plugin_root)
    wanted = args.hook_id.removesuffix(".py")
    tool_name = tool_name_for(event)
    candidates = [
        registration
        for registration in registry.for_event(manifest_event, tool_name=tool_name)
        if registration.hook == wanted or registration.script.stem == wanted
    ]
    if not candidates:
        # Degenerate frames (upstream's own adversarial tests send these) can
        # yield a tool name that matches no matcher group, or none at all. The
        # host only runs a matcher-gated hook when its matcher already matched,
        # so running the named registration is the faithful thing to do;
        # refusing here would diverge from running the hook directly.
        candidates = [
            registration
            for registration in registry.for_event(manifest_event)
            if registration.hook == wanted or registration.script.stem == wanted
        ]
    if not candidates:
        modules = ", ".join(registry.modules())
        print(
            f"pact: no registration named {args.hook_id!r} on {manifest_event}; modules: {modules}",
            file=sys.stderr,
        )
        return EXIT_PROBLEM
    registration = candidates[0]
    state_root = (
        Path(args.state_root)
        if args.state_root
        else resolve_state_root(adapter=args.adapter).path
    )
    # The shim must be transparent: a directive tells Claude Code to run
    # "python3 <script>", and the shim was started by that python3, so its child
    # runs on the same interpreter. Resolving independently could pick another
    # Python, which shows up as a byte difference the moment a hook reports it.
    interpreter = args.python or sys.executable
    context = AdapterContext(
        plugin_root=plugin_root,
        state_root=state_root,
        python=interpreter,
        base_env=dict(os.environ),
    )
    legacy = adapter.legacy_stdin(event)
    env = adapter.hook_env(event, context)
    run = run_hook(
        registration,
        legacy.data,
        env=env.values,
        python=interpreter,
        cwd=event.session.cwd,
        inject_target=inject_target_for(event.kind),
    )
    if args.passthrough:
        # The bridged mode is a shim, so it must not be a translator: the hook's
        # bytes and exit code pass through untouched.
        sys.stdout.buffer.write(run.result.stdout)
        sys.stdout.buffer.flush()
        sys.stderr.buffer.write(run.result.stderr)
        sys.stderr.buffer.flush()
        return int(run.result.exit) if run.result.exit is not None else EXIT_PROBLEM
    if args.raw:
        payload: dict[str, Any] = {
            "hook": registration.hook,
            "event": registration.event,
            "exit": run.result.exit,
            "duration_ms": run.result.duration_ms,
            "stdout": run.result.stdout_text,
            "stderr": run.result.stderr_text,
        }
    else:
        payload = run.to_dict()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return EXIT_OK if run.decoded.problem is None else EXIT_PROBLEM


def _cmd_migrate_state(args: argparse.Namespace) -> int:
    """Move one state root to another, refusing partial moves."""
    import shutil

    adapter = args.adapter or detect_adapter()
    source = (
        Path(args.from_dir).expanduser()
        if args.from_dir
        else resolve_state_root(adapter=adapter).path
    )
    target = Path(args.to).expanduser()
    if not source.is_dir():
        print(f"pact: nothing to migrate: {source} does not exist")
        return EXIT_OK
    source_resolved = source.resolve()
    target_resolved = target.resolve()
    if (
        target_resolved == source_resolved
        or source_resolved in target_resolved.parents
        or target_resolved in source_resolved.parents
    ):
        print("pact: source and target must not contain one another", file=sys.stderr)
        return EXIT_PROBLEM
    entries = sorted(source.iterdir())
    conflicts = [entry.name for entry in entries if (target / entry.name).exists()]
    if conflicts:
        print(
            "pact: refusing a partial move; target already has: " + ", ".join(conflicts),
            file=sys.stderr,
        )
        return EXIT_PROBLEM
    if args.dry_run:
        print(f"would move {len(entries)} entries: {source} -> {target}")
        for entry in entries:
            print(f"  {entry.name}")
        return EXIT_OK
    target.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        shutil.move(str(entry), str(target / entry.name))
    if not any(source.iterdir()):
        source.rmdir()
    print(f"moved {len(entries)} entries: {source} -> {target}")
    return EXIT_OK


def _cmd_install_claude_code(args: argparse.Namespace) -> int:
    """Report or refuse a Claude Code install."""
    from .runtime.registry import HookRegistry

    mode = args.mode if args.mode in ("native", "bridged") else "native"
    plugin_root = _plugin_root_from(getattr(args, "plugin_root", None))
    if plugin_root is None:
        print("pact: plugin root unresolved; pass --plugin-root or set PACT_PLUGIN_ROOT", file=sys.stderr)
        return EXIT_PROBLEM
    if mode == "bridged":
        from .install.claude_code import ClaudeCodeInstallError, install_bridged

        if not getattr(args, "target", None) or args.target == ".":
            print(
                "pact: bridged mode owns a directory; pass --target DIR",
                file=sys.stderr,
            )
            return EXIT_PROBLEM
        try:
            result = install_bridged(Path(args.target), plugin_root)
        except ClaudeCodeInstallError as exc:
            print(f"pact: {exc}", file=sys.stderr)
            return EXIT_PROBLEM
        if args.json:
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        else:
            print(f"bridged      : {result.target}")
            print(f"commands     : {result.commands} rewritten to call the shim")
            print(f"parity       : {result.parity_cells} cells byte-identical")
            for note in result.notes:
                print(f"note         : {note}")
        return EXIT_OK
    registry = HookRegistry.from_plugin_root(plugin_root)
    print("claude-code native: nothing to install")
    print(f"plugin root  : {plugin_root}")
    print(f"hook registry: {len(registry)} registrations on {len(registry.events)} events")
    print("note         : Claude Code keeps its own hooks; the harness reads the same tree")
    return EXIT_OK


def _cmd_uninstall_claude_code(args: argparse.Namespace) -> int:
    """Remove a bridged install, or report that a native one left nothing."""
    from .install.claude_code import ClaudeCodeInstallError, uninstall_bridged

    target = getattr(args, "target", None)
    if target and target != ".":
        try:
            manifest = uninstall_bridged(Path(target))
        except ClaudeCodeInstallError as exc:
            print(f"pact: {exc}", file=sys.stderr)
            return EXIT_PROBLEM
        if getattr(args, "json", False):
            print(json.dumps({"removed": manifest}, indent=2, sort_keys=True))
        else:
            print(f"removed      : {target}")
            print(f"had commands : {manifest.get('commands')}")
        return EXIT_OK
    print("claude-code native: nothing was installed, so there is nothing to remove")
    return EXIT_OK


def _cmd_corpus(args: argparse.Namespace) -> int:
    """Record or replay the corpus verdict matrix."""
    from .conformance.harvest import (
        HarvestError,
        harvest_corpus,
        load_harvest,
        replay_harvest,
        save_harvest,
    )
    from .vendor import VendorError, load_pin

    if args.action == "harvest-upstream":
        from .conformance.upstream_corpus import UpstreamCorpusError, harvest, load_recorded_frames

        try:
            if args.from_recorded:
                frames = load_recorded_frames(Path(args.from_recorded), limit=args.limit)
                document = {
                    "v": 1,
                    "source": "recorded upstream frames",
                    "frames": [
                        {"name": frame.name, "event": frame.event, "frame": frame.frame}
                        for frame in frames
                    ],
                }
                destination = Path(args.out)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
                print(f"corpus       : {destination}")
                print(f"frames       : {len(frames)}")
                return EXIT_OK
            if not args.repo:
                print("pact: harvest-upstream needs --repo or --from-recorded", file=sys.stderr)
                return EXIT_PROBLEM
            harness = find_harness_root()
            recorder = (
                Path(args.recorder)
                if args.recorder
                else (harness or Path.cwd()) / "tools" / "upstream_recorder"
            )
            code, count = harvest(
                Path(args.repo),
                out=Path(args.out),
                recorder=recorder,
                select=args.select,
                limit=args.limit,
            )
            print(f"corpus       : {args.out}")
            print(f"frames       : {count}")
            print(f"pytest exit  : {code}")
            return EXIT_OK if code == 0 else EXIT_PROBLEM
        except UpstreamCorpusError as exc:
            print(f"pact: {exc}", file=sys.stderr)
            return EXIT_PROBLEM

    plugin_root = _plugin_root_from(getattr(args, "plugin_root", None))
    if plugin_root is None:
        print("pact: plugin root unresolved; pass --plugin-root or set PACT_PLUGIN_ROOT", file=sys.stderr)
        return EXIT_PROBLEM
    version = commit = ""
    harness = find_harness_root()
    if harness is not None:
        try:
            pin = load_pin(harness)
            version, commit = pin.pact_version, pin.commit
        except VendorError:
            pass
    try:
        if args.action == "harvest":
            record = harvest_corpus(
                plugin_root, pact_version=version, pact_commit=commit
            )
            destination = save_harvest(Path(args.out), record)
            if args.json:
                print(json.dumps(record.to_dict(), indent=2, sort_keys=True))
            else:
                print(f"baseline     : {destination}")
                print(f"pact         : {version or 'unknown'}")
                print(f"cells        : {len(record.cells)}")
                print(f"hooks        : {len({cell.hook for cell in record.cells})}")
                print(f"frames       : {len({cell.frame for cell in record.cells})}")
            return EXIT_OK
        recorded = load_harvest(Path(args.file))
        report = replay_harvest(plugin_root, recorded, current_version=version)
    except HarvestError as exc:
        print(f"pact: {exc}", file=sys.stderr)
        return EXIT_PROBLEM
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"recorded     : {report.recorded_version or 'unknown'} ({report.cells} cells)")
        print(f"current      : {report.current_version or 'unknown'}")
        print(f"changes      : {len(report.changes)}")
        for change in report.changes[:20]:
            print(f"  changed: {change.key} {change.field}: {change.before!r} -> {change.after!r}")
        for key in report.added:
            print(f"  added  : {key}")
        for key in report.removed:
            print(f"  removed: {key}")
    return EXIT_OK if report.ok else EXIT_PROBLEM


def _cmd_dev(args: argparse.Namespace) -> int:
    """Link, unlink or report a development checkout."""
    from .dev import DevLinkError, link, status, unlink
    from .paths import dev_link_file

    if args.action == "link":
        if not args.plugin:
            print("pact: dev link needs --plugin DIR", file=sys.stderr)
            return EXIT_PROBLEM
        try:
            result = link(Path(args.plugin).expanduser())
        except DevLinkError as exc:
            print(f"pact: {exc}", file=sys.stderr)
            return EXIT_PROBLEM
        payload = result.to_dict()
        payload["link_file"] = str(dev_link_file())
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"linked       : {result.plugin_root}")
            print(f"version      : {result.version or 'unknown'}")
            print(f"dirty        : {'yes' if result.dirty else 'no' if result.dirty is False else 'not a git checkout'}")
            print("note         : PACT_PLUGIN_ROOT still wins for a single command")
        return EXIT_OK
    if args.action == "unlink":
        try:
            removed = unlink()
        except DevLinkError as exc:
            print(f"pact: {exc}", file=sys.stderr)
            return EXIT_PROBLEM
        if args.json:
            print(json.dumps({"removed": removed.to_dict() if removed else None}, indent=2, sort_keys=True))
        else:
            print(f"unlinked     : {removed.plugin_root}" if removed else "nothing was linked")
        return EXIT_OK
    payload = status()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return EXIT_OK
    print(f"link file    : {payload['link_file']}")
    print(f"recorded     : {'yes' if payload['recorded'] else 'no'}")
    print(f"plugin root  : {payload['plugin_root'] or 'none'}")
    print(f"present      : {'yes' if payload['present'] else 'no'}")
    if payload["present"]:
        print(f"version      : {payload['version'] or 'unknown'}")
        dirty = payload["dirty"]
        print(f"dirty        : {'yes' if dirty else 'no' if dirty is False else 'unknown'}")
    return EXIT_OK


def _cmd_memory_status(args: argparse.Namespace) -> int:
    """Report the memory backend's location and presence."""
    from .memory import CliMemoryBackend, MemoryBackendError

    plugin_root = _plugin_root_from(args.plugin_root)
    if plugin_root is None:
        print("pact: plugin root unresolved; pass --plugin-root or set PACT_PLUGIN_ROOT", file=sys.stderr)
        return EXIT_PROBLEM
    backend = CliMemoryBackend(plugin_root=plugin_root, python=args.python or "")
    try:
        status = backend.status()
    except MemoryBackendError as exc:
        print(f"pact: {exc}", file=sys.stderr)
        return EXIT_PROBLEM
    if args.json:
        print(json.dumps(status.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"backend      : {status.backend}")
        print(f"location     : {status.location}")
        print(f"present      : {'yes' if status.exists else 'no'}")
        if status.size_bytes is not None:
            print(f"size         : {status.size_bytes} bytes")
        print(f"detail       : {status.detail}")
    return EXIT_OK


def _cmd_mcp_serve(args: argparse.Namespace) -> int:
    """Serve PACT's advisory tools over MCP stdio."""
    from .mcp.server import McpServer
    from .mcp.tools import ToolOptions

    plugin_root = _plugin_root_from(args.plugin_root)
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

    plugin_root = _plugin_root_from(args.plugin_root)
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
    install.add_argument("platform", choices=["dsh", "generic", "claude-code"], help="platform to install into")
    install.add_argument("--target", default=".", help="project directory for generic installs")
    install.add_argument("--skills-root", metavar="DIR", help="shared skills root for generic installs")
    install.add_argument(
        "--no-skills",
        action="store_true",
        help="write the section without linking skills (generic)",
    )
    install.add_argument("--plugin-root", metavar="DIR", help="upstream tree to link (generic)")
    install.add_argument("--profile", default="pact", help="DSH profile to create or update")
    install.add_argument("--dsh-home", metavar="DIR", help="DSH configuration root")
    install.add_argument("--dsh-cli", metavar="PATH", help="dsh executable to use")
    install.add_argument(
        "--mode",
        default="shadow",
        help="dsh: off, shadow or enforce; claude-code: native (default) or bridged",
    )
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
    install.add_argument(
        "--base-profile",
        default="headless",
        metavar="NAME",
        help="template used to create a bootable profile (default: headless)",
    )
    install.add_argument(
        "--no-base-profile",
        action="store_true",
        help="do not materialise a template; the caller owns the profile",
    )
    install.add_argument(
        "--venv",
        action="store_true",
        help="also create the memory CLI's virtual environment",
    )
    install.add_argument("--json", action="store_true", help="print JSON")
    install.set_defaults(func=_cmd_install)

    uninstall = subparsers.add_parser("uninstall", help="remove the bridge from a host platform")
    uninstall.add_argument("platform", choices=["dsh", "generic", "claude-code"], help="platform to uninstall from")
    uninstall.add_argument("--target", default=".", help="project directory for generic uninstalls")
    uninstall.add_argument("--skills-root", metavar="DIR", help="shared skills root for generic uninstalls")
    uninstall.add_argument("--plugin-root", metavar="DIR", help="upstream tree whose links are removed")
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
    conformance.add_argument(
        "--upstream-corpus",
        metavar="FILE",
        help="add frames recorded from PACT's own tests to the corpus and parity run",
    )
    conformance.add_argument("--json", action="store_true", help="print JSON")
    conformance.add_argument(
        "--parity",
        action="store_true",
        help="also prove the bridged shim is byte-identical to a direct hook run",
    )
    conformance.set_defaults(func=_cmd_conformance)

    hook = subparsers.add_parser("hook", help="run one hook against one frame")
    hook.add_argument("hook_id", metavar="HOOK")
    hook.add_argument("--adapter", default="claude-code", metavar="ID")
    hook.add_argument("--plugin-root", metavar="DIR")
    hook.add_argument("--state-root", metavar="DIR")
    hook.add_argument("--python", metavar="PATH")
    hook.add_argument("--raw", action="store_true", help="print the raw process result")
    hook.add_argument(
        "--passthrough",
        action="store_true",
        help="write the hook's own stdout/stderr and exit with its code (bridged mode)",
    )
    hook.set_defaults(func=_cmd_hook)

    migrate = subparsers.add_parser("migrate-state", help="move a state root")
    migrate.add_argument("--to", metavar="DIR", required=True)
    migrate.add_argument("--from", dest="from_dir", metavar="DIR")
    migrate.add_argument("--adapter", default=None, metavar="ID")
    migrate.add_argument("--dry-run", action="store_true")
    migrate.set_defaults(func=_cmd_migrate_state)

    corpus = subparsers.add_parser("corpus", help="record or replay the corpus verdict matrix")
    corpus_actions = corpus.add_subparsers(dest="action", required=True)
    corpus_harvest = corpus_actions.add_parser("harvest", help="record the matrix for this tree")
    corpus_harvest.add_argument("--out", metavar="PATH", required=True)
    corpus_harvest.add_argument("--plugin-root", metavar="DIR")
    corpus_harvest.add_argument("--json", action="store_true")
    corpus_harvest.set_defaults(func=_cmd_corpus)
    corpus_replay = corpus_actions.add_parser("replay", help="compare this tree with a matrix")
    corpus_replay.add_argument("file", metavar="FILE")
    corpus_replay.add_argument("--plugin-root", metavar="DIR")
    corpus_replay.add_argument("--json", action="store_true")
    corpus_replay.set_defaults(func=_cmd_corpus)
    corpus_upstream = corpus_actions.add_parser(
        "harvest-upstream",
        help="record PACT's own test frames as parity cells",
    )
    corpus_upstream.add_argument("--repo", metavar="DIR", help="upstream checkout root")
    corpus_upstream.add_argument("--out", metavar="PATH", required=True)
    corpus_upstream.add_argument(
        "--from-recorded",
        metavar="FILE",
        help="convert an existing recorder JSONL instead of running the suite",
    )
    corpus_upstream.add_argument(
        "--recorder",
        metavar="DIR",
        help="recorder directory (default: tools/upstream_recorder in the harness)",
    )
    corpus_upstream.add_argument("--select", metavar="EXPR", help="pytest -k expression")
    corpus_upstream.add_argument("--limit", type=int, default=2000)
    corpus_upstream.set_defaults(func=_cmd_corpus)

    dev = subparsers.add_parser("dev", help="run against a working checkout")
    dev_actions = dev.add_subparsers(dest="action", required=True)
    dev_link = dev_actions.add_parser("link", help="record a checkout as the tree to use")
    dev_link.add_argument("--plugin", metavar="DIR", required=True)
    dev_link.add_argument("--json", action="store_true")
    dev_link.set_defaults(func=_cmd_dev)
    dev_unlink = dev_actions.add_parser("unlink", help="remove the recorded checkout")
    dev_unlink.add_argument("--json", action="store_true")
    dev_unlink.set_defaults(func=_cmd_dev)
    dev_status = dev_actions.add_parser("status", help="report the recorded checkout")
    dev_status.add_argument("--json", action="store_true")
    dev_status.set_defaults(func=_cmd_dev)

    upstream = subparsers.add_parser("upstream", help="inspect or verify the upstream surface")
    upstream_actions = upstream.add_subparsers(dest="action", required=True)
    upstream_inspect = upstream_actions.add_parser("inspect", help="extract the surface manifest")
    upstream_inspect.add_argument("--plugin-root", metavar="DIR")
    upstream_inspect.add_argument("--out", metavar="PATH", help="write the manifest here")
    upstream_inspect.add_argument("--json", action="store_true", help="print JSON when no --out")
    upstream_inspect.add_argument(
        "--portable",
        action="store_true",
        help="write the plugin root as a placeholder instead of an absolute path",
    )
    upstream_inspect.set_defaults(func=_cmd_upstream)
    upstream_verify = upstream_actions.add_parser("verify", help="check the tree against the harness")
    upstream_verify.add_argument("--plugin-root", metavar="DIR")
    upstream_verify.add_argument("--json", action="store_true", help="print JSON")
    upstream_verify.add_argument(
        "--allow-unknown-env",
        action="store_true",
        help="report an unclassified environment read as a warning",
    )
    upstream_verify.set_defaults(func=_cmd_upstream)
    upstream_diff = upstream_actions.add_parser(
        "diff", help="classify the difference between two manifests"
    )
    upstream_diff.add_argument("old", metavar="OLD", help="manifest file or tree")
    upstream_diff.add_argument("new", metavar="NEW", help="manifest file or tree")
    upstream_diff.add_argument("--json", action="store_true", help="print JSON")
    upstream_diff.set_defaults(func=_cmd_upstream_diff)
    upstream_check = upstream_actions.add_parser(
        "check", help="report how far the pin is from the latest release"
    )
    upstream_check.add_argument("--repository", metavar="URL", help="override the upstream URL")
    upstream_check.add_argument("--json", action="store_true", help="print JSON")
    upstream_check.set_defaults(func=_cmd_upstream_check)

    memory = subparsers.add_parser("memory", help="inspect the memory backend")
    memory_actions = memory.add_subparsers(dest="action", required=True)
    memory_status = memory_actions.add_parser("status", help="report the backend's store")
    memory_status.add_argument("--plugin-root", metavar="DIR")
    memory_status.add_argument("--python", metavar="PATH")
    memory_status.add_argument("--json", action="store_true", help="print JSON")
    memory_status.set_defaults(func=_cmd_memory_status)

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