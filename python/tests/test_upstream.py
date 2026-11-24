"""The upstream surface: extraction, classification, and verify judgement."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pact_harness import paths
from pact_harness.upstream.inspect import Artifact, EnvRead, Surface, inspect_surface
from pact_harness.upstream.verify import verify_surface


def plugin_root() -> Path:
    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    candidate = paths.resolve_plugin_root(env={}, harness_root=root)
    if candidate is None or not candidate.is_dir():
        pytest.skip("vendored submodule not checked out")
    return candidate


def pin_values() -> tuple[str, str, str]:
    from pact_harness.vendor import load_pin

    root = paths.find_harness_root()
    if root is None:
        return "", "", ""
    pin = load_pin(root)
    return pin.pact_version, pin.commit, pin.tag or ""


@pytest.fixture(scope="module")
def surface() -> Surface:
    version, commit, tag = pin_values()
    return inspect_surface(plugin_root(), pact_version=version, commit=commit, tag=tag)


def minimal_surface(**overrides) -> Surface:
    base = dict(
        plugin_root="/tmp/plugin",
        registrations=(
            {"event": "PreToolUse", "hook": "h", "matcher": "Edit", "script": "hooks/h.py"},
        ),
        matchers=("Edit",),
        tool_sets={"_BLOCKED_TOOLS": ("Edit", "Write")},
        env_reads=(EnvRead(name="CLAUDE_CONFIG_DIR", file="hooks/h.py", line=1),),
        artifacts=(
            Artifact(kind="agents", path="agents/a.md", sha256="0" * 64, bytes=1),
            Artifact(kind="commands", path="commands/c.md", sha256="0" * 64, bytes=1),
            Artifact(kind="skills", path="skills/s/SKILL.md", sha256="0" * 64, bytes=1),
            Artifact(kind="protocols", path="protocols/p.md", sha256="0" * 64, bytes=1),
        ),
        decision_tokens={"permissionDecision": 1, "hookSpecificOutput": 1},
    )
    base.update(overrides)
    return Surface(**base)


def test_the_registry_is_extracted(surface: Surface) -> None:
    assert len(surface.registrations) == 34
    assert len({item["event"] for item in surface.registrations}) == 13
    assert len({item["hook"] for item in surface.registrations}) == 28


def test_the_matcher_vocabulary_is_extracted(surface: Surface) -> None:
    assert "Edit|Write" in surface.matchers
    assert "Bash" in surface.matchers
    assert "Agent" in surface.matchers


def test_the_gate_tool_sets_are_extracted(surface: Surface) -> None:
    assert set(surface.tool_sets) == {"_BLOCKED_TOOLS", "_READ_ONLY_TOOLS", "_DEGRADED_ASK_TOOLS"}
    assert "Edit" in surface.tool_sets["_BLOCKED_TOOLS"]
    assert "Bash" in surface.tool_sets["_READ_ONLY_TOOLS"] or "Read" in surface.tool_sets["_READ_ONLY_TOOLS"]
    assert set(surface.tool_sets["_DEGRADED_ASK_TOOLS"]) == {"WebFetch", "WebSearch"}


def test_env_reads_include_helper_indirected_names(surface: Surface) -> None:
    names = {read.name for read in surface.env_reads}
    assert "CLAUDE_CONFIG_DIR" in names
    assert "CLAUDE_PROJECT_DIR" in names
    # Read through a module constant, which a naive scan misses.
    assert "CLAUDE_CODE_REMOTE" in names


def test_the_artifact_inventory_is_hashed(surface: Surface) -> None:
    counts: dict[str, int] = {}
    for artifact in surface.artifacts:
        counts[artifact.kind] = counts.get(artifact.kind, 0) + 1
    assert counts == {"agents": 13, "commands": 14, "protocols": 22, "reference": 3, "skills": 21}
    assert all(len(artifact.sha256) == 64 for artifact in surface.artifacts)


def test_decision_tokens_are_counted(surface: Surface) -> None:
    for token in ("permissionDecision", "additionalContext", "systemMessage", "hookSpecificOutput"):
        assert surface.decision_tokens[token] > 0, token


def test_extraction_is_deterministic(surface: Surface) -> None:
    version, commit, tag = pin_values()
    again = inspect_surface(plugin_root(), pact_version=version, commit=commit, tag=tag)
    assert surface.to_dict() == again.to_dict()


def test_a_healthy_tree_verifies(surface: Surface) -> None:
    report = verify_surface(surface)
    assert report.ok, report.errors
    assert report.facts["registrations"] == 34
    assert report.facts["env_unclassified"] == []


def test_a_matcher_outside_the_vocabulary_fails() -> None:
    report = verify_surface(minimal_surface(matchers=("Edit|Wibble",)))
    assert not report.ok
    assert any("Wibble" in error for error in report.errors)


def test_a_tool_set_name_outside_the_vocabulary_fails() -> None:
    report = verify_surface(minimal_surface(tool_sets={"_BLOCKED_TOOLS": ("Edit", "Frobnicate")}))
    assert not report.ok
    assert any("Frobnicate" in error for error in report.errors)


def test_an_unclassified_env_read_fails() -> None:
    report = verify_surface(
        minimal_surface(env_reads=(EnvRead(name="SOME_NEW_VAR", file="hooks/h.py", line=3),))
    )
    assert not report.ok
    assert any("SOME_NEW_VAR" in error for error in report.errors)


def test_an_unclassified_env_read_can_be_downgraded() -> None:
    report = verify_surface(
        minimal_surface(env_reads=(EnvRead(name="SOME_NEW_VAR", file="hooks/h.py", line=3),)),
        allow_unknown_env=True,
    )
    assert report.ok
    assert any("SOME_NEW_VAR" in warning for warning in report.warnings)


def test_missing_artifacts_fail() -> None:
    report = verify_surface(minimal_surface(artifacts=()))
    assert not report.ok
    assert any("no agents artifacts" in error for error in report.errors)


def test_an_empty_registry_fails() -> None:
    report = verify_surface(minimal_surface(registrations=(), matchers=()))
    assert not report.ok
    assert any("registry is empty" in error for error in report.errors)


def test_the_report_serialises(surface: Surface) -> None:
    payload = verify_surface(surface).to_dict()
    assert payload["ok"] is True
    assert payload["facts"]["modules"] == 28
    json.dumps(payload)


def test_the_committed_manifest_matches_the_vendored_tree(surface: Surface) -> None:
    """The committed baseline must be regenerated when the vendored tree moves.

    Without this, a pin bump could land with a stale manifest and the first
    person to run a diff would be comparing against a tree that no longer
    exists.
    """
    root = paths.find_harness_root()
    assert root is not None
    committed = json.loads((root / "harness" / "upstream-surface.v1.json").read_text())
    fresh = surface.to_dict()
    for key in ("registry", "matchers", "tool_sets", "env_reads", "artifacts", "decision_tokens", "pact"):
        assert committed[key] == fresh[key], key


def test_identical_surfaces_are_l0(surface: Surface) -> None:
    from pact_harness.upstream.diff import Level, diff_surfaces

    report = diff_surfaces(surface.to_dict(), surface.to_dict())
    assert report.level is Level.L0
    assert report.differences == ()
    assert report.automatic is True


def test_reordering_the_registry_is_not_a_change(surface: Surface) -> None:
    import copy

    from pact_harness.upstream.diff import Level, diff_surfaces

    document = copy.deepcopy(surface.to_dict())
    document["registry"]["items"].reverse()
    report = diff_surfaces(surface.to_dict(), document)
    assert report.level is Level.L0


def test_an_artifact_change_is_l1(surface: Surface) -> None:
    import copy

    from pact_harness.upstream.diff import Level, diff_surfaces

    document = copy.deepcopy(surface.to_dict())
    document["artifacts"][0]["sha256"] = "f" * 64
    report = diff_surfaces(surface.to_dict(), document)
    assert report.level is Level.L1
    assert report.automatic is True
    assert report.differences[0].kind == "artifact-changed"


def test_an_added_registration_is_l2(surface: Surface) -> None:
    import copy

    from pact_harness.upstream.diff import Level, diff_surfaces

    document = copy.deepcopy(surface.to_dict())
    document["registry"]["items"].append(
        {
            "event": "PreToolUse",
            "group": 9,
            "command": 0,
            "matcher": "Read",
            "hook": "new_gate",
            "script": "hooks/new_gate.py",
            "timeout_s": None,
            "async": False,
        }
    )
    report = diff_surfaces(surface.to_dict(), document)
    assert report.level is Level.L2
    assert report.automatic is False


def test_a_removed_registration_is_l3(surface: Surface) -> None:
    import copy

    from pact_harness.upstream.diff import Level, diff_surfaces

    document = copy.deepcopy(surface.to_dict())
    document["registry"]["items"].pop(0)
    report = diff_surfaces(surface.to_dict(), document)
    assert report.level is Level.L3


def test_a_changed_timeout_is_l3(surface: Surface) -> None:
    import copy

    from pact_harness.upstream.diff import Level, diff_surfaces

    document = copy.deepcopy(surface.to_dict())
    for item in document["registry"]["items"]:
        if item["hook"] == "pin_caps_gate":
            item["timeout_s"] = 60.0
    report = diff_surfaces(surface.to_dict(), document)
    assert report.level is Level.L3
    assert any(difference.kind == "registration-changed" for difference in report.differences)


def test_tool_and_matcher_additions_are_l2_and_removals_l3(surface: Surface) -> None:
    import copy

    from pact_harness.upstream.diff import Level, diff_surfaces

    added = copy.deepcopy(surface.to_dict())
    added["tool_sets"]["_BLOCKED_TOOLS"].append("Frobnicate")
    added["matchers"].append("Frobnicate")
    assert diff_surfaces(surface.to_dict(), added).level is Level.L2

    removed = copy.deepcopy(surface.to_dict())
    removed["tool_sets"]["_BLOCKED_TOOLS"].remove("Edit")
    removed["matchers"].remove("Bash")
    report = diff_surfaces(surface.to_dict(), removed)
    assert report.level is Level.L3
    # "Bash" still appears inside "Edit|Write|Bash", so the token survives; the
    # grouping change is what this removal really is.
    assert {difference.kind for difference in report.differences} >= {
        "tool-removed",
        "matcher-group-removed",
    }

    token_gone = copy.deepcopy(surface.to_dict())
    token_gone["matchers"].remove("Agent")
    report = diff_surfaces(surface.to_dict(), token_gone)
    assert report.level is Level.L3
    assert any(difference.kind == "matcher-removed" for difference in report.differences)


def test_env_additions_are_l2_and_removals_l3(surface: Surface) -> None:
    import copy

    from pact_harness.upstream.diff import Level, diff_surfaces

    added = copy.deepcopy(surface.to_dict())
    added["env_reads"].append({"name": "CLAUDE_NEW_THING", "file": "hooks/h.py", "line": 1})
    assert diff_surfaces(surface.to_dict(), added).level is Level.L2

    removed = copy.deepcopy(surface.to_dict())
    removed["env_reads"] = [
        read for read in removed["env_reads"] if read["name"] != "CLAUDE_CONFIG_DIR"
    ]
    report = diff_surfaces(surface.to_dict(), removed)
    assert report.level is Level.L3
    assert any(difference.kind == "env-removed" for difference in report.differences)


def test_an_emptied_registry_is_l4(surface: Surface) -> None:
    import copy

    from pact_harness.upstream.diff import Level, diff_surfaces

    document = copy.deepcopy(surface.to_dict())
    document["registry"]["items"] = []
    report = diff_surfaces(surface.to_dict(), document)
    assert report.level is Level.L4


def test_an_emptied_artifact_kind_is_l4(surface: Surface) -> None:
    import copy

    from pact_harness.upstream.diff import Level, diff_surfaces

    document = copy.deepcopy(surface.to_dict())
    document["artifacts"] = [a for a in document["artifacts"] if a["kind"] != "agents"]
    report = diff_surfaces(surface.to_dict(), document)
    assert report.level is Level.L4


def test_the_cli_diffs_two_manifests(tmp_path: Path) -> None:
    from pact_harness import cli

    first = tmp_path / "old.json"
    second = tmp_path / "new.json"
    assert cli.main(["upstream", "inspect", "--out", str(first)]) == 0
    document = json.loads(first.read_text())
    document["artifacts"][0]["sha256"] = "e" * 64
    second.write_text(json.dumps(document))
    assert cli.main(["upstream", "diff", str(first), str(second)]) == 0
    assert cli.main(["upstream", "diff", str(first), str(second), "--json"]) == 0


SAMPLE_TAGS = """\
1111111111111111111111111111111111111111\trefs/tags/v4.8.35
2222222222222222222222222222222222222222\trefs/tags/v4.8.36
3333333333333333333333333333333333333333\trefs/tags/v4.9.0
4444444444444444444444444444444444444444\trefs/tags/v4.9.0^{}
5555555555555555555555555555555555555555\trefs/tags/v4.10.0
6666666666666666666666666666666666666666\trefs/tags/v5.0.0-rc.1
7777777777777777777777777777777777777777\trefs/heads/main
"""


def pin(**overrides):
    from pact_harness.vendor import PactPin

    base = dict(
        pact_version="4.8.36",
        commit="4607c7a079203b97aa1a0c0f1baa006ebb0982a4",
        tag="v4.8.36",
        submodule_path="vendor/pact-plugin",
        repository="https://github.com/Synaptic-Labs-AI/PACT-Plugin.git",
    )
    base.update(overrides)
    return PactPin(**base)


def test_release_tags_are_parsed_and_sorted() -> None:
    from pact_harness.upstream.check import parse_release_tags

    tags = parse_release_tags(SAMPLE_TAGS)
    versions = [entry[0] for entry in tags]
    assert versions == [(4, 8, 35), (4, 8, 36), (4, 9, 0), (4, 10, 0)]
    # A prerelease, a branch ref and the dereference are all excluded.
    refs = [entry[1] for entry in tags]
    assert "refs/tags/v5.0.0-rc.1" not in refs
    assert all(not ref.endswith("^{}") for ref in refs)


def test_the_pin_is_measured_against_the_latest_release() -> None:
    from pact_harness.upstream.check import check_release

    report = check_release(pin(), tags_output=SAMPLE_TAGS)
    assert report.checked is True
    assert report.latest_version == "4.10.0"
    assert report.latest_tag == "v4.10.0"
    assert report.behind == 2
    assert report.newer_tags == ("v4.9.0", "v4.10.0")


def test_a_pin_at_the_latest_release_is_zero_behind() -> None:
    from pact_harness.upstream.check import check_release

    report = check_release(pin(pact_version="4.10.0"), tags_output=SAMPLE_TAGS)
    assert report.behind == 0
    assert report.newer_tags == ()


def test_a_tag_fetch_failure_is_reported_not_raised() -> None:
    from pact_harness.upstream.check import check_release

    def failing_runner(argv, **kwargs):
        raise OSError("no network")

    report = check_release(pin(), runner=failing_runner)
    assert report.checked is False
    assert "cannot reach" in (report.error or "")


def test_no_release_tags_is_reported() -> None:
    from pact_harness.upstream.check import check_release

    report = check_release(pin(), tags_output="abc\trefs/heads/main\n")
    assert report.checked is False
    assert "no release tags" in (report.error or "")


def test_the_check_command_prints_json(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from pact_harness import cli
    from pact_harness.upstream import check as check_module

    monkeypatch.setattr(check_module, "fetch_tags", lambda repository, **kwargs: SAMPLE_TAGS)
    code = cli.main(["upstream", "check", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["behind"] == 2
    assert payload["latest_tag"] == "v4.10.0"


def test_the_sync_script_is_valid_shell_and_valid_python() -> None:
    import subprocess

    root = paths.find_harness_root()
    assert root is not None
    script = root / "tools" / "sync-upstream.sh"
    completed = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    text = script.read_text(encoding="utf-8")
    start = text.index("<<'PY'") + len("<<'PY'")
    end = text.index("\nPY", start)
    # compile() rather than exec(): a heredoc with a mangled escape is a
    # syntax error, which is exactly the defect this guard is for.
    compile(text[start:end], "sync-upstream-heredoc", "exec")


def test_the_cli_inspects_and_verifies(tmp_path: Path) -> None:
    from pact_harness import cli

    out = tmp_path / "surface.json"
    assert cli.main(["upstream", "inspect", "--out", str(out)]) == 0
    document = json.loads(out.read_text())
    assert document["registry"]["registrations"] == 34
    assert cli.main(["upstream", "verify", "--json"]) == 0
