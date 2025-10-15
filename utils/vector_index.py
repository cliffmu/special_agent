"""Simple NumPy-based vector index for Home Assistant devices."""

from __future__ import annotations

import logging
import asyncio
import json
import os
import hashlib
from pathlib import Path
from typing import Iterable, Tuple, List, Dict, Any
from collections import defaultdict

import numpy as np

from . import logging as log
from .constants import (
    EXCLUDED_DOMAINS,
    EXCLUDED_SUFFIXES,
    INCLUDED_ENTITY_IDS,
    EMBED_MODEL,
    EMBED_DIM,
    FALLBACK_MODEL,
    BOOST_DOMAIN,
    BOOST_AREA,
    BOOST_OVERLAP,
)

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
DEFAULT_DEVICE_PERSIST_DIR = os.path.join(DEFAULT_PERSIST_DIR, "devices")
DEFAULT_SCENE_PERSIST_DIR = os.path.join(DEFAULT_PERSIST_DIR, "scenes")

_LOGGER = logging.getLogger(__package__)


DIMENSION = EMBED_DIM


_EMBED_CACHE_FILE = os.environ.get(
    "SPECIAL_AGENT_EMBED_CACHE", str(Path(DEFAULT_PERSIST_DIR) / "embed_cache.json")
)
_EMBED_CACHE: Dict[str, list] | None = None


def _load_cache() -> Dict[str, list]:
    """Load embedding cache from disk lazily."""
    global _EMBED_CACHE
    if _EMBED_CACHE is None:
        if os.path.exists(_EMBED_CACHE_FILE):
            try:
                with open(_EMBED_CACHE_FILE, "r", encoding="utf-8") as f:
                    _EMBED_CACHE = json.load(f)
            except Exception:  # pragma: no cover - cache failures not fatal
                _EMBED_CACHE = {}
        else:
            _EMBED_CACHE = {}
    return _EMBED_CACHE


def _save_cache() -> None:  # pragma: no cover - trivial
    """Persist embedding cache to disk."""
    if _EMBED_CACHE is None:
        return
    try:
        os.makedirs(os.path.dirname(_EMBED_CACHE_FILE), exist_ok=True)
        with open(_EMBED_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_EMBED_CACHE, f)
    except Exception:  # pragma: no cover - cache failures not fatal
        pass


def _hash_embed(text: str) -> np.ndarray:
    vec = np.zeros(DIMENSION, dtype=np.float32)
    for word in text.split():
        idx = hash(word) % DIMENSION
        vec[idx] += 1.0
    return vec


def _semantic_embed(text: str) -> np.ndarray:
    """Return a semantic embedding for text with caching and fallbacks."""
    cache = _load_cache()
    key = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if key in cache:
        log.debug("embedding cache hit %s", key[:8])
        return np.array(cache[key], dtype=np.float32)
    log.debug("embedding cache miss %s", key[:8])

    if os.environ.get("SPECIAL_AGENT_TESTING") == "true":
        vec = _hash_embed(text)
    else:
        try:  # pragma: no cover - requires openai package
            from openai import OpenAI, OpenAIError  # type: ignore

            client = OpenAI()
            resp = client.embeddings.create(model=EMBED_MODEL, input=[text])
            vec = np.array(resp.data[0].embedding, dtype=np.float32)
        except Exception as err:  # pragma: no cover - network failures
            log.debug("OpenAI embed failed: %s", err)
            try:
                from sentence_transformers import SentenceTransformer  # type: ignore

                model = SentenceTransformer(FALLBACK_MODEL)
                vec = model.encode(text)
                if vec.shape[0] < DIMENSION:
                    vec = np.pad(vec, (0, DIMENSION - vec.shape[0]))
                vec = vec.astype(np.float32)
            except Exception as err2:  # pragma: no cover - no fallback model
                log.debug("SentenceTransformer embed failed: %s", err2)
                vec = _hash_embed(text)

    vec = vec / (np.linalg.norm(vec) + 1e-9)
    cache[key] = vec.tolist()
    _save_cache()
    return vec


