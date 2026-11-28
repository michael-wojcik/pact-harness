"""Replay PACT's own test frames on the parity lane.

Upstream's suite calls hooks as child processes. A sitecustomize recorder
(tools/upstream_recorder) captures the Claude-shaped stdin each invocation
received; this module loads those records as corpus frames and rebases their
paths onto a fresh scratch tree, so the direct and bridged paths can both run
them and be compared byte for byte.

Nothing here writes to the vendored tree: harvesting copies frames into a JSON
document under the harness, and replaying only reads them.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..errors import PactHarnessError
from .corpus import CorpusFrame

PLACEHOLDER = "<project>"
SCHEMA_VERSION = 1

#: pytest arguments for a harvest: the suite's own defaults, no cache writes.
PYTEST_BASE = ("tests", "-q", "-p", "no:cacheprovider")


class UpstreamCorpusError(PactHarnessError):
    """A recorded corpus could not be read or produced."""


def _rebase(value: Any, project: str) -> Any:
    if isinstance(value, str):
        return value.replace(PLACEHOLDER, project)
    if isinstance(value, list):
        return [_rebase(item, project) for item in value]
    if isinstance(value, dict):
        return {key: _rebase(item, project) for key, item in value.items()}
    return value


def _placeholder(value: Any, recorded_cwd: str) -> Any:
    if isinstance(value, str):
        return value.replace(recorded_cwd, PLACEHOLDER)
    if isinstance(value, list):
        return [_placeholder(item, recorded_cwd) for item in value]
    if isinstance(value, dict):
        return {key: _placeholder(item, recorded_cwd) for key, item in value.items()}
    return value


def rebase(frame: CorpusFrame, project: Path) -> CorpusFrame:
    """Point a recorded frame at a replay tree."""
    return replace(frame, frame=_rebase(frame.frame, str(project)))


def load_recorded_frames(path: Path | str, *, limit: int = 2000) -> tuple[CorpusFrame, ...]:
    """Read a recorded JSONL file (or a harvested JSON document) into frames."""
    source = Path(path)
    if not source.is_file():
        raise UpstreamCorpusError(f"no recorded corpus at {source}")
    if source.suffix == ".json":
        document = json.loads(source.read_text(encoding="utf-8"))
        if isinstance(document, dict) and "frames" in document:
            records = document["frames"]
        elif isinstance(document, list):
            records = document
        else:
            raise UpstreamCorpusError(f"{source} is not a corpus document")
    else:
        records = []
        for line in source.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    frames: list[CorpusFrame] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        frame = record.get("frame")
        hook = record.get("hook")
        if not isinstance(frame, dict):
            continue
        if not isinstance(hook, str) or not hook:
            hook = str(record.get("name") or "upstream")
        digest = json.dumps(frame, sort_keys=True)
        if digest in seen:
            continue
        seen.add(digest)
        recorded_cwd = str(record.get("cwd") or frame.get("cwd") or "")
        if recorded_cwd:
            frame = _placeholder(frame, recorded_cwd)
        event = frame.get("hook_event_name") or record.get("event") or ""
        if not isinstance(event, str) or not event:
            continue
        tool_name = frame.get("tool_name")
        name = "upstream:" + hook.replace("/", "_") + ":" + str(len(frames))
        frames.append(
            CorpusFrame(
                name=name,
                event=event,
                frame=frame,
                tool_name=tool_name if isinstance(tool_name, str) else None,
            )
        )
        if len(frames) >= limit:
            break
    if not frames:
        raise UpstreamCorpusError(f"{source} contained no usable recorded frames")
    return tuple(frames)


def harvest(
    repo: Path,
    *,
    out: Path,
    recorder: Path,
    select: str | None = None,
    limit: int = 2000,
) -> tuple[int, int]:
    """Run upstream's suite with the recorder and write a corpus document.

    Returns (pytest exit code, frame count). The suite is expected to pass; a
    non-zero exit still leaves whatever was recorded, and the caller reports both.
    """
    plugin = repo / "pact-plugin"
    if not (plugin / "tests").is_dir():
        raise UpstreamCorpusError(f"{plugin}/tests is missing; not an upstream checkout")
    if not (recorder / "sitecustomize.py").is_file():
        raise UpstreamCorpusError(f"recorder missing at {recorder}")
    handle = tempfile.NamedTemporaryFile(prefix="pact-upstream-", suffix=".jsonl", delete=False)
    recorded = Path(handle.name)
    handle.close()
    recorded.unlink(missing_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(recorder) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    env["PACT_CORPUS_OUT"] = str(recorded)
    for key in ("CLAUDE_", "PACT_"):
        env.pop(key, None)
    args = [sys.executable, "-m", "pytest", *PYTEST_BASE]
    if select:
        args += ["-k", select]
    proc = subprocess.run(args, cwd=str(plugin), env=env)
    frames = ()
    if recorded.exists():
        try:
            frames = load_recorded_frames(recorded, limit=limit)
        finally:
            recorded.unlink(missing_ok=True)
    document = {
        "v": SCHEMA_VERSION,
        "source": "PACT-Plugin test suite",
        "pytestExit": proc.returncode,
        "frames": [{"name": frame.name, "event": frame.event, "frame": frame.frame} for frame in frames],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return proc.returncode, len(frames)
