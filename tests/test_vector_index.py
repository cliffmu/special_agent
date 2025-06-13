import os
from utils.vector_index import build_vector_index, load_vector_index, query_vector_index


def test_build_and_query_vector_index(tmp_path):
    states = [
        {"entity_id": "light.kitchen", "name": "Kitchen Light", "attributes": {"friendly_name": "Kitchen Light"}},
        {"entity_id": "switch.garage", "name": "Garage Switch", "attributes": {"friendly_name": "Garage"}},
    ]
    persist = tmp_path / "index"
    index, docs = build_vector_index(states, persist_dir=str(persist))

    assert os.path.exists(persist / "index.faiss")
    assert len(docs) == 2

    loaded = load_vector_index(str(persist))
    results = query_vector_index(loaded, "kitchen", k=2)
    ids = [r["metadata"]["entity_id"] for r in results]
    assert "light.kitchen" in ids
