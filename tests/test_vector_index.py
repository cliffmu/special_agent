import os
from utils.vector_index import build_vector_index, load_vector_index, query_vector_index


def test_build_and_query_vector_index(tmp_path):
    states = [
        {"entity_id": "light.kitchen", "name": "Kitchen Light", "attributes": {"friendly_name": "Kitchen Light"}},
        {"entity_id": "switch.garage", "name": "Garage Switch", "attributes": {"friendly_name": "Garage"}},
    ]
    persist = tmp_path / "index"
    index, docs = build_vector_index(states, persist_dir=str(persist))

    assert os.path.exists(persist / "matrix.npy")
    assert len(docs) == 2

    loaded = load_vector_index(str(persist))
    results = query_vector_index(loaded, "kitchen", k=2)
    ids = [r["metadata"]["entity_id"] for r in results]
    assert "light.kitchen" in ids


def test_build_vector_index_includes_area(tmp_path):
    states = [
        {
            "entity_id": "light.bedroom",
            "name": "Bedroom Light",
            "attributes": {"friendly_name": "Bedroom Light"},
            "area_id": "bedroom",
        }
    ]

    persist = tmp_path / "index_area"
    _, docs = build_vector_index(states, persist_dir=str(persist))

    assert docs[0]["metadata"].get("area_id") == "bedroom"
