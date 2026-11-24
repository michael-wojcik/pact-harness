"""Platform adapters: the only platform-specific code in the harness."""

from __future__ import annotations

from .base import AdapterContext, LegacyFrame, PlatformAdapter
from .registry import NotImplementedAdapterError, available_adapters, detect_adapter, get_adapter

__all__ = [
    "AdapterContext",
    "LegacyFrame",
    "NotImplementedAdapterError",
    "PlatformAdapter",
    "available_adapters",
    "detect_adapter",
    "get_adapter",
]
