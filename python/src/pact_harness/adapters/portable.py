"""The portable adapter: PACT on a platform with no interception at all.

This is the honest T0. The platform supplies no events and no way to veto a
tool call, so the harness claims nothing: the model asks the harness what PACT
thinks, through MCP tools, and decides what to do with the answer.

Frame synthesis is shared with the DSH adapter because the work is identical --
reconstruct the stdin the vendored hooks read from a normalised event -- and the
difference between the two is the capability claim, not the translation. Sharing
the code keeps a change to the frame shape from having to be made twice.
"""

from __future__ import annotations

from ..capabilities import CapabilitySet
from .dsh import DshAdapter


class PortableAdapter(DshAdapter):
    """A normalised-event adapter that claims no platform capability."""

    id = "mcp"

    #: Tier T0 by construction. Nothing here can observe or veto a tool call;
    #: the model may ask, and an advisory answer is all PACT can promise.
    capabilities = CapabilitySet()
