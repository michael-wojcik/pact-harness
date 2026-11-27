"""The upstream surface: what the harness depends on, extracted rather than assumed.

An upstream release is a fact the harness must absorb, not a surprise it reacts
to. This package extracts the parts of PACT the harness actually depends on --
the hook registry, the tool vocabulary the gates classify by, every environment
variable they read, the model-facing artifacts and their hashes -- and can then
answer two questions: what changed, and does this tree still fit.
"""

from __future__ import annotations

from .check import ReleaseCheck, check_release
from .diff import DiffReport, Difference, Level, diff_surfaces
from .inspect import Artifact, EnvRead, ModuleHash, Surface, inspect_surface
from .verify import VerifyReport, verify_surface

__all__ = [
    "Artifact",
    "DiffReport",
    "Difference",
    "EnvRead",
    "Level",
    "ModuleHash",
    "ReleaseCheck",
    "Surface",
    "check_release",
    "VerifyReport",
    "diff_surfaces",
    "inspect_surface",
    "verify_surface",
]