def build_device_index(
    states: Iterable[Dict],
    persist_dir: str = DEFAULT_DEVICE_PERSIST_DIR,
    force_rebuild: bool = False,
) -> Tuple[np.ndarray, List[Dict]]:
    """Build or load a NumPy index from Home Assistant device/entity states."""
    states = list(states)
    log.debug(
        "build_device_index: dir=%s force_rebuild=%s states=%d",
        persist_dir,
        force_rebuild,
        len(states),
    )
    os.makedirs(persist_dir, exist_ok=True)
    index_file = os.path.join(persist_dir, "matrix.npy")
    mapping_file = os.path.join(persist_dir, "mapping.json")
    meta_file = os.path.join(persist_dir, "meta.json")

    if (
        not force_rebuild
        and os.path.exists(index_file)
        and os.path.exists(mapping_file)
        and os.path.exists(meta_file)
    ):
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("embedding_model") == EMBED_MODEL:
                log.debug("Loading existing index from %s", persist_dir)
                matrix = np.load(index_file)
                with open(mapping_file, "r", encoding="utf-8") as f2:
                    mapping = json.load(f2)
                log.debug(
                    "Loaded index shape=%s docs=%d",
                    matrix.shape,
                    len(mapping),
                )
                return matrix, mapping
            log.debug("Embedding model changed; rebuilding index")
        except Exception as err:
            log.debug("Failed loading existing index: %s", err, exc_info=True)
            # fallthrough to rebuild

    docs = []
    vectors = []
    excluded_count = 0
    area_summary: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    platform_summary: Dict[str, int] = defaultdict(int)
    platforms_by_area: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for st in states:
        entity_id = st.get("entity_id", "")
        domain = st.get("domain") or entity_id.split(".")[0]
        excluded_domain = domain in EXCLUDED_DOMAINS
        excluded_suffix = entity_id.endswith(EXCLUDED_SUFFIXES)
        if entity_id not in INCLUDED_ENTITY_IDS and (excluded_domain or excluded_suffix):
            excluded_count += 1
            continue

        text = (
            f"Entity: {entity_id}\n"
            f"Name: {st.get('name')}\n"
            f"Attributes: {st.get('attributes')}"
        )
        vec = _semantic_embed(text)
        meta = {
            "entity_id": entity_id,
            "domain": domain,
            "area_id": st.get("area_id") or st.get("attributes", {}).get("area_id"),
            "friendly_name": st.get("attributes", {}).get("friendly_name"),
            "platform": st.get("platform"),  # Integration that provides this entity
        }
        docs.append({"page_content": text, "metadata": meta})
        vectors.append(vec)
        area = meta.get("area_id") or "Unassigned"
        area_summary[str(area)][domain] += 1
        
        # Track platforms
        platform = meta.get("platform")
        if platform:
            platform_summary[platform] += 1
            platforms_by_area[str(area)][platform] += 1

    if not vectors:
        log.debug("No vectors generated; raising error")
        raise ValueError("No states provided to build vector index")

    matrix = np.vstack(vectors).astype("float32")
    log.debug("Saving index matrix to %s", index_file)
    np.save(index_file, matrix)
    log.debug("Writing mapping to %s", mapping_file)
    with open(mapping_file, "w", encoding="utf-8") as f:
        json.dump(docs, f, indent=2)
    meta_file = os.path.join(persist_dir, "meta.json")
    log.debug("Writing meta to %s", meta_file)
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "excluded_count": excluded_count,
                "embedding_model": EMBED_MODEL,
                "area_summary": {a: dict(d) for a, d in area_summary.items()},
                "platform_summary": dict(platform_summary),
                "platforms_by_area": {a: dict(p) for a, p in platforms_by_area.items()},
            },
            f,
        )

    log.info(
        "Device index rebuilt with %d docs (excluded=%d)", len(docs), excluded_count
    )
    log.debug("build_device_index: completed")
    return matrix, docs


