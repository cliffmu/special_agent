"""Simple NumPy-based vector index for Home Assistant devices."""

from __future__ import annotations

import logging
import json
import os
from pathlib import Path
from typing import Iterable, Tuple, List, Dict

import numpy as np

from . import logging as log
from .constants import EXCLUDED_DOMAINS, EXCLUDED_SUFFIXES

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

_LOGGER = logging.getLogger(__package__)


DIMENSION = 128


def _text_to_vector(text: str, dim: int = DIMENSION) -> np.ndarray:
    """Hash words into a fixed-size vector."""
    # log.debug("Vectorizing text: %s", text.replace("\n", " ")[:80])
    vec = np.zeros(dim, dtype=np.float32)
    for word in text.split():
        idx = hash(word) % dim
        vec[idx] += 1.0
    return vec


def build_vector_index(
    states: Iterable[Dict],
    persist_dir: str = DEFAULT_PERSIST_DIR,
    force_rebuild: bool = False,
) -> Tuple[np.ndarray, List[Dict]]:
    """Build or load a NumPy index from Home Assistant states."""
    states = list(states)
    log.debug(
        "build_vector_index: dir=%s force_rebuild=%s states=%d",
        persist_dir,
        force_rebuild,
        len(states),
    )
    os.makedirs(persist_dir, exist_ok=True)
    index_file = os.path.join(persist_dir, "matrix.npy")
    mapping_file = os.path.join(persist_dir, "mapping.json")

    if (
        not force_rebuild
        and os.path.exists(index_file)
        and os.path.exists(mapping_file)
    ):
        try:
            log.debug("Loading existing index from %s", persist_dir)
            matrix = np.load(index_file)
            with open(mapping_file, "r", encoding="utf-8") as f:
                mapping = json.load(f)
            log.debug("Loaded index shape=%s docs=%d", matrix.shape, len(mapping))
            return matrix, mapping
        except Exception as err:
            log.debug("Failed loading existing index: %s", err, exc_info=True)
            # fallthrough to rebuild

    docs = []
    vectors = []
    excluded_count = 0
    for st in states:
        entity_id = st.get("entity_id", "")
        domain = st.get("domain") or entity_id.split(".")[0]
        if domain in EXCLUDED_DOMAINS or entity_id.endswith(EXCLUDED_SUFFIXES):
            excluded_count += 1
            continue

        text = (
            f"Entity: {entity_id}\n"
            f"Name: {st.get('name')}\n"
            f"Attributes: {st.get('attributes')}"
        )
        vec = _text_to_vector(text)
        meta = {
            "entity_id": entity_id,
            "friendly_name": st.get("attributes", {}).get("friendly_name"),
            # Prefer top-level area_id injected by get_ha_states; fall back to
            # any value stored under attributes for backward compatibility.
            "area_id": st.get("area_id") or st.get("attributes", {}).get("area_id"),
            "domain": domain,
        }
        docs.append({"page_content": text, "metadata": meta})
        vectors.append(vec)

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
        json.dump({"excluded_count": excluded_count}, f)

    log.info(
        "Vector index rebuilt with %d docs (excluded=%d)", len(docs), excluded_count
    )
    log.debug("build_vector_index: completed")
    return matrix, docs


def load_vector_index(
    persist_dir: str = DEFAULT_PERSIST_DIR,
) -> Tuple[np.ndarray, List[Dict]] | Tuple[None, None]:
    """Load a previously built NumPy index if available."""
    index_file = os.path.join(persist_dir, "matrix.npy")
    mapping_file = os.path.join(persist_dir, "mapping.json")
    log.debug("load_vector_index from %s", persist_dir)
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


def query_vector_index(
    index_data: Tuple[np.ndarray, List[Dict]],
    query: str,
    k: int = 5,
    return_scores: bool = False,
) -> List[Dict] | List[Tuple[Dict, float]]:
    """Query the index and return matching docs using cosine similarity."""
    if not index_data or index_data[0] is None:
        log.debug("query_vector_index: no index data")
        return []
    matrix, docs = index_data
    log.debug("Vector search query '%s' k=%s", query, k)
    vec = _text_to_vector(query)
    matrix_norm = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9)
    vec_norm = vec / (np.linalg.norm(vec) + 1e-9)
    scores = matrix_norm @ vec_norm
    top_indices = scores.argsort()[::-1][:k]
    if return_scores:
        results = [(docs[i], float(scores[i])) for i in top_indices]
    else:
        results = [docs[i] for i in top_indices]
    log.debug(
        "Vector search results: %s",
        [
            (
                r[0]["metadata"]["entity_id"]
                if return_scores
                else r["metadata"]["entity_id"]
            )
            for r in results
        ],
    )
    return results
