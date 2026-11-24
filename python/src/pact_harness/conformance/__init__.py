"""Conformance: run the vendored hooks and check the harness understood them.

The suite answers two questions per registration.

Did the harness deliver the frames the hooks expect? A Claude Code event's raw
frame is re-serialised by the adapter, so decoding that output must reproduce
the original document exactly.

Did the harness understand what came back? Every hook's stdout and exit code
must decode without a problem, and a refusal must produce a refusal directive.
An unrecognised output shape is a decoder gap, and a gap in a gate's output is
indistinguishable from the gate passing.
"""

from __future__ import annotations

from .corpus import CorpusFrame, corpus_frames
from .suite import CheckResult, ConformanceReport, run_conformance

__all__ = [
    "CheckResult",
    "ConformanceReport",
    "CorpusFrame",
    "corpus_frames",
    "run_conformance",
]