def load_device_index(
    persist_dir: str = DEFAULT_DEVICE_PERSIST_DIR,
) -> Tuple[np.ndarray, List[Dict]] | Tuple[None, None]:
    """Load a previously built device/entity index if available."""
    index_file = os.path.join(persist_dir, "matrix.npy")
    mapping_file = os.path.join(persist_dir, "mapping.json")
    log.debug("load_device_index from %s", persist_dir)
    if os.path.exists(index_file) and os.path.exists(mapping_file):
        try:
            matrix = np.load(index_file)
            with open(mapping_file, "r", encoding="utf-8") as f:
                mapping = json.load(f)
            log.debug("Loaded index shape=%s docs=%d", matrix.shape, len(mapping))
            return matrix, mapping
        except Exception as err:
            log.debug("Error loading vector index: %s", err, exc_info=True)
            return None, None
    log.debug("No vector index found in %s", persist_dir)
    return None, None


async def async_load_device_index(
    persist_dir: str = DEFAULT_DEVICE_PERSIST_DIR,
    hass: Any | None = None,
) -> Tuple[np.ndarray, List[Dict]] | Tuple[None, None]:
    """Asynchronously load a previously built device/entity index if available."""
    index_file = os.path.join(persist_dir, "matrix.npy")
    mapping_file = os.path.join(persist_dir, "mapping.json")
    log.debug("async_load_device_index from %s", persist_dir)
    if os.path.exists(index_file) and os.path.exists(mapping_file):
        try:
            add_job = getattr(hass, "async_add_executor_job", None) if hass else None
            if callable(add_job) and add_job.__class__.__name__ != "MagicMock":
                matrix = await add_job(np.load, index_file)

                def _load_json(path: str) -> Any:
                    with open(path, "r", encoding="utf-8") as f:
                        return json.load(f)

                mapping = await add_job(_load_json, mapping_file)
            else:
                matrix = await asyncio.to_thread(np.load, index_file)

                def _load_json() -> Any:
                    with open(mapping_file, "r", encoding="utf-8") as f:
                        return json.load(f)

                mapping = await asyncio.to_thread(_load_json)
            log.debug(
                "Loaded index shape=%s docs=%d", matrix.shape, len(mapping)
            )
            return matrix, mapping
        except Exception as err:
            log.debug("Error loading vector index: %s", err, exc_info=True)
            return None, None
    log.debug("No vector index found in %s", persist_dir)
    return None, None


def load_vector_meta(
    persist_dir: str = DEFAULT_PERSIST_DIR,
) -> Dict | None:
    """Load ``meta.json`` for the vector index if available."""
    meta_file = os.path.join(persist_dir, "meta.json")
    log.debug("load_vector_meta from %s", persist_dir)
    if os.path.exists(meta_file):
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as err:  # pragma: no cover - optional
            log.debug("Error loading vector meta: %s", err, exc_info=True)
            return None
    log.debug("No vector meta found in %s", persist_dir)
    return None


async def async_load_vector_meta(
    persist_dir: str = DEFAULT_PERSIST_DIR,
    hass: Any | None = None,
) -> Dict | None:
    """Asynchronously read ``meta.json`` if present."""
    meta_file = os.path.join(persist_dir, "meta.json")
    log.debug("async_load_vector_meta from %s", persist_dir)
    if os.path.exists(meta_file):
        try:
            add_job = getattr(hass, "async_add_executor_job", None) if hass else None
            if callable(add_job) and add_job.__class__.__name__ != "MagicMock":
                def _load(path: str) -> Any:
                    with open(path, "r", encoding="utf-8") as f:
                        return json.load(f)

                return await add_job(_load, meta_file)
            else:
                def _load() -> Any:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        return json.load(f)

                return await asyncio.to_thread(_load)
        except Exception as err:  # pragma: no cover - optional
            log.debug("Error loading vector meta: %s", err, exc_info=True)
            return None
    log.debug("No vector meta found in %s", persist_dir)
    return None


