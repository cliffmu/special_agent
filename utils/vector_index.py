"""Simple FAISS-based vector index for Home Assistant devices."""
from __future__ import annotations

import json
import os
from typing import Iterable, Tuple, List, Dict

import faiss
import numpy as np


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
) -> Tuple[faiss.Index, List[Dict]]:
    """Build or load a FAISS index from Home Assistant states."""
    os.makedirs(persist_dir, exist_ok=True)
    index_file = os.path.join(persist_dir, "index.faiss")
    mapping_file = os.path.join(persist_dir, "mapping.json")

    if not force_rebuild and os.path.exists(index_file) and os.path.exists(mapping_file):
        try:
            index = faiss.read_index(index_file)
            with open(mapping_file, "r", encoding="utf-8") as f:
                mapping = json.load(f)
            return index, mapping
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
    index = faiss.IndexFlatL2(matrix.shape[1])
    index.add(matrix)

    faiss.write_index(index, index_file)
    with open(mapping_file, "w", encoding="utf-8") as f:
        json.dump(docs, f, indent=2)

    return index, docs


def load_vector_index(persist_dir: str = "vector_index") -> Tuple[faiss.Index, List[Dict]] | Tuple[None, None]:
    """Load a previously built FAISS index if available."""
    index_file = os.path.join(persist_dir, "index.faiss")
    mapping_file = os.path.join(persist_dir, "mapping.json")
    if os.path.exists(index_file) and os.path.exists(mapping_file):
        try:
            index = faiss.read_index(index_file)
            with open(mapping_file, "r", encoding="utf-8") as f:
                mapping = json.load(f)
            return index, mapping
        except Exception:
            return None, None
    return None, None


def query_vector_index(index_data: Tuple[faiss.Index, List[Dict]], query: str, k: int = 5) -> List[Dict]:
    """Query the FAISS index and return matching docs."""
    if not index_data or index_data[0] is None:
        return []
    index, docs = index_data
    vec = _text_to_vector(query)
    distances, indices = index.search(np.array([vec], dtype="float32"), k)
    results = []
    for idx in indices[0]:
        if 0 <= idx < len(docs):
            results.append(docs[idx])
    return results
