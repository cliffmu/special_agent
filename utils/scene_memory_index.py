"""Vector index adapter for scene memory search and retrieval."""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, Any, List, TYPE_CHECKING

from . import logging as log
from . import scene_memory_store

if TYPE_CHECKING:
    pass  # For hass type hint
from .vector_index import (
    build_scene_index,
    load_scene_index,
    async_load_scene_index,
    query_vector_index,
    async_query_vector_index,
    DEFAULT_SCENE_PERSIST_DIR,
)
from . import performance

_LOGGER = logging.getLogger(__package__)


def _entry_to_doc(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a memory entry to a vector index document.
    
    Args:
        entry: Memory entry dict
        
    Returns:
        Document dict with page_content and metadata
    """
    # Build searchable text from intent, summary, and strategy
    intent = entry.get("intent", "")
    summary = entry.get("summary", "")
    strategy = entry.get("strategy", "")
    area = entry.get("area_hint", "")
    
    # Combine for embedding
    parts = [f"Intent: {intent}"]
    if area:
        parts.append(f"Area: {area}")
    if summary:
        parts.append(f"Summary: {summary}")
    if strategy:
        parts.append(f"Strategy: {strategy}")
    
    page_content = "\n".join(parts)
    
    # Store full entry in metadata for retrieval
    return {
        "page_content": page_content,
        "metadata": {
            "entry_id": entry.get("id"),
            "intent": intent,
            "area_hint": area,
            "summary": summary,
            "steps": entry.get("steps", []),
            "strategy": strategy,
            "confidence": entry.get("confidence", 0.0),
            "updated_at": entry.get("updated_at"),
        }
    }


def _rebuild_index(hass: Any | None = None) -> None:
    """
    Rebuild the entire scene index from store.
    
    Args:
        hass: Home Assistant instance (optional)
    """
    log.debug("Rebuilding scene index from store")
    
    # Get all entries from store
    all_entries = scene_memory_store.get_all()
    
    # Convert to docs
    docs = [_entry_to_doc(entry) for entry in all_entries]
    
    # Build index (full rebuild)
    with performance.track_operation(
        "scene_index_rebuild",
        metadata={"doc_count": len(docs)}
    ):
        build_scene_index(docs, force_rebuild=True)
    
    log.info("Scene index rebuilt with %d entries", len(docs))


async def _async_rebuild_index(hass: Any | None = None) -> None:
    """Async version of rebuild."""
    add_job = getattr(hass, "async_add_executor_job", None) if hass else None
    if callable(add_job) and add_job.__class__.__name__ != "MagicMock":
        await add_job(_rebuild_index, hass)
    else:
        await asyncio.to_thread(_rebuild_index, hass)


def upsert_scene(entry: Dict[str, Any], hass: Any | None = None) -> None:
    """
    Insert or update a scene memory entry and rebuild index.
    
    Args:
        entry: Memory entry dict with at least "id" field
        hass: Home Assistant instance (optional)
    """
    log.debug("Upserting scene: %s", entry.get("id"))
    
    # Update store
    scene_memory_store.upsert(entry)
    
    # Rebuild index
    _rebuild_index(hass)


async def async_upsert_scene(entry: Dict[str, Any], hass: Any | None = None) -> None:
    """Async version of upsert_scene."""
    log.debug("Async upserting scene: %s", entry.get("id"))
    
    # Update store
    add_job = getattr(hass, "async_add_executor_job", None) if hass else None
    if callable(add_job) and add_job.__class__.__name__ != "MagicMock":
        await add_job(scene_memory_store.upsert, entry)
    else:
        await asyncio.to_thread(scene_memory_store.upsert, entry)
    
    # Rebuild index
    await _async_rebuild_index(hass)


def search_scenes(
    intent_text: str,
    area: str | None = None,
    k: int = 1,
    hass: Any | None = None,
) -> List[Dict[str, Any]]:
    """
    Search for matching scene memories.
    
    Args:
        intent_text: Text to search for (e.g., "movie", "cozy")
        area: Optional area filter
        k: Number of results to return
        hass: Home Assistant instance (optional)
        
    Returns:
        List of memory entry dicts from metadata
    """
    log.debug("Searching scenes: query='%s', area=%s, k=%d", intent_text, area, k)
    
    # Load index
    index_data = load_scene_index()
    if index_data[0] is None:
        log.debug("No scene index found, returning empty results")
        return []
    
    # Build filters
    filters = {}
    if area:
        filters["area_hint"] = area
    
    # Query index
    results = query_vector_index(
        index_data,
        intent_text,
        k=k,
        filters=filters if filters else None,
        return_scores=True,
    )
    
    # Extract metadata (which contains full entry data)
    entries = []
    for doc, score in results:
        entry = doc.get("metadata", {})
        entry["search_score"] = score  # Add search score for debugging
        entries.append(entry)
    
    log.debug("Found %d matching scenes", len(entries))
    return entries


async def async_search_scenes(
    intent_text: str,
    area: str | None = None,
    k: int = 1,
    hass: Any | None = None,
) -> List[Dict[str, Any]]:
    """Async version of search_scenes."""
    log.debug("Async searching scenes: query='%s', area=%s, k=%d", intent_text, area, k)
    
    # Load index
    index_data = await async_load_scene_index(hass=hass)
    if index_data[0] is None:
        log.debug("No scene index found, returning empty results")
        return []
    
    # Build filters
    filters = {}
    if area:
        filters["area_hint"] = area
    
    # Query index
    results = await async_query_vector_index(
        index_data,
        intent_text,
        k=k,
        filters=filters if filters else None,
        return_scores=True,
        hass=hass,
    )
    
    # Extract metadata (which contains full entry data)
    entries = []
    for doc, score in results:
        entry = doc.get("metadata", {})
        entry["search_score"] = score  # Add search score for debugging
        entries.append(entry)
    
    log.debug("Found %d matching scenes", len(entries))
    return entries


def remove_scene(entry_id: str, hass: Any | None = None) -> bool:
    """
    Remove a scene memory entry and rebuild index.
    
    Args:
        entry_id: Entry ID to remove
        hass: Home Assistant instance (optional)
        
    Returns:
        True if removed, False if not found
    """
    log.debug("Removing scene: %s", entry_id)
    
    # Delete from store
    deleted = scene_memory_store.delete(entry_id)
    
    if deleted:
        # Rebuild index
        _rebuild_index(hass)
        log.info("Scene removed and index rebuilt: %s", entry_id)
    
    return deleted


async def async_remove_scene(entry_id: str, hass: Any | None = None) -> bool:
    """Async version of remove_scene."""
    log.debug("Async removing scene: %s", entry_id)
    
    # Delete from store
    add_job = getattr(hass, "async_add_executor_job", None) if hass else None
    if callable(add_job) and add_job.__class__.__name__ != "MagicMock":
        deleted = await add_job(scene_memory_store.delete, entry_id)
    else:
        deleted = await asyncio.to_thread(scene_memory_store.delete, entry_id)
    
    if deleted:
        # Rebuild index
        await _async_rebuild_index(hass)
        log.info("Scene removed and index rebuilt: %s", entry_id)
    
    return deleted


def rebuild_index(hass: Any | None = None) -> None:
    """
    Manually rebuild scene index from store.
    
    Useful for recovery or after bulk updates.
    
    Args:
        hass: Home Assistant instance (optional)
    """
    _rebuild_index(hass)


async def async_rebuild_index(hass: Any | None = None) -> None:
    """Async version of rebuild_index."""
    await _async_rebuild_index(hass)

