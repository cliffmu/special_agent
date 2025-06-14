"""Helpers to lazily create an AsyncOpenAI client."""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__package__)

try:  # AsyncOpenAI only in openai>=1.0
    from openai import AsyncOpenAI  # type: ignore
except Exception:  # pragma: no cover - openai optional
    AsyncOpenAI = None  # type: ignore

_CLIENT: Any | None = None


async def get_async_client(hass: Any | None = None):
    """Return a cached AsyncOpenAI client, creating it in executor if needed."""
    if AsyncOpenAI is None:  # pragma: no cover - openai optional
        raise RuntimeError("openai package not available")

    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    if hass is not None:
        _CLIENT = await hass.async_add_executor_job(AsyncOpenAI)
    else:  # used in unit tests without hass
        _CLIENT = AsyncOpenAI()
    return _CLIENT

