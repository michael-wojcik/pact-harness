"""Shared fixtures for the harness suite."""

from __future__ import annotations

import pytest

from pact_harness import paths
from pact_harness.vendor import load_pin


@pytest.fixture(scope="session")
def pin():
    """The pin of record, so tests follow an upstream bump instead of pinning it."""
    root = paths.find_harness_root()
    if root is None:
        pytest.skip("not running inside the harness checkout")
    return load_pin(root)
