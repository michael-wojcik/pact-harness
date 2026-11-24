"""Shared validation helpers for the harness contract.

The contract is validated by hand rather than with a schema library: the hook
path runs in a fresh process per platform event, so every import on that path
is a latency cost, and the harness ships with no runtime dependencies.

Messages name the offending path (for example `tool.input.command`) so an
adapter author can find the field without reading this module.
"""

from __future__ import annotations

import datetime as _datetime
from typing import Any, Iterable, Mapping

from ..errors import ContractError


def fail(path: str, message: str) -> "ContractError":
    """Build a ContractError naming one contract path."""
    return ContractError(f"{path}: {message}")


def require_str(value: Any, path: str, *, allow_empty: bool = False) -> str:
    """Return value as a string, or raise when it is not an acceptable one."""
    if not isinstance(value, str):
        raise fail(path, f"expected a string, got {type(value).__name__}")
    if not allow_empty and value == "":
        raise fail(path, "expected a non-empty string")
    return value


def optional_str(value: Any, path: str) -> str | None:
    """Return None for absent values, otherwise a validated non-empty string."""
    if value is None:
        return None
    return require_str(value, path)


def require_int(value: Any, path: str, *, minimum: int | None = None) -> int:
    """Return value as an int (bool is rejected, since bool is an int subclass)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise fail(path, f"expected an integer, got {type(value).__name__}")
    if minimum is not None and value < minimum:
        raise fail(path, f"expected an integer >= {minimum}, got {value}")
    return value


def optional_int(value: Any, path: str, *, minimum: int | None = None) -> int | None:
    """Return None for absent values, otherwise a validated integer."""
    if value is None:
        return None
    return require_int(value, path, minimum=minimum)


def require_bool(value: Any, path: str) -> bool:
    """Return value as a bool."""
    if not isinstance(value, bool):
        raise fail(path, f"expected a boolean, got {type(value).__name__}")
    return value


def require_object(value: Any, path: str) -> dict[str, Any]:
    """Return value as a plain JSON object."""
    if not isinstance(value, Mapping):
        raise fail(path, f"expected an object, got {type(value).__name__}")
    out: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise fail(path, "object keys must be strings")
        out[key] = item
    return out


def optional_object(value: Any, path: str) -> dict[str, Any] | None:
    """Return None for absent values, otherwise a validated JSON object."""
    if value is None:
        return None
    return require_object(value, path)


def reject_unknown(value: Mapping[str, Any], allowed: Iterable[str], path: str) -> None:
    """Raise when an object carries a key the contract does not define."""
    allowed_set = set(allowed)
    unknown = sorted(set(value) - allowed_set)
    if unknown:
        joined = ", ".join(repr(name) for name in unknown)
        raise fail(path, f"unknown field(s) {joined}; allowed: {sorted(allowed_set)}")


def parse_timestamp(value: Any, path: str) -> str:
    """Validate an RFC 3339 timestamp and return it unchanged.

    A naive timestamp is rejected: the harness compares instants across
    platforms and a missing offset makes that comparison undefined.
    """
    text = require_str(value, path)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = _datetime.datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise fail(path, f"expected an RFC 3339 timestamp, got {text!r}") from exc
    if parsed.tzinfo is None:
        raise fail(path, f"timestamp {text!r} has no UTC offset")
    return text


def require_enum(value: Any, allowed: frozenset[str], path: str) -> str:
    """Return value when it is a member of allowed."""
    text = require_str(value, path)
    if text not in allowed:
        raise fail(path, f"{text!r} is not one of {sorted(allowed)}")
    return text
