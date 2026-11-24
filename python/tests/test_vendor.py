"""The vendor pin and its inspection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pact_harness import paths
from pact_harness.errors import VendorError
from pact_harness.vendor import PIN_RELATIVE, inspect_vendor, load_pin


def repo_root() -> Path:
    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    return root


def test_pin_records_upstream_version() -> None:
    pin = load_pin(repo_root())
    assert pin.pact_version == "4.8.36"
    assert pin.tag == "v4.8.36"
    assert len(pin.commit) == 40


def test_vendor_is_at_the_pin() -> None:
    root = repo_root()
    pin = load_pin(root)
    state = inspect_vendor(root, pin)
    if not state.exists:
        pytest.skip("vendored submodule not checked out")
    assert state.commit == pin.commit
    assert state.gitlink_commit == pin.commit
    assert state.dirty is False
    assert state.problems == ()
    assert state.at_pin


def test_mismatched_pin_is_reported() -> None:
    root = repo_root()
    pin = load_pin(root)
    if not (root / pin.submodule_path).is_dir():
        pytest.skip("vendored submodule not checked out")
    wrong = load_pin(root).__class__(
        pact_version=pin.pact_version,
        commit="0" * 40,
        tag=pin.tag,
        submodule_path=pin.submodule_path,
        repository=pin.repository,
    )
    state = inspect_vendor(root, wrong)
    assert state.at_pin is False
    joined = " ".join(state.problems)
    assert "does not match pin" in joined


def test_missing_pin_file_raises(tmp_path: Path) -> None:
    with pytest.raises(VendorError, match="pin file missing"):
        load_pin(tmp_path)


def test_malformed_pin_file_raises(tmp_path: Path) -> None:
    target = tmp_path / PIN_RELATIVE
    target.parent.mkdir(parents=True)
    target.write_text("{oops", encoding="utf-8")
    with pytest.raises(VendorError, match="pin file unreadable"):
        load_pin(tmp_path)


def test_pin_missing_keys_raises(tmp_path: Path) -> None:
    target = tmp_path / PIN_RELATIVE
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"schemaVersion": 1}), encoding="utf-8")
    with pytest.raises(VendorError, match="missing key"):
        load_pin(tmp_path)