def query_vector_index(
    index_data: Tuple[np.ndarray, List[Dict]],
    query: str,
    k: int = 5,
    filters: Dict[str, Any] | None = None,
    return_scores: bool = False,
) -> List[Dict] | List[Tuple[Dict, float]]:
    """Query the index and return matching docs using cosine similarity.

    If ``filters`` is provided, only documents whose metadata match every
    key/value pair are considered. Values may be single items or lists.
    """
    if not index_data or index_data[0] is None:
        log.debug("query_vector_index: no index data")
        return []
    matrix, docs = index_data
    if filters:
        keep_indices = []
        for idx, doc in enumerate(docs):
            meta = doc.get("metadata", {})
            match = True
            for key, value in filters.items():
                val = meta.get(key)
                if isinstance(value, (list, tuple, set)):
                    if val not in value:
                        match = False
                        break
                else:
                    if val != value:
                        match = False
                        break
            if match:
                keep_indices.append(idx)
        if not keep_indices:
            return []
        matrix = matrix[keep_indices]
        docs = [docs[i] for i in keep_indices]
    log.debug("Vector search query '%s' k=%s", query, k)
    vec = _semantic_embed(query)
    matrix_norm = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9)
    vec_norm = vec / (np.linalg.norm(vec) + 1e-9)
    scores = matrix_norm @ vec_norm
    top_indices = scores.argsort()[::-1][:k]
    base_hits = [(docs[i], float(scores[i])) for i in top_indices]

    reranked = []
    query_tokens = query.lower().split()
    for doc, cos in base_hits:
        meta = doc.get("metadata", {})
        fn_tokens = str(meta.get("friendly_name", "")).lower().split()
        overlap = 0.0
        if query_tokens:
            overlap = len(set(query_tokens) & set(fn_tokens)) / len(query_tokens)
        area_match = (
            1
            if meta.get("area_id")
            and str(meta.get("area_id")).lower() in query.lower()
            else 0
        )
        domain_bonus = 1 if meta.get("domain") == "light" else 0
        final = cos + BOOST_OVERLAP * overlap + BOOST_DOMAIN * domain_bonus + BOOST_AREA * area_match
        reranked.append((doc, final))

    reranked.sort(key=lambda x: x[1], reverse=True)
    results = reranked
    log.debug(
        "Vector search results: %s",
        [r[0]["metadata"].get("entity_id") for r in results],
    )
    if return_scores:
        return results
    return [r[0] for r in results]


async def async_query_vector_index(
    index_data: Tuple[np.ndarray, List[Dict]],
    query: str,
    k: int = 5,
    filters: Dict[str, Any] | None = None,
    return_scores: bool = False,
    hass: Any | None = None,
) -> List[Dict] | List[Tuple[Dict, float]]:
    """Run ``query_vector_index`` in an executor thread."""
    add_job = getattr(hass, "async_add_executor_job", None) if hass else None
    if callable(add_job) and add_job.__class__.__name__ != "MagicMock":
        return await add_job(
            query_vector_index, index_data, query, k, filters, return_scores
        )
    return await asyncio.to_thread(
        query_vector_index, index_data, query, k, filters, return_scores
    )


# ---------- Scene Memory Index Functions ----------


