"""Version identity: one number, stated in every place that must agree."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from pact_harness import CONTRACT_VERSION, __version__


def harness_root() -> Path:
    from pact_harness import paths

    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    return root


def test_the_package_version_is_the_single_source() -> None:
    root = harness_root()
    project = tomllib.loads((root / "python" / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"] == __version__


def test_the_bridge_package_tracks_the_harness_version() -> None:
    root = harness_root()
    adapter = json.loads((root / "adapters" / "dsh" / "package.json").read_text(encoding="utf-8"))
    assert adapter["version"] == __version__


def test_the_contract_version_matches_the_published_schemas() -> None:
    root = harness_root()
    schemas = root / "python" / "src" / "pact_harness" / "contract" / "schema"
    for name in ("event.v1.json", "directive.v1.json"):
        document = json.loads((schemas / name).read_text(encoding="utf-8"))
        assert document["properties"]["v"]["const"] == CONTRACT_VERSION, name
