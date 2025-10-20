"""Global registry utilities for tool specifications."""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List
from unittest.mock import MagicMock

try:
    import voluptuous as vol
except ImportError:
    vol = None  # type: ignore

try:
    from . import logging as log
except ImportError:
    from utils import logging as log


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


def schema_to_json(schema: Any) -> Dict[str, Any]:
    """Convert voluptuous schemas and simple Python types to JSON Schema."""

    try:
        from voluptuous.schema_builder import Optional, Required, Schema, UNDEFINED
    except Exception:
        Optional = getattr(vol, "Optional", None) if vol else None
        Required = getattr(vol, "Required", None) if vol else None
        Schema = getattr(vol, "Schema", None) if vol else None
        UNDEFINED = getattr(vol, "UNDEFINED", None) if vol else None

    if isinstance(schema, MagicMock):
        stored = getattr(schema, "_sa_schema_args", None)
        if stored is None:
            call_args_list = getattr(getattr(vol, "Schema", None), "call_args_list", []) if vol else []
            if call_args_list:
                try:
                    stored = call_args_list.pop(0)[0][0]
                except Exception:  # pragma: no cover - defensive
                    stored = None
                if stored is not None:
                    setattr(schema, "_sa_schema_args", stored)
        if stored is None:
            return {"type": "object", "properties": {}}
        return schema_to_json(stored)

    if Schema and isinstance(Schema, type) and isinstance(schema, Schema):
        return schema_to_json(schema.schema)

    if isinstance(schema, dict):
        # If dict already has "type" key, it's already JSON Schema - return as-is
        if "type" in schema:
            # Also check it's not being used as a voluptuous-style key wrapper
            # (voluptuous uses Required/Optional which would make non-string keys)
            has_nonstring_keys = any(not isinstance(k, str) for k in schema.keys())
            if not has_nonstring_keys:
                return schema
        
        # Otherwise, treat as voluptuous schema dict
        properties: Dict[str, Any] = {}
        required_fields: List[str] = []
        key_wrappers: List[type] = []
        if Required and isinstance(Required, type):
            key_wrappers.append(Required)
        if Optional and isinstance(Optional, type):
            key_wrappers.append(Optional)
        for key, value in schema.items():
            default = None
            key_name = key
            if key_wrappers and isinstance(key, tuple(key_wrappers)):
                key_name = key.schema
                if Required and isinstance(key, Required):
                    required_fields.append(key_name)
                default = key.default
                if callable(default):
                    try:
                        default = default()
                    except Exception:  # pragma: no cover - defensive
                        default = None
                if UNDEFINED and default is UNDEFINED:
                    default = None
            properties[key_name] = schema_to_json(value)
            if default is not None:
                properties[key_name]["default"] = default

        result: Dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required_fields:
            result["required"] = required_fields
        return result

    if isinstance(schema, list):
        item_schema = schema_to_json(schema[0]) if schema else {"type": "string"}
        return {"type": "array", "items": item_schema}

    if schema in (str, "string"):
        return {"type": "string"}
    if schema in (int, "integer"):
        return {"type": "integer"}
    if schema in (float, "number"):
        return {"type": "number"}
    if schema in (bool, "boolean"):
        return {"type": "boolean"}
    if schema in (dict, "object"):
        return {"type": "object"}

    return {"type": "string"}


def spec_to_json(spec: ToolSpec) -> Dict[str, Any]:
    """Convert ToolSpec to OpenAI Responses/Chat API format."""
    params = spec.parameters
    if isinstance(params, (dict, list)) or hasattr(params, "schema"):
        params_json = schema_to_json(params)
    else:
        params_json = params  # Fall back to provided structure

    result = {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": params_json,
        },
    }
    
    # Validate the output has required structure
    if "function" not in result or "name" not in result.get("function", {}):
        log.error(f"Tool {spec.name} generated invalid JSON: {result}")
    
    return result


def get_tool_module_names(config: Dict = None) -> List[str]:
    """Return list of tool module names to load based on config.
    
    Args:
        config: Optional configuration dict with keys:
            - require_confirmation: bool (default True)
            - scene_memory_enabled: bool (default False)
    
    Returns:
        List of tool module names to load
    """
    config = config or {}
    
    # Core tools (always loaded)
    base_tools = [
        "tool_specs.build_device_index",
        "tool_specs.ask_user",
        "tool_specs.search_devices",
        "tool_specs.control_device",
        "tool_specs.search_plex",
        "tool_specs.play_plex_media",
        "tool_specs.search_spotify",
        "tool_specs.get_entity_state",
        "tool_specs.get_entity_history",
        "tool_specs.prepare_voice_response",
        "tool_specs.run_sequence",
    ]
    
    # Conditional: confirmation tool
    if config.get("require_confirmation", True):
        base_tools.insert(1, "tool_specs.confirm_action")
        log.info("Confirmation enabled - confirm_action tool will be loaded")
    else:
        log.info("Confirmation disabled - confirm_action tool not loaded")
    
    # Conditional: scene memory tools
    if config.get("scene_memory_enabled", False):
        base_tools.extend([
            "tool_specs.get_scene",
            "tool_specs.set_scene",
        ])
        log.info("Scene memory enabled - scene tools will be loaded")
    else:
        log.debug("Scene memory disabled - scene tools not loaded")
    
    return base_tools


async def load_all_tools(hass: Any = None, config: Dict = None) -> Dict[str, ToolSpec]:
    """Dynamically load all tool specs and return as dict.
    
    Args:
        hass: Home Assistant instance (for async imports)
        config: Optional configuration dict for conditional loading
        
    Returns:
        Dict mapping tool names to ToolSpec objects
    """
    tools = {}
    # Get parent package (e.g., "custom_components.special_agent" from "custom_components.special_agent.utils")
    base_package = '.'.join(__package__.split('.')[:-1]) if __package__ else ""
    
    for mod_name in get_tool_module_names(config):
        # Build full module path
        if base_package:
            module_name = f"{base_package}.{mod_name}"
        else:
            module_name = mod_name
            
        try:
            # Use async import if hass available, otherwise sync
            if hass:
                module = await hass.async_add_executor_job(
                    importlib.import_module, module_name
                )
            else:
                module = importlib.import_module(module_name)
            
            # Extract SPEC and register
            spec = module.SPEC
            tools[spec.name] = spec
            register_tool_spec(spec)
            log.debug(f"Loaded tool: {spec.name}")
            
        except Exception as err:
            log.debug(f"Tool '{module_name}' not loaded: {err}")
    
    log.info(f"Loaded {len(tools)} tools successfully")
    return tools
