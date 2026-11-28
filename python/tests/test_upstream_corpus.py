"""The upstream-test corpus: recording frames and replaying them on parity."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pact_harness import cli, paths
from pact_harness.conformance.upstream_corpus import (
    HOME_PLACEHOLDER,
    PLACEHOLDER,
    UpstreamCorpusError,
    load_recorded_frames,
    rebase,
)


def harness_root() -> Path:
    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    return root


def test_the_recorder_records_a_hook_frame(tmp_path: Path) -> None:
    recorder = harness_root() / "tools" / "upstream_recorder"
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hook = hooks / "echo_hook.py"
    hook.write_text(
        "import json, sys\n"
        "frame = json.loads(sys.stdin.read())\n"
        "print(json.dumps({'hookSpecificOutput': {'hookEventName': frame['hook_event_name']}}))\n"
    )
    runner = tmp_path / "runner.py"
    runner.write_text(
        "import json, subprocess, sys\n"
        "frame = {'hook_event_name': 'PreToolUse', 'session_id': 's', 'cwd': sys.argv[1], 'tool_name': 'Edit'}\n"
        "subprocess.run([sys.executable, sys.argv[2]], input=json.dumps(frame), text=True, check=True)\n"
    )
    out = tmp_path / "recorded.jsonl"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(recorder)
    env["PACT_CORPUS_OUT"] = str(out)
    subprocess.run(
        [sys.executable, str(runner), str(tmp_path), str(hook)],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    lines = [line for line in out.read_text().splitlines() if line.strip()]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["hook"] == "hooks/echo_hook.py"
    assert record["event"] == "PreToolUse"
    assert record["frame"]["tool_name"] == "Edit"
    assert record["home"], "the recorder should name the home it recorded under"


def test_recordings_deduplicate_and_placeholder_their_cwd(tmp_path: Path) -> None:
    cwd = str(tmp_path / "recorded-project")
    frame = {"hook_event_name": "PreToolUse", "cwd": cwd, "tool_name": "Bash"}
    record = {"hook": "hooks/task_lifecycle_gate.py", "event": "PreToolUse", "cwd": cwd, "frame": frame}
    path = tmp_path / "recorded.jsonl"
    path.write_text("\n".join([json.dumps(record), json.dumps(record), "not json"]) + "\n")
    frames = load_recorded_frames(path)
    assert len(frames) == 1
    assert frames[0].frame["cwd"] == PLACEHOLDER
    assert frames[0].event == "PreToolUse"
    project = tmp_path / "replay-project"
    project.mkdir()
    moved = rebase(frames[0], project)
    assert moved.frame["cwd"] == str(project)


def test_a_recorded_home_is_placeholdered(tmp_path: Path) -> None:
    home = str(tmp_path / "recorded-home")
    frame = {"hook_event_name": "PreToolUse", "cwd": str(tmp_path / "project"), "transcript_path": home + "/t.jsonl"}
    record = {"hook": "hooks/x.py", "event": "PreToolUse", "cwd": frame["cwd"], "home": home, "frame": frame}
    path = tmp_path / "recorded.jsonl"
    path.write_text(json.dumps(record) + "\n")
    frames = load_recorded_frames(path)
    assert frames[0].frame["transcript_path"] == HOME_PLACEHOLDER + "/t.jsonl"


def test_the_recorder_stays_out_of_dash_c_probes(tmp_path: Path) -> None:
    recorder = harness_root() / "tools" / "upstream_recorder"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(recorder)
    env["PACT_CORPUS_OUT"] = str(tmp_path / "recorded.jsonl")
    probe = subprocess.run(
        [sys.executable, "-c", "import sys; print('subprocess' in sys.modules)"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "False", "the recorder leaked subprocess into a probe interpreter"


def test_missing_recordings_raise(tmp_path: Path) -> None:
    with pytest.raises(UpstreamCorpusError):
        load_recorded_frames(tmp_path / "absent.jsonl")


def test_already_prefixed_names_are_not_double_prefixed(tmp_path: Path) -> None:
    frame = {"hook_event_name": "PreToolUse"}
    document = {
        "v": 1,
        "frames": [{"name": "upstream:hooks_x.py:3", "event": "PreToolUse", "frame": frame}],
    }
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(document))
    frames = load_recorded_frames(path)
    assert frames[0].name.startswith("upstream:hooks_x.py")
    assert "upstream:upstream" not in frames[0].name


def test_the_committed_upstream_corpus_loads() -> None:
    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    path = root / "harness" / "corpus" / "upstream" / "pact-4.8.38.json"
    if not path.is_file():
        pytest.skip("no committed upstream corpus for this pin")
    frames = load_recorded_frames(path)
    assert len(frames) >= 100
    assert len({frame.name for frame in frames}) == len(frames)
    events = {frame.event for frame in frames}
    assert {"PreToolUse", "PostToolUse", "UserPromptSubmit"} <= events
    text = path.read_text()
    assert "/home/runner" not in text
    assert "/Users/" not in text


def test_the_cli_converts_a_recording(tmp_path: Path) -> None:
    frame = {"hook_event_name": "SessionStart", "session_id": "s", "cwd": str(tmp_path / "x")}
    record = {"hook": "hooks/session_init.py", "event": "SessionStart", "cwd": frame["cwd"], "frame": frame}
    recorded = tmp_path / "recorded.jsonl"
    recorded.write_text(json.dumps(record) + "\n")
    out = tmp_path / "cells.json"
    code = cli.main(["corpus", "harvest-upstream", "--from-recorded", str(recorded), "--out", str(out)])
    assert code == 0
    document = json.loads(out.read_text())
    assert document["v"] == 1
    assert len(document["frames"]) == 1
    assert document["frames"][0]["event"] == "SessionStart"
