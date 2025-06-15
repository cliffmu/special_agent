import json

from special_agent.utils.vector_index import build_vector_index


def test_vector_meta(tmp_path):
    states = [
        {
            "entity_id": "light.kitchen",
            "name": "Kitchen",
            "attributes": {},
            "domain": "light",
        },
        {
            "entity_id": "sensor.kitchen_temperature",
            "name": "Temp",
            "attributes": {},
            "domain": "sensor",
        },
    ]

    persist = tmp_path / "index"
    build_vector_index(states, persist_dir=str(persist))

    with open(persist / "meta.json", encoding="utf-8") as f:
        meta = json.load(f)

    assert meta.get("excluded_count", 0) > 0
