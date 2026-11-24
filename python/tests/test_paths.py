"""Path and interpreter resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from pact_harness import paths


def test_pact_home_wins(tmp_path: Path) -> None:
    state = paths.resolve_state_root(
        adapter="dsh", env={"PACT_HOME": str(tmp_path / "custom")}, home=tmp_path
    )
    assert state.path == tmp_path / "custom"
    assert state.source == "PACT_HOME"


def test_pact_home_expands_tilde(tmp_path: Path) -> None:
    state = paths.resolve_state_root(adapter="dsh", env={"PACT_HOME": "~/state"}, home=tmp_path)
    assert state.path == tmp_path / "state"


def test_claude_adapter_honours_config_dir(tmp_path: Path) -> None:
    state = paths.resolve_state_root(
        adapter="claude-code",
        env={"CLAUDE_CONFIG_DIR": str(tmp_path / "claude")},
        home=tmp_path,
    )
    assert state.path == tmp_path / "claude"
    assert state.source == "CLAUDE_CONFIG_DIR"


def test_claude_adapter_default_is_dot_claude(tmp_path: Path) -> None:
    state = paths.resolve_state_root(adapter="claude-code", env={}, home=tmp_path)
    assert state.path == tmp_path / ".claude"


def test_other_adapters_default_to_dot_pact(tmp_path: Path) -> None:
    for adapter in ("dsh", "generic", None):
        state = paths.resolve_state_root(adapter=adapter, env={}, home=tmp_path)
        assert state.path == tmp_path / ".pact", adapter


def test_memory_dir_is_pinned_to_home_not_state_root(tmp_path: Path) -> None:
    # Upstream pins the database deliberately; the harness must not move it.
    state = paths.resolve_state_root(
        adapter="dsh", env={"PACT_HOME": str(tmp_path / "state")}, home=tmp_path
    )
    assert state.path == tmp_path / "state"
    assert paths.resolve_memory_dir(home=tmp_path) == tmp_path / ".claude" / "pact-memory"


def test_backlog_dir_is_pinned_to_home(tmp_path: Path) -> None:
    assert paths.resolve_backlog_dir(home=tmp_path) == tmp_path / ".claude" / "pact-backlog"


def test_python_prefers_pact_python() -> None:
    seen: list[str] = []

    def probe(executable: str) -> tuple[int, int, int] | None:
        seen.append(executable)
        return (3, 12, 14)

    resolution = paths.resolve_python(env={"PACT_PYTHON": "custom-python"}, probe=probe)
    assert resolution.runtime is not None
    assert resolution.runtime.source == "PACT_PYTHON"
    assert resolution.runtime.executable == "custom-python"
    assert seen == ["custom-python"]


def test_python_skips_a_too_old_pact_python() -> None:
    def probe(executable: str) -> tuple[int, int, int] | None:
        return (3, 11, 9) if executable == "old-python" else (3, 12, 0)

    resolution = paths.resolve_python(env={"PACT_PYTHON": "old-python"}, probe=probe)
    assert resolution.runtime is not None
    assert resolution.runtime.source == "python3"
    assert [entry[0] for entry in resolution.considered] == ["PACT_PYTHON", "python3"]


def test_python_falls_back_to_dsh_bundled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundled = tmp_path / "python3"
    bundled.write_text("")
    monkeypatch.setattr(paths, "DSH_BUNDLED_PYTHON", bundled)

    def probe(executable: str) -> tuple[int, int, int] | None:
        return None if executable == "python3" else (3, 12, 14)

    resolution = paths.resolve_python(env={}, probe=probe)
    assert resolution.runtime is not None
    assert resolution.runtime.source == "dsh-bundled"


def test_python_returns_nothing_when_none_qualify() -> None:
    resolution = paths.resolve_python(env={}, probe=lambda executable: (3, 9, 0))
    assert resolution.runtime is None
    assert len(resolution.considered) >= 2


def test_runtime_flags_certification() -> None:
    certified = paths.PythonRuntime(executable="p", version=(3, 12, 14), source="test")
    newer = paths.PythonRuntime(executable="p", version=(3, 14, 6), source="test")
    assert certified.certified and certified.meets_floor
    assert not newer.certified and newer.meets_floor


def test_find_harness_root_from_package() -> None:
    root = paths.find_harness_root()
    assert root is not None
    assert (root / "harness" / "pact-pin.json").is_file()


def test_plugin_root_override_wins(tmp_path: Path) -> None:
    assert paths.resolve_plugin_root(env={"PACT_PLUGIN_ROOT": str(tmp_path)}) == tmp_path


def test_plugin_root_falls_back_to_vendor() -> None:
    root = paths.find_harness_root()
    assert root is not None
    plugin = paths.resolve_plugin_root(env={}, harness_root=root)
    assert plugin == root / "vendor" / "pact-plugin"
