"""Global registry utilities for tool specifications."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict


@dataclass
class ToolSpec:
    """Metadata describing a callable tool for the agent."""

    name: str
    description: str
    parameters: dict
    returns: str | None
    func: Callable[..., Awaitable[Any]]
    validate: Callable[[dict], dict] | None = None
    can_run_parallel: bool = True
    can_run_in_sequence: bool = False


# Internal registry storage used by both the agent and supporting utils.
_TOOL_REGISTRY: Dict[str, ToolSpec] = {}


def register_tool_spec(spec: ToolSpec) -> None:
    """Register (or replace) a tool specification."""

    _TOOL_REGISTRY[spec.name] = spec


def unregister_tool_spec(name: str) -> None:
    """Remove a tool specification if it exists."""

    _TOOL_REGISTRY.pop(name, None)


def clear_tool_registry() -> None:
    """Remove all registered tool specifications."""

    _TOOL_REGISTRY.clear()


def restore_tool_registry(snapshot: Dict[str, ToolSpec]) -> None:
    """Replace the registry contents with the provided snapshot."""

    clear_tool_registry()
    _TOOL_REGISTRY.update(snapshot)


def get_registered_tool_specs() -> Dict[str, ToolSpec]:
    """Return a shallow copy of the registered tool specifications."""

    return dict(_TOOL_REGISTRY)


def get_sequence_safe_tool_specs() -> Dict[str, ToolSpec]:
    """Return tools flagged as safe for run_sequence execution."""

    return {
        name: spec
        for name, spec in _TOOL_REGISTRY.items()
        if spec.can_run_in_sequence
    }
