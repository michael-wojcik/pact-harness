"""The warm transport: parity with a cold process, and the fork hazards."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

from pact_harness import paths
from pact_harness.runtime import warm
from pact_harness.runtime.launch import launch_hook
from pact_harness.runtime.registry import HookRegistry

pytestmark = pytest.mark.skipif(
    not warm.warm_supported(), reason="the warm transport requires os.fork"
)


def plugin_root() -> Path:
    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    candidate = paths.resolve_plugin_root(env={}, harness_root=root)
    if candidate is None or not candidate.is_dir():
        pytest.skip("vendored submodule not checked out")
    return candidate


def package_env() -> dict[str, str]:
    env = dict(os.environ)
    src = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


@pytest.fixture(scope="module")
def worker():
    root = plugin_root()
    directory = Path(tempfile.mkdtemp())
    handle = warm.spawn_worker(
        python=sys.executable,
        plugin_root=root,
        socket_path=directory / "worker.sock",
        base_env=package_env(),
    )
    try:
        yield handle
    finally:
        handle.stop()


def edit_frame(tmp_path: Path) -> bytes:
    return (
        '{"session_id":"sess-warm","cwd":"%s","hook_event_name":"PreToolUse",'
        '"tool_name":"Edit","tool_input":{"file_path":"%s/a.py","old_string":"a","new_string":"b"},'
        '"agent_type":"pact-orchestrator","tool_use_id":"toolu_warm"}'
        % (tmp_path, tmp_path)
    ).encode("utf-8")


def hook_env(tmp_path: Path) -> dict[str, str]:
    env = package_env()
    state = tmp_path / "state"
    state.mkdir()
    env.update(
        {
            "CLAUDE_CONFIG_DIR": str(state),
            "CLAUDE_PLUGIN_ROOT": str(plugin_root()),
            "CLAUDE_PROJECT_DIR": str(tmp_path),
        }
    )
    return env


def test_worker_starts_and_accepts(worker) -> None:
    assert worker.process.poll() is None
    assert worker.socket_path.exists()


def test_warm_output_matches_cold_for_a_cheap_hook(worker, tmp_path: Path) -> None:
    registry = HookRegistry.from_plugin_root(plugin_root())
    registration = next(
        item for item in registry.registrations if item.hook == "worktree_guard"
    )
    stdin = edit_frame(tmp_path)
    env = hook_env(tmp_path)
    cold = launch_hook(registration.script, stdin, env=env, python=sys.executable, cwd=str(tmp_path))
    hot = worker.transport.run(registration.script, stdin, env=env, python=sys.executable, cwd=str(tmp_path))
    assert hot.exit == cold.exit
    assert hot.stdout == cold.stdout
    assert hot.stderr == cold.stderr


def test_warm_output_matches_cold_for_a_hook_with_rich_output(worker, tmp_path: Path) -> None:
    # bootstrap_gate emits a compound decision object, so equality here proves
    # the fork plumbing carries more than a one-line suppressOutput.
    registry = HookRegistry.from_plugin_root(plugin_root())
    registration = next(
        item for item in registry.registrations if item.hook == "bootstrap_gate"
    )
    stdin = edit_frame(tmp_path)
    env = hook_env(tmp_path)
    cold = launch_hook(registration.script, stdin, env=env, python=sys.executable, cwd=str(tmp_path))
    hot = worker.transport.run(registration.script, stdin, env=env, python=sys.executable, cwd=str(tmp_path))
    assert hot.exit == cold.exit
    assert hot.stdout == cold.stdout


def test_script_outside_the_plugin_root_is_refused(worker, tmp_path: Path) -> None:
    outside = tmp_path / "evil.py"
    outside.write_text("print('should not run')")
    result = worker.transport.run(outside, b"", env={}, python=sys.executable)
    assert result.launch_error is not None
    assert "outside the plugin root" in result.launch_error


def test_forked_child_captures_stdout(tmp_path: Path) -> None:
    script = tmp_path / "talky.py"
    script.write_text("print('{\"ok\": true}')")
    result = warm.run_forked(script, b"", env={})
    assert result["timed_out"] is False
    assert result["exit"] == 0
    assert result["stdout"].strip() == b'{"ok": true}'


def test_forked_child_receives_stdin(tmp_path: Path) -> None:
    script = tmp_path / "reader.py"
    script.write_text("import sys\nprint(sys.stdin.read().strip())\n")
    result = warm.run_forked(script, b"frame-bytes", env={})
    assert result["stdout"].strip() == b"frame-bytes"


def test_forked_child_reports_its_exit_code(tmp_path: Path) -> None:
    script = tmp_path / "refuse.py"
    script.write_text("import sys\nsys.stderr.write('nope')\nsys.exit(2)\n")
    result = warm.run_forked(script, b"", env={})
    assert result["exit"] == 2
    assert result["stderr"] == b"nope"


def test_forked_child_is_killed_on_timeout(tmp_path: Path) -> None:
    script = tmp_path / "sleepy.py"
    script.write_text("import time\ntime.sleep(30)\n")
    result = warm.run_forked(script, b"", env={}, timeout_s=0.5)
    assert result["timed_out"] is True
    assert result["exit"] is None


def test_forked_child_gets_the_requested_environment(tmp_path: Path) -> None:
    script = tmp_path / "env.py"
    script.write_text("import os\nprint(os.environ.get('CLAUDE_PROJECT_DIR', 'missing'))\n")
    result = warm.run_forked(script, b"", env={"CLAUDE_PROJECT_DIR": "/tmp/project"})
    assert result["stdout"].strip() == b"/tmp/project"
