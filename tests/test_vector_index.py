import os
import json
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


def test_mapping_contains_light_area(tmp_path):
    states = [
        {
            "entity_id": "light.kitchen_ceiling",
            "name": "Kitchen Ceiling",
            "attributes": {"friendly_name": "Kitchen Ceiling"},
            "area_id": "kitchen",
        },
        {
            "entity_id": "switch.outlet",
            "name": "Outlet",
            "attributes": {"friendly_name": "Outlet"},
        },
    ]

    persist = tmp_path / "index_check"
    build_vector_index(states, persist_dir=str(persist))

    with open(persist / "mapping.json", encoding="utf-8") as f:
        mapping = json.load(f)

    assert any(
        doc["metadata"].get("domain") == "light" and doc["metadata"].get("area_id")
        for doc in mapping
    )


def test_query_vector_index_with_filters(tmp_path):
    states = [
        {
            "entity_id": "light.office_ceiling",
            "name": "Office Ceiling",
            "attributes": {"friendly_name": "Office Ceiling"},
            "area_id": "office",
            "domain": "light",
        },
        {
            "entity_id": "switch.office_fan",
            "name": "Office Fan",
            "attributes": {"friendly_name": "Office Fan"},
            "area_id": "office",
            "domain": "switch",
        },
    ]

    persist = tmp_path / "index_filter"
    index = build_vector_index(states, persist_dir=str(persist))

    results = query_vector_index(
        index,
        "office light",
        k=5,
        filters={"area_id": "office", "domain": "light"},
    )

    assert any(
        r["metadata"].get("domain") == "light" and "office" in (r["metadata"].get("area_id") or "")
        for r in results
    )