def build_scene_index(
    docs: List[Dict],
    persist_dir: str = DEFAULT_SCENE_PERSIST_DIR,
    force_rebuild: bool = False,
) -> Tuple[np.ndarray, List[Dict]]:
    """
    Build or load a scene memory index from memory entry documents.
    
    Args:
        docs: List of dicts with {"page_content": str, "metadata": dict}
        persist_dir: Directory to store scene index (default: .../scenes/)
        force_rebuild: Force rebuild even if index exists
        
    Returns:
        Tuple of (matrix, docs) for use with query_vector_index
    """
    log.debug(
        "build_scene_index: dir=%s force_rebuild=%s docs=%d",
        persist_dir,
        force_rebuild,
        len(docs),
    )
    os.makedirs(persist_dir, exist_ok=True)
    index_file = os.path.join(persist_dir, "matrix.npy")
    mapping_file = os.path.join(persist_dir, "mapping.json")
    meta_file = os.path.join(persist_dir, "meta.json")

    if (
        not force_rebuild
        and os.path.exists(index_file)
        and os.path.exists(mapping_file)
        and os.path.exists(meta_file)
    ):
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("embedding_model") == EMBED_MODEL:
                log.debug("Loading existing scene index from %s", persist_dir)
                matrix = np.load(index_file)
                with open(mapping_file, "r", encoding="utf-8") as f2:
                    mapping = json.load(f2)
                log.debug(
                    "Loaded scene index shape=%s docs=%d",
                    matrix.shape,
                    len(mapping),
                )
                return matrix, mapping
            log.debug("Embedding model changed; rebuilding scene index")
        except Exception as err:
            log.debug("Failed loading existing scene index: %s", err, exc_info=True)
            # fallthrough to rebuild

    if not docs:
        log.debug("No docs provided; creating empty scene index")
        # Create empty index
        matrix = np.zeros((0, DIMENSION), dtype=np.float32)
        docs = []
    else:
        # Build embeddings for each doc
        vectors = []
        for doc in docs:
            text = doc.get("page_content", "")
            if not text:
                log.warning("Empty page_content in scene doc, skipping")
                continue
            vec = _semantic_embed(text)
            vectors.append(vec)
        
        if not vectors:
            log.debug("No valid vectors generated; creating empty scene index")
            matrix = np.zeros((0, DIMENSION), dtype=np.float32)
            docs = []
        else:
            matrix = np.vstack(vectors).astype("float32")

    # Save to disk
    log.debug("Saving scene index matrix to %s", index_file)
    np.save(index_file, matrix)
    log.debug("Writing scene index mapping to %s", mapping_file)
    with open(mapping_file, "w", encoding="utf-8") as f:
        json.dump(docs, f, indent=2)
    log.debug("Writing scene index meta to %s", meta_file)
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "doc_count": len(docs),
                "embedding_model": EMBED_MODEL,
            },
            f,
        )

    log.info("Scene index built with %d docs", len(docs))
    log.debug("build_scene_index: completed")
    return matrix, docs


def load_scene_index(
    persist_dir: str = DEFAULT_SCENE_PERSIST_DIR,
) -> Tuple[np.ndarray, List[Dict]] | Tuple[None, None]:
    """Load a previously built scene index if available."""
    index_file = os.path.join(persist_dir, "matrix.npy")
    mapping_file = os.path.join(persist_dir, "mapping.json")
    log.debug("load_scene_index from %s", persist_dir)
    if os.path.exists(index_file) and os.path.exists(mapping_file):
        try:
            matrix = np.load(index_file)
            with open(mapping_file, "r", encoding="utf-8") as f:
                mapping = json.load(f)
            log.debug("Loaded scene index shape=%s docs=%d", matrix.shape, len(mapping))
            return matrix, mapping
        except Exception as err:
            log.debug("Error loading scene index: %s", err, exc_info=True)
            return None, None
    log.debug("No scene index found in %s", persist_dir)
    return None, None


async def async_load_scene_index(
    persist_dir: str = DEFAULT_SCENE_PERSIST_DIR,
    hass: Any | None = None,
) -> Tuple[np.ndarray, List[Dict]] | Tuple[None, None]:
    """Asynchronously load a previously built scene index if available."""
    index_file = os.path.join(persist_dir, "matrix.npy")
    mapping_file = os.path.join(persist_dir, "mapping.json")
    log.debug("async_load_scene_index from %s", persist_dir)
    if os.path.exists(index_file) and os.path.exists(mapping_file):
        try:
            add_job = getattr(hass, "async_add_executor_job", None) if hass else None
            if callable(add_job) and add_job.__class__.__name__ != "MagicMock":
                matrix = await add_job(np.load, index_file)

                def _load_json(path: str) -> Any:
                    with open(path, "r", encoding="utf-8") as f:
                        return json.load(f)

                mapping = await add_job(_load_json, mapping_file)
            else:
                matrix = await asyncio.to_thread(np.load, index_file)

                def _load_json() -> Any:
                    with open(mapping_file, "r", encoding="utf-8") as f:
                        return json.load(f)

                mapping = await asyncio.to_thread(_load_json)
            log.debug(
                "Loaded scene index shape=%s docs=%d", matrix.shape, len(mapping)
            )
            return matrix, mapping
        except Exception as err:
            log.debug("Error loading scene index: %s", err, exc_info=True)
            return None, None
    log.debug("No scene index found in %s", persist_dir)
    return None, None


# ---------- Scene Memory Operations (uses scene_memory_store.py) ----------


