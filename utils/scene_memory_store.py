"""Persistent storage for scene memory entries."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import threading
from functools import wraps
from pathlib import Path
from typing import Dict, Any, Iterator

from . import logging as log
from .tool_registry import get_sequence_safe_tool_specs

_LOGGER = logging.getLogger(__package__)

# Storage location (persists across component updates)
BASE_DIR = Path(
    os.environ.get(
        "SPECIAL_AGENT_BASE_DIR",
        (
            "/homeassistant"
            if Path("/homeassistant").exists()
            else str(Path(__file__).resolve().parents[2])
        ),
    )
)
DEFAULT_PERSIST_DIR = os.environ.get(
    "SPECIAL_AGENT_PERSIST_DIR",
    str(Path(BASE_DIR) / "sa_vector_index"),
)
SCENE_MEMORY_FILE = os.path.join(DEFAULT_PERSIST_DIR, "scene_memory.json")

# Shared by scene mutations and index snapshot loads, which run in executor
# threads. A reentrant lock lets an upsert rebuild its index in one transaction.
_SCENE_LOCK = threading.RLock()


def scene_transaction(function):
    @wraps(function)
    def locked(*args, **kwargs):
        with _SCENE_LOCK:
            return function(*args, **kwargs)
    return locked


class SceneMemoryStore:
    """
    Stores scene memory entries with full data.
    
    Entry structure:
    {
        "id": str,              # Unique identifier
        "intent": str,          # Scene intent/name
        "area_hint": str,       # Optional area
        "summary": str,         # Short description
        "steps": list,          # Ordered steps for run_sequence
        "strategy": str,        # How/why strategy text
        "confidence": float,    # 0..1
        "updated_at": float,    # Unix timestamp
    }
    """
    
    def __init__(self, file_path: str = SCENE_MEMORY_FILE):
        self.file_path = file_path
        self._ensure_file()
    
    @scene_transaction
    def _ensure_file(self) -> None:
        """Ensure storage file exists."""
        if not os.path.exists(self.file_path):
            os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
            self._save({})
            log.debug("Created scene memory store at %s", self.file_path)
    
    def _load(self) -> Dict[str, Any]:
        """Load all entries from disk."""
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("entries", {})
        except Exception as err:
            log.error("Error loading scene memory store: %s", err, exc_info=True)
            return {}
    
    @scene_transaction
    def _save(self, entries: Dict[str, Any]) -> None:
        """Save all entries to disk."""
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=os.path.dirname(self.file_path),
                                             prefix=".scene-memory-", suffix=".tmp", delete=False) as f:
                temporary = f.name
                json.dump({"entries": entries}, f, indent=2)
            os.replace(temporary, self.file_path)
            temporary = None
            log.debug("Saved %d scene memory entries", len(entries))
        except Exception as err:
            log.error("Error saving scene memory store: %s", err, exc_info=True)
            raise
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
    
    def get(self, entry_id: str) -> Dict[str, Any] | None:
        """
        Get a single entry by ID.
        
        Args:
            entry_id: Unique entry identifier
            
        Returns:
            Entry dict or None if not found
        """
        entries = self._load()
        return entries.get(entry_id)
    
    @scene_transaction
    def upsert(self, entry: Dict[str, Any]) -> None:
        """
        Insert or update an entry.
        
        Args:
            entry: Entry dict with at least "id" field
        """
        if "id" not in entry:
            log.error("Cannot upsert entry without 'id' field")
            return
        
        entry_id = entry["id"]
        entries = self._load()
        
        if entry_id in entries:
            log.debug("Updating scene memory entry: %s", entry_id)
        else:
            log.debug("Creating scene memory entry: %s", entry_id)
        
        entries[entry_id] = entry
        self._save(entries)
    
    @scene_transaction
    def delete(self, entry_id: str) -> bool:
        """
        Delete an entry by ID.
        
        Args:
            entry_id: Unique entry identifier
            
        Returns:
            True if deleted, False if not found
        """
        entries = self._load()
        if entry_id in entries:
            del entries[entry_id]
            self._save(entries)
            log.debug("Deleted scene memory entry: %s", entry_id)
            return True
        return False
    
    def iter_all(self) -> Iterator[Dict[str, Any]]:
        """
        Iterate over all entries.
        
        Yields:
            Entry dicts
        """
        entries = self._load()
        for entry in entries.values():
            yield entry
    
    def get_all(self) -> list[Dict[str, Any]]:
        """
        Get all entries as a list.
        
        Returns:
            List of entry dicts
        """
        return list(self.iter_all())
    
    def count(self) -> int:
        """
        Count total entries.
        
        Returns:
            Number of entries
        """
        entries = self._load()
        return len(entries)


# Singleton instance
_store_instance: SceneMemoryStore | None = None


def get_store() -> SceneMemoryStore:
    """Get singleton store instance."""
    global _store_instance
    if _store_instance is None:
        with _SCENE_LOCK:
            if _store_instance is None:
                _store_instance = SceneMemoryStore()
    return _store_instance


# Convenience functions that use singleton
def get(entry_id: str) -> Dict[str, Any] | None:
    """Get entry by ID."""
    return get_store().get(entry_id)


async def async_get(entry_id: str, hass: Any | None = None) -> Dict[str, Any] | None:
    """Read a scene without blocking HA, including lazy store construction."""
    if hass is not None:
        return await hass.async_add_executor_job(get, entry_id)
    return await asyncio.to_thread(get, entry_id)


def upsert(entry: Dict[str, Any]) -> None:
    """Insert or update entry."""
    get_store().upsert(entry)


def delete(entry_id: str) -> bool:
    """Delete entry by ID."""
    return get_store().delete(entry_id)


def iter_all() -> Iterator[Dict[str, Any]]:
    """Iterate over all entries."""
    return get_store().iter_all()


def get_all() -> list[Dict[str, Any]]:
    """Get all entries as list."""
    return get_store().get_all()


def count() -> int:
    """Count total entries."""
    return get_store().count()


@scene_transaction
def clear_all() -> int:
    """
    Delete all scene memory entries.
    
    Returns:
        Number of entries deleted
    """
    store = get_store()
    entries = store._load()
    count = len(entries)
    store._save({})
    log.info("Cleared all %d scene memory entries", count)
    return count


def import_template(template: Dict[str, Any]) -> None:
    """
    Import a template scene entry.
    
    Args:
        template: Scene entry dict with proper format
    """
    if "id" not in template:
        log.error("Template must have 'id' field")
        return
    
    get_store().upsert(template)
    log.info("Imported template scene: %s", template["id"])


def should_update_scene(existing_entry: dict | None, new_steps: list, outcome: str) -> tuple[bool, str]:
    """
    Determine if scene should be updated based on comparison with existing.
    
    Args:
        existing_entry: Existing scene entry or None
        new_steps: New steps being saved
        outcome: Execution outcome ('success', 'fail', 'corrected')
        
    Returns:
        Tuple of (should_update: bool, reason: str)
    """
    if not existing_entry:
        return True, "created"
    
    # Don't overwrite working scene with failed attempt
    if outcome == "fail":
        return False, "skipped_failed_execution"
    
    existing_steps = existing_entry.get("steps", [])
    
    # Compare steps (ignore metadata)
    def steps_equal(s1, s2):
        """Check if two step lists are functionally identical."""
        if len(s1) != len(s2):
            return False
        for a, b in zip(s1, s2):
            if a.get("type") != b.get("type"):
                return False
            if a.get("service") != b.get("service"):
                return False
            if a.get("data") != b.get("data"):
                return False
            if a.get("seconds") != b.get("seconds"):
                return False
            if a.get("post_condition") != b.get("post_condition"):
                return False
        return True
    
    if steps_equal(existing_steps, new_steps):
        # Steps identical - only update if correction flag
        if outcome == "corrected":
            return True, "corrected_after_failure"
        return False, "unchanged"
    
    # Steps changed and succeeded - check if optimization
    total_delays_old = sum(s.get("seconds", 0) for s in existing_steps if s.get("type") == "delay")
    total_delays_new = sum(s.get("seconds", 0) for s in new_steps if s.get("type") == "delay")
    
    if total_delays_new < total_delays_old:
        return True, f"optimized_from_{total_delays_old}s_to_{total_delays_new}s"
    
    return True, "updated_workflow"


def normalize_and_validate_steps(steps: list) -> tuple[list, list]:
    """
    Normalize steps to proper run_sequence format and validate.
    
    Args:
        steps: Raw steps from agent (may be malformed)
        
    Returns:
        Tuple of (normalized_steps, validation_errors)
    """
    normalized_steps = []
    validation_errors = []

    sequence_tools = get_sequence_safe_tool_specs()

    for idx, s in enumerate(steps):
        if isinstance(s, str):
            validation_errors.append(f"Step {idx}: String steps not supported")
            continue
        elif not isinstance(s, dict):
            validation_errors.append(f"Step {idx}: Invalid type {type(s)}")
            continue
        
        normalized = {}
        
        # Delay step
        if "wait_seconds" in s or s.get("type") == "wait":
            normalized["type"] = "delay"
            normalized["seconds"] = s.get("wait_seconds") or s.get("seconds", 0)
            # Preserve guards
            if "only_if_state" in s:
                normalized["only_if_state"] = s["only_if_state"]
            if "only_if" in s:
                normalized["only_if"] = s["only_if"]
            
        # Service call step
        elif "service" in s:
            normalized["type"] = "service_call"
            normalized["service"] = s["service"]
            
            # Build data field
            if "data" in s and isinstance(s["data"], dict):
                data = s["data"].copy()
            elif "entity_id" in s:
                data = {"entity_id": s["entity_id"]}
                if "source" in s:
                    data["source"] = s["source"]
                if "params" in s and isinstance(s["params"], dict):
                    data.update(s["params"])
                elif "params" in s and isinstance(s["params"], str):
                    data["source"] = s["params"]
            elif "entity" in s and "entity_id" not in s:
                validation_errors.append(
                    f"Step {idx}: Has friendly name '{s['entity']}' instead of entity_id"
                )
                continue
            else:
                data = {}
            
            # Validate entity_id for services that need it
            if "entity_id" not in data and s["service"] not in ["scene.turn_on", "script.turn_on"]:
                validation_errors.append(f"Step {idx}: Service '{s['service']}' missing entity_id")
                continue
            
            normalized["data"] = data
            
            # Preserve guards
            if "only_if_state" in s:
                normalized["only_if_state"] = s["only_if_state"]
            if "only_if" in s:
                normalized["only_if"] = s["only_if"]
            
        # Already proper format
        elif s.get("type") == "delay":
            normalized = s
        elif s.get("type") == "service_call":
            if "data" not in s or not isinstance(s["data"], dict):
                validation_errors.append(f"Step {idx}: service_call missing data dict")
                continue
            if "entity_id" not in s["data"] and s.get("service", "").split(".")[0] not in ["scene", "script"]:
                validation_errors.append(f"Step {idx}: service_call missing entity_id")
                continue
            # Preserve guards (only_if_state, only_if)
            normalized = s.copy()
            # Validate guard if present
            if "only_if_state" in normalized:
                guard = normalized["only_if_state"]
                if not isinstance(guard, dict) or "entity_id" not in guard:
                    validation_errors.append(f"Step {idx}: only_if_state guard missing entity_id")
                    del normalized["only_if_state"]
        elif s.get("type") == "tool_call" or ("tool" in s and s.get("type") is None):
            tool_name = s.get("tool")
            if not isinstance(tool_name, str) or not tool_name:
                validation_errors.append(f"Step {idx}: tool_call missing 'tool' name")
                continue

            if tool_name not in sequence_tools:
                validation_errors.append(
                    f"Step {idx}: tool '{tool_name}' is not allowed in run_sequence scenes"
                )
                continue

            normalized["type"] = "tool_call"
            normalized["tool"] = tool_name

            args = s.get("args", {})
            if args is None:
                args = {}
            if not isinstance(args, dict):
                validation_errors.append(f"Step {idx}: tool_call 'args' must be an object")
                continue
            normalized["args"] = args

            result_var = s.get("result_var")
            if result_var is not None:
                if isinstance(result_var, str) and result_var.strip():
                    normalized["result_var"] = result_var.strip()
                else:
                    validation_errors.append(f"Step {idx}: result_var must be a non-empty string")

            if "result_path" in s:
                result_path = s.get("result_path")
                if isinstance(result_path, str) and result_path.strip():
                    if "result_var" not in normalized:
                        validation_errors.append(
                            f"Step {idx}: result_path provided but result_var missing"
                        )
                    normalized["result_path"] = result_path.strip()
                else:
                    validation_errors.append(f"Step {idx}: result_path must be a non-empty string if provided")

            if "expect" in s:
                expect = s["expect"]
                if isinstance(expect, dict):
                    normalized["expect"] = expect
                else:
                    validation_errors.append(f"Step {idx}: expect must be an object if provided")
                    continue

            if "only_if" in s:
                normalized["only_if"] = s["only_if"]
            if "only_if_state" in s:
                normalized["only_if_state"] = s["only_if_state"]
        # Reject invalid types with helpful messages
        elif s.get("type") == "wait_state":
            validation_errors.append(f"Step {idx}: wait_state not allowed - use delay instead")
            continue
        else:
            validation_errors.append(
                f"Step {idx}: Unknown step type '{s.get('type', 'missing')}' - use service_call, delay, or tool_call"
            )
            continue
        
        if normalized.get("type") == "service_call" and "post_condition" in s:
            post_condition = s["post_condition"]
            if not isinstance(post_condition, dict) or not post_condition:
                validation_errors.append(f"Step {idx}: post_condition must be a non-empty object")
                continue
            normalized["post_condition"] = post_condition.copy()

        normalized_steps.append(normalized)
    
    return normalized_steps, validation_errors
