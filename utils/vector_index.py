"""Simple NumPy-based vector index for Home Assistant devices."""
from __future__ import annotations

import logging
import json
import os
from typing import Iterable, Tuple, List, Dict

import numpy as np

from . import logging as log

_LOGGER = logging.getLogger(__package__)


DIMENSION = 128


def _text_to_vector(text: str, dim: int = DIMENSION) -> np.ndarray:
    """Hash words into a fixed-size vector."""
    vec = np.zeros(dim, dtype=np.float32)
    for word in text.split():
        idx = hash(word) % dim
        vec[idx] += 1.0
    return vec


def build_vector_index(
    states: Iterable[Dict],
    persist_dir: str = "vector_index",
    force_rebuild: bool = False,
) -> Tuple[np.ndarray, List[Dict]]:
    """Build or load a NumPy index from Home Assistant states."""
    os.makedirs(persist_dir, exist_ok=True)
    index_file = os.path.join(persist_dir, "matrix.npy")
    mapping_file = os.path.join(persist_dir, "mapping.json")

    if not force_rebuild and os.path.exists(index_file) and os.path.exists(mapping_file):
        try:
            matrix = np.load(index_file)
            with open(mapping_file, "r", encoding="utf-8") as f:
                mapping = json.load(f)
            return matrix, mapping
        except Exception:
            pass  # fallthrough to rebuild

    docs = []
    vectors = []
    for st in states:
        text = (
            f"Entity: {st.get('entity_id')}\n"
            f"Name: {st.get('name')}\n"
            f"Attributes: {st.get('attributes')}"
        )
        vec = _text_to_vector(text)
        docs.append({"page_content": text, "metadata": {"entity_id": st.get("entity_id")}})
        vectors.append(vec)

    if not vectors:
        raise ValueError("No states provided to build vector index")

    matrix = np.vstack(vectors).astype("float32")
    np.save(index_file, matrix)
    with open(mapping_file, "w", encoding="utf-8") as f:
        json.dump(docs, f, indent=2)

    log.info("Vector index rebuilt with %d docs", len(docs))
    return matrix, docs


def load_vector_index(persist_dir: str = "vector_index") -> Tuple[np.ndarray, List[Dict]] | Tuple[None, None]:
    """Load a previously built NumPy index if available."""
    index_file = os.path.join(persist_dir, "matrix.npy")
    mapping_file = os.path.join(persist_dir, "mapping.json")
    if os.path.exists(index_file) and os.path.exists(mapping_file):
        try:
            matrix = np.load(index_file)
            with open(mapping_file, "r", encoding="utf-8") as f:
                mapping = json.load(f)
            return matrix, mapping
        except Exception:
            return None, None
    return None, None


def query_vector_index(index_data: Tuple[np.ndarray, List[Dict]], query: str, k: int = 5) -> List[Dict]:
    """Query the index and return matching docs using cosine similarity."""
    if not index_data or index_data[0] is None:
        return []
    matrix, docs = index_data
    log.debug("Vector search query '%s' k=%s", query, k)
    vec = _text_to_vector(query)
    matrix_norm = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9)
    vec_norm = vec / (np.linalg.norm(vec) + 1e-9)
    scores = matrix_norm @ vec_norm
    top_indices = scores.argsort()[::-1][:k]
    results = [docs[i] for i in top_indices]
    log.debug("Vector search results: %s", [r["metadata"]["entity_id"] for r in results])
    return results