def _scene_entry_to_doc(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a scene memory entry to a vector index document.
    
    Args:
        entry: Memory entry dict from scene_memory_store
        
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


def rebuild_scene_index(hass: Any | None = None) -> None:
    """
    Rebuild the entire scene index from scene_memory_store.
    
    Args:
        hass: Home Assistant instance (optional)
    """
    log.debug("Rebuilding scene index from store")
    
    try:
        from . import scene_memory_store
        
        # Get all entries from store
        all_entries = scene_memory_store.get_all()
        
        # Convert to docs
        docs = [_scene_entry_to_doc(entry) for entry in all_entries]
        
        # Build index (full rebuild)
        # Note: Performance tracking done in async version
        build_scene_index(docs, force_rebuild=True)
        
        log.info("Scene index rebuilt with %d entries", len(docs))
    except ImportError:
        log.error("scene_memory_store not available for rebuild")


async def async_rebuild_scene_index(hass: Any | None = None) -> None:
    """Async version of rebuild_scene_index with performance tracking."""
    try:
        from . import performance
        
        async with performance.track_operation(
            "scene_index_rebuild",
            metadata={}
        ):
            add_job = getattr(hass, "async_add_executor_job", None) if hass else None
            if callable(add_job) and add_job.__class__.__name__ != "MagicMock":
                await add_job(rebuild_scene_index, hass)
            else:
                await asyncio.to_thread(rebuild_scene_index, hass)
    except ImportError:
        # Performance module not available, just rebuild
        add_job = getattr(hass, "async_add_executor_job", None) if hass else None
        if callable(add_job) and add_job.__class__.__name__ != "MagicMock":
            await add_job(rebuild_scene_index, hass)
        else:
            await asyncio.to_thread(rebuild_scene_index, hass)


def upsert_scene(entry: Dict[str, Any], hass: Any | None = None) -> None:
    """
    Insert or update a scene memory entry and rebuild index.
    
    Args:
        entry: Memory entry dict with at least "id" field
        hass: Home Assistant instance (optional)
    """
    log.debug("Upserting scene: %s", entry.get("id"))
    
    try:
        from . import scene_memory_store
        
        # Update store
        scene_memory_store.upsert(entry)
        
        # Rebuild index
        rebuild_scene_index(hass)
    except ImportError:
        log.error("scene_memory_store not available for upsert")


async def async_upsert_scene(entry: Dict[str, Any], hass: Any | None = None) -> None:
    """Async version of upsert_scene."""
    log.debug("Async upserting scene: %s", entry.get("id"))
    
    try:
        from . import scene_memory_store
        
        # Update store
        add_job = getattr(hass, "async_add_executor_job", None) if hass else None
        if callable(add_job) and add_job.__class__.__name__ != "MagicMock":
            await add_job(scene_memory_store.upsert, entry)
        else:
            await asyncio.to_thread(scene_memory_store.upsert, entry)
        
        # Rebuild index
        await async_rebuild_scene_index(hass)
    except ImportError:
        log.error("scene_memory_store not available for async upsert")


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
    
    try:
        from . import scene_memory_store
        
        # Delete from store
        deleted = scene_memory_store.delete(entry_id)
        
        if deleted:
            # Rebuild index
            rebuild_scene_index(hass)
            log.info("Scene removed and index rebuilt: %s", entry_id)
        
        return deleted
    except ImportError:
        log.error("scene_memory_store not available for remove")
        return False


async def async_remove_scene(entry_id: str, hass: Any | None = None) -> bool:
    """Async version of remove_scene."""
    log.debug("Async removing scene: %s", entry_id)
    
    try:
        from . import scene_memory_store
        
        # Delete from store
        add_job = getattr(hass, "async_add_executor_job", None) if hass else None
        if callable(add_job) and add_job.__class__.__name__ != "MagicMock":
            deleted = await add_job(scene_memory_store.delete, entry_id)
        else:
            deleted = await asyncio.to_thread(scene_memory_store.delete, entry_id)
        
        if deleted:
            # Rebuild index
            await async_rebuild_scene_index(hass)
            log.info("Scene removed and index rebuilt: %s", entry_id)
        
        return deleted
    except ImportError:
        log.error("scene_memory_store not available for async remove")
        return False
