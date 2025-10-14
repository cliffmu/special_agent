"""Persistent storage for scene memory entries."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Iterator

from . import logging as log

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
    
    def _ensure_file(self) -> None:
        """Ensure storage file exists."""
        if not os.path.exists(self.file_path):
            os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump({"entries": {}}, f)
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
    
    def _save(self, entries: Dict[str, Any]) -> None:
        """Save all entries to disk."""
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump({"entries": entries}, f, indent=2)
            log.debug("Saved %d scene memory entries", len(entries))
        except Exception as err:
            log.error("Error saving scene memory store: %s", err, exc_info=True)
    
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
        _store_instance = SceneMemoryStore()
    return _store_instance


# Convenience functions that use singleton
def get(entry_id: str) -> Dict[str, Any] | None:
    """Get entry by ID."""
    return get_store().get(entry_id)


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
            normalized = s
        else:
            validation_errors.append(f"Step {idx}: Unknown format")
            continue
        
        normalized_steps.append(normalized)
    
    return normalized_steps, validation_errors

