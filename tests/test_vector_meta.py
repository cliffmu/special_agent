import json

from special_agent.utils.vector_index import build_device_index
from special_agent.utils import constants


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
    build_device_index(states, persist_dir=str(persist))

    with open(persist / "meta.json", encoding="utf-8") as f:
        meta = json.load(f)

    assert meta.get("excluded_count", 0) > 0
    assert meta.get("embedding_model") == constants.EMBED_MODEL
