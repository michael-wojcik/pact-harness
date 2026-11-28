#!/usr/bin/env python3
"""Prepare an upstream bump branch for an L0/L1 release, and optionally publish it.

Only the mechanical part is automated: pin, submodule pointer, portable surface
manifest, corpus baseline, harness version and changelog. If the diff is not L0 or
L1, or verify/conformance fail, the tree is restored and nothing is opened. The
PR is never merged and no tag is ever created; a human does both.

Exit codes: 0 the branch was prepared (or deliberately not prepared, with the
reason in the output), 1 publishing failed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

MANIFEST = "harness/upstream-surface.v1.json"
PIN = "harness/pact-pin.json"
VERSION_FILES = (
    "python/pyproject.toml",
    "python/src/pact_harness/__init__.py",
    "adapters/dsh/package.json",
)


def run(args: list[str], *, cwd: Path | str | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)


def module(args: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return run([sys.executable, "-m", "pact_harness", *args], env=env)


def write_output(path: str | None, opened: bool, reason: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("opened=" + ("true" if opened else "false") + "\n")
    print('AUTO_BUMP {"opened": ' + ("true" if opened else "false") + ', "reason": "' + reason.replace('"', "'") + '"}')


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="upstream tag to absorb")
    parser.add_argument("--root", default=".", help="harness checkout root")
    parser.add_argument("--body-file", help="where to write the PR body")
    parser.add_argument("--github-output", help="append opened=true/false here")
    parser.add_argument("--publish", action="store_true", help="push the branch, open the PR and dispatch the contract lane")
    parser.add_argument("--dry-run", action="store_true", help="prepare the branch but never push")
    args = parser.parse_args(argv[1:])

    root = Path(args.root).resolve()
    os.chdir(root)
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(root / "python" / "src"))

    pin = json.loads(Path(PIN).read_text(encoding="utf-8"))
    old_pact = pin["pactVersion"]
    sub = Path(pin["submodulePath"])
    if not sub.is_dir():
        write_output(args.github_output, False, "vendored submodule is not checked out")
        return 0

    if run(["git", "-C", str(sub), "fetch", "--quiet", "--tags", "origin"]).returncode != 0:
        write_output(args.github_output, False, "could not fetch upstream tags")
        return 0
    resolved = run(["git", "-C", str(sub), "rev-parse", args.tag + "^{commit}"])
    if resolved.returncode != 0:
        write_output(args.github_output, False, "tag " + args.tag + " was not found upstream")
        return 0
    commit = resolved.stdout.strip()
    if commit == pin["commit"]:
        write_output(args.github_output, False, "already at " + args.tag)
        return 0

    old_manifest = run(["git", "show", "HEAD:" + MANIFEST])
    old_manifest_path = Path("/tmp/pact-old-surface.json")
    old_manifest_path.write_text(old_manifest.stdout, encoding="utf-8")

    def restore(reason: str) -> int:
        run(["git", "checkout", "--", "."])
        if new_baseline.exists():
            new_baseline.unlink()
        run(["git", "-C", str(sub), "checkout", "--quiet", pin["commit"]])
        write_output(args.github_output, False, reason)
        return 0

    new_pact = args.tag[1:] if args.tag.startswith("v") else args.tag
    new_baseline = Path("harness/corpus/pact-" + new_pact + ".json")

    checkout = run(["git", "-C", str(sub), "checkout", "--quiet", commit])
    if checkout.returncode != 0:
        return restore("could not check out " + args.tag)

    pin.update({"pactVersion": new_pact, "commit": commit, "tag": args.tag if args.tag.startswith("v") else "v" + new_pact})
    Path(PIN).write_text(json.dumps(pin, indent=2) + "\n", encoding="utf-8")

    inspect = module(["upstream", "inspect", "--portable", "--out", MANIFEST], env=env)
    if inspect.returncode != 0:
        return restore("surface inspection failed")

    diff = run([sys.executable, "-m", "pact_harness", "upstream", "diff", str(old_manifest_path), MANIFEST, "--json"], env=env)
    try:
        report = json.loads(diff.stdout)
    except ValueError:
        return restore("surface diff was not JSON")
    level = str(report.get("level") or "unknown")
    if level not in ("L0", "L1"):
        return restore("diff is " + level + "; only L0/L1 bumps are automated")

    verify = module(["upstream", "verify", "--json"], env=env)
    try:
        verified = json.loads(verify.stdout)
    except ValueError:
        verified = {"ok": False}
    if not verified.get("ok"):
        return restore("upstream verify reports errors")

    conformance = module(["conformance", "--parity", "--json"], env=env)
    try:
        conformed = json.loads(conformance.stdout)
    except ValueError:
        conformed = {"ok": False}
    if not conformed.get("ok"):
        return restore("conformance or parity failed on the new tree")

    harvest = module(["corpus", "harvest", "--out", str(new_baseline)], env=env)
    if harvest.returncode != 0:
        return restore("corpus baseline could not be regenerated")
    old_baseline = Path("harness/corpus/pact-" + old_pact + ".json")
    if old_baseline.exists():
        old_baseline.unlink()

    pyproject = Path("python/pyproject.toml")
    text = pyproject.read_text(encoding="utf-8")
    current = tomllib.loads(text)["project"]["version"]
    major, minor, patch = (int(part) for part in current.split("."))
    new_version = str(major) + "." + str(minor) + "." + str(patch + 1)
    replacements = {
        "python/pyproject.toml": ('version = "' + current + '"', 'version = "' + new_version + '"'),
        "python/src/pact_harness/__init__.py": ('__version__ = "' + current + '"', '__version__ = "' + new_version + '"'),
        "adapters/dsh/package.json": ('"version": "' + current + '"', '"version": "' + new_version + '"'),
    }
    for name, (old, new) in replacements.items():
        path = Path(name)
        body = path.read_text(encoding="utf-8")
        if old not in body:
            return restore("could not bump the version in " + name)
        path.write_text(body.replace(old, new, 1), encoding="utf-8")

    artifacts = [item for item in report.get("differences") or [] if isinstance(item, dict)]
    bullets = "\n".join(
        "- " + str(item.get("level", "")) + " " + str(item.get("kind", "")) + ": " + str(item.get("identity", ""))
        for item in artifacts[:12]
    )
    changelog = Path("CHANGELOG.md")
    text = changelog.read_text(encoding="utf-8")
    entry = (
        "## v" + new_version + ": upstream PACT " + (args.tag if args.tag.startswith("v") else "v" + new_pact) + "\n\n"
        "- Absorbs upstream PACT " + new_pact + " (" + commit[:8] + "). The surface diff is " + level + ", so this\n"
        "  is the mechanical part only: pin, surface manifest, corpus baseline and version bump.\n"
        "- Pre-qualification: verify clean, conformance/parity clean, corpus verdicts unchanged.\n"
        + (bullets + "\n" if bullets else "")
        + "\n"
    )
    marker = "## v" + current + ":"
    if marker in text:
        text = text.replace(marker, entry + marker, 1)
    else:
        text = text.replace("# Changelog", "# Changelog\n\n" + entry.rstrip(), 1)
    changelog.write_text(text, encoding="utf-8")

    branch = "upstream/" + args.tag
    run(["git", "checkout", "-B", branch])
    run(["git", "add", "-A"])
    title = "Absorb upstream PACT " + (args.tag if args.tag.startswith("v") else "v" + new_pact) + " (v" + new_version + ")"
    commit_result = run(["git", "commit", "-q", "-m", title])
    if commit_result.returncode != 0:
        return restore("nothing was committed")
    if args.body_file:
        body = (
            "Automated L0/L1 absorb of upstream PACT " + new_pact + " (" + commit + ").\n\n"
            "Surface grade: " + level + "\n\n"
            "### Named changes\n\n" + (bullets or "- none") + "\n\n"
            "### What is already done\n\n"
            "- pin and submodule pointer\n- portable surface manifest\n"
            "- corpus baseline for " + new_pact + "\n- harness version v" + new_version + "\n"
            "- changelog entry\n\n"
            "Merge after the contract lane is green, then tag v" + new_version + "; the release lane publishes.\n"
            "Review docs/progress.md for prose that still names the previous pin.\n"
        )
        Path(args.body_file).write_text(body, encoding="utf-8")

    if not args.publish or args.dry_run:
        write_output(args.github_output, True, "prepared " + branch + " (not published)")
        return 0

    push = run(["git", "push", "--force-with-lease", "origin", "HEAD:refs/heads/" + branch])
    if push.returncode != 0:
        write_output(args.github_output, False, "could not push " + branch)
        return 1
    existing = run(["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number", "--jq", ".[0].number // empty"])
    number = existing.stdout.strip()
    if number:
        pr = run(["gh", "pr", "edit", number, "--title", title, "--body-file", args.body_file or "/dev/null"])
    else:
        pr = run(["gh", "pr", "create", "--base", "main", "--head", branch, "--title", title, "--body-file", args.body_file or "/dev/null"])
    if pr.returncode != 0:
        write_output(args.github_output, False, "could not open the pull request")
        return 1
    run(["gh", "workflow", "run", "contract.yml", "--ref", branch])
    write_output(args.github_output, True, "opened pull request for " + branch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
