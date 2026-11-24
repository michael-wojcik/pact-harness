"""Encode and decode the harness contract documents.

Decoding is strict: an unknown field or version is an error, never something
to ignore. A directive the platform did not understand is a directive it did
not apply, and applying a gate verdict only partially is exactly the silent
under-enforcement the harness exists to prevent.

Documents are size-bounded. Upstream's hooks read stdin through an 8 MiB cap;
the same cap applies here so a hostile or runaway adapter cannot make decoding
itself a denial-of-service.
"""

from __future__ import annotations

import json
from typing import Any

from ..errors import ContractError
from .directives import EnvelopeV1
from .events import EventV1

#: The maximum accepted document size, matching upstream's stdin cap.
MAX_DOCUMENT_BYTES: int = 8 * 1024 * 1024


def _decode_json(text: str | bytes, path: str) -> Any:
    if isinstance(text, bytes):
        if len(text) > MAX_DOCUMENT_BYTES:
            raise ContractError(
                f"{path}: document exceeds {MAX_DOCUMENT_BYTES} bytes"
            )
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContractError(f"{path}: document is not valid UTF-8") from exc
    elif len(text.encode("utf-8", errors="replace")) > MAX_DOCUMENT_BYTES:
        raise ContractError(f"{path}: document exceeds {MAX_DOCUMENT_BYTES} bytes")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContractError(f"{path}: invalid JSON: {exc}") from exc


def event_to_dict(event: EventV1) -> dict[str, Any]:
    """Return the wire form of one event."""
    return event.to_dict()


def event_from_dict(value: Any) -> EventV1:
    """Decode one event from a JSON-shaped value."""
    return EventV1.from_dict(value)


def envelope_to_dict(envelope: EnvelopeV1) -> dict[str, Any]:
    """Return the wire form of one envelope."""
    return envelope.to_dict()


def envelope_from_dict(value: Any) -> EnvelopeV1:
    """Decode one envelope from a JSON-shaped value."""
    return EnvelopeV1.from_dict(value)


def loads_event(text: str | bytes) -> EventV1:
    """Decode one event from JSON text."""
    return event_from_dict(_decode_json(text, "event"))


def loads_envelope(text: str | bytes) -> EnvelopeV1:
    """Decode one envelope from JSON text."""
    return envelope_from_dict(_decode_json(text, "envelope"))


def dumps_event(event: EventV1) -> str:
    """Serialise one event as compact JSON."""
    return json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def dumps_envelope(envelope: EnvelopeV1) -> str:
    """Serialise one envelope as compact JSON."""
    return json.dumps(
        envelope.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
