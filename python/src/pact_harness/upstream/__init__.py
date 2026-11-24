"""The upstream surface: what the harness depends on, extracted rather than assumed.

An upstream release is a fact the harness must absorb, not a surprise it reacts
to. This package extracts the parts of PACT the harness actually depends on --
the hook registry, the tool vocabulary the gates classify by, every environment
variable they read, the model-facing artifacts and their hashes -- and can then
answer two questions: what changed, and does this tree still fit.
"""

from __future__ import annotations

from .inspect import Artifact, EnvRead, Surface, inspect_surface
from .verify import VerifyReport, verify_surface

__all__ = [
    "Artifact",
    "EnvRead",
    "Surface",
    "VerifyReport",
    "inspect_surface",
    "verify_surface",
]
