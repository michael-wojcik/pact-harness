#!/usr/bin/env python3
"""Pre-qualify the next upstream PACT release, and fail when one is waiting.

Reads the JSON from 'pact upstream check --json', and when the pin is behind the
latest release it does the cheap part of an absorb against that tag in a
throwaway checkout:

    upstream inspect -> upstream diff -> upstream verify
    -> conformance --parity -> corpus replay against the pinned baseline

Then it writes a job summary that says what the bump will cost (L-level and the
named changes) and exits non-zero, so the scheduled lane is red while a release
is waiting instead of silently green.

Deliberately not here: upstream's own test suite (the contract lane runs it once
the pin moves), any repository mutation, and any release decision.

Usage:
    python tools/bump-radar.py CHECK_JSON

Exit codes: 0 pin current (or the check could not reach the registry),
1 a release is waiting (or the pre-qualification could not be completed).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPOSITORY = "https://github.com/Synaptic-Labs-AI/PACT-Plugin.git"
MANIFEST = Path("harness/upstream-surface.v1.json")
BASELINE_DIR = Path("harness/corpus")

RECOMMENDATION = {
    "L0": "version string only; no surface change",
    "L1": "absorb mechanically: recompile the playbook and regenerate the corpus baseline",
    "L2": "review required: changed or added modules or an additive ABI delta",
    "L3": "review required: breaking surface change; runtime or adapter work may be needed",
    "L4": "structural change: this is an adapter and runtime release, not an absorb",
}


def write_summary(lines: list[str]) -> None:
    text = "\n".join(lines).rstrip() + "\n"
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(text)
    print(text)


def pact(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pact_harness", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def probe(name: str, args: list[str], env: dict[str, str]) -> tuple[dict | None, str]:
    """Run one gate against the pending tree; return (payload, failure detail)."""
    proc = pact(args, env)
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    payload: dict | None = None
    if stdout.startswith("{"):
        try:
            payload = json.loads(stdout)
        except ValueError:
            payload = None
    if payload is None:
        detail = (stderr or stdout).splitlines()
        return None, detail[-1] if detail else f"exit {proc.returncode}"
    return payload, ""


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip())
        return 2
    report = json.loads(Path(argv[1]).read_text(encoding="utf-8"))

    if not report.get("checked"):
        write_summary(
            [
                "## Upstream release radar",
                "",
                "Could not read upstream releases: " + str(report.get("error") or "unknown error"),
                "",
                "The pin was not checked. This is a network or registry problem, not a",
                "pending-bump signal, so the lane stays green.",
            ]
        )
        print("::warning::could not check upstream releases; see job summary")
        return 0

    pinned = str(report.get("pinned_version") or "")
    latest = str(report.get("latest_version") or "")
    tag = str(report.get("latest_tag") or "v" + latest)
    behind = int(report.get("behind") or 0)

    if behind == 0:
        write_summary(
            [
                "## Upstream release radar",
                "",
                "Pin is current: PACT " + pinned + " is upstream's latest release (" + tag + ").",
                "",
                "Nothing to absorb.",
            ]
        )
        return 0

    newer = [str(item) for item in report.get("newer_tags") or []][:10]
    lines = [
        "## Upstream release radar",
        "",
        "**A release is waiting.** Pin is at PACT " + pinned + "; upstream is at "
        + latest + " (" + tag + "), " + str(behind) + " release(s) ahead.",
        "",
        "Run: tools/sync-upstream.sh " + tag,
        "",
    ]
    if newer:
        lines += ["Waiting releases: " + ", ".join(newer), ""]

    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(Path.cwd() / "python" / "src"))
    temp_root = Path(tempfile.mkdtemp(prefix="pact-radar-"))
    tree = temp_root / "upstream"
    surface = temp_root / "surface.json"
    rows: list[str] = []
    level = "unknown"
    differences: list[dict] = []
    try:
        try:
            subprocess.run(
                ["git", "clone", "--quiet", "--depth", "1", "--branch", tag, REPOSITORY, str(tree)],
                check=True,
                capture_output=True,
                text=True,
            )
        except (subprocess.CalledProcessError, OSError) as exc:
            detail = (getattr(exc, "stderr", "") or str(exc)).strip().splitlines()
            lines += [
                "Pre-qualification could not clone the pending tag:",
                "",
                "    " + (detail[-1] if detail else str(exc)),
            ]
            write_summary(lines)
            print("::error::upstream release is waiting; pre-qualification could not clone it")
            return 1

        plugin = tree / "pact-plugin"

        proc = pact(["upstream", "inspect", "--portable", "--plugin-root", str(plugin), "--out", str(surface)], env)
        inspected = surface.is_file()
        rows.append("| upstream inspect | " + ("ok" if inspected else "failed") + " |")

        if inspected:
            proc = pact(["upstream", "diff", str(MANIFEST), str(surface), "--json"], env)
            diff_payload: dict | None = None
            if (proc.stdout or "").strip().startswith("{"):
                try:
                    diff_payload = json.loads(proc.stdout)
                except ValueError:
                    diff_payload = None
            if diff_payload is None:
                detail = (proc.stderr or proc.stdout).strip().splitlines()
                rows.append("| upstream diff | failed: " + (detail[-1] if detail else "unparseable") + " |")
            else:
                level = str(diff_payload.get("level") or "unknown")
                differences = [item for item in diff_payload.get("differences") or [] if isinstance(item, dict)]
                counts: dict[str, int] = {}
                for item in differences:
                    kind = str(item.get("kind") or "unknown")
                    counts[kind] = counts.get(kind, 0) + 1
                rendered = ", ".join(kind + " x" + str(count) for kind, count in sorted(counts.items())) or "none"
                rows.append("| upstream diff | " + level + " (" + rendered + ") |")
        else:
            rows.append("| upstream diff | skipped: no surface was extracted |")

        payload, detail = probe("upstream verify", ["upstream", "verify", "--plugin-root", str(plugin), "--json"], env)
        if payload is None:
            rows.append("| upstream verify | failed: " + detail + " |")
        else:
            rows.append("| upstream verify | " + str(len(payload.get("errors") or [])) + " error(s) |")

        payload, detail = probe(
            "conformance",
            ["conformance", "--plugin-root", str(plugin), "--parity", "--json"],
            env,
        )
        if payload is None:
            rows.append("| conformance + parity | failed: " + detail + " |")
        else:
            counts = payload.get("counts") or {}
            failures = payload.get("failures") or []
            parity = payload.get("parity") or {}
            mismatch = sum(1 for cell in parity.get("cells") or [] if not cell.get("ok", True))
            rows.append(
                "| conformance + parity | "
                + str(counts.get("exercised", 0)) + "/" + str(counts.get("total_registrations", 0))
                + " registrations, " + str(len(failures)) + " failure(s), "
                + str(mismatch) + " parity mismatch(es) |"
            )

        baseline = BASELINE_DIR / ("pact-" + pinned + ".json")
        if baseline.is_file():
            payload, detail = probe(
                "corpus replay",
                ["corpus", "replay", str(baseline), "--plugin-root", str(plugin), "--json"],
                env,
            )
            if payload is None:
                rows.append("| corpus replay | failed: " + detail + " |")
            else:
                rows.append("| corpus replay | " + str(len(payload.get("changes") or [])) + " verdict change(s) |")
        else:
            rows.append("| corpus replay | skipped: no baseline for " + pinned + " |")
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    lines += [
        "### Pre-qualification against " + tag,
        "",
        "| Probe | Result |",
        "|---|---|",
        *rows,
        "",
        "### What this means",
        "",
        "Surface grade: **" + level + "** - " + RECOMMENDATION.get(level, "see the diff output") + ".",
    ]
    if differences:
        lines += ["", "Named changes:", ""]
        for item in differences[:12]:
            extra = " - " + str(item["detail"]) if item.get("detail") else ""
            lines.append("- " + str(item.get("level", "")) + " " + str(item.get("kind", "")) + ": " + str(item.get("identity", "")) + extra)
        if len(differences) > 12:
            lines.append("- ... and " + str(len(differences) - 12) + " more")
    lines += [
        "",
        "This job failed on purpose: a release is waiting. Absorb it deliberately,",
        "then the radar goes green once the pin matches upstream again.",
    ]
    write_summary(lines)
    print("::error::PACT " + latest + " (" + tag + ") is waiting; surface grade " + level + "; run tools/sync-upstream.sh " + tag)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
