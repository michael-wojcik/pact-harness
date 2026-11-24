"""Harness error types.

Every failure the harness raises deliberately is a subclass of
PactHarnessError, so callers can distinguish a declared refusal from a
programming error.
"""

from __future__ import annotations


class PactHarnessError(Exception):
    """Base class for every deliberate harness failure."""


class ContractError(PactHarnessError):
    """A document did not satisfy the harness contract."""


class VendorError(PactHarnessError):
    """The vendored PACT tree is missing, dirty, or not at the pinned commit."""
