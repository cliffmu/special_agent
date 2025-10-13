import json
import numpy as np
from special_agent.utils import constants
import special_agent.utils.vector_index as vi


def test_embedding_dimensions_and_ranking(tmp_path, monkeypatch):
    def fake_embed(text: str) -> np.ndarray:
        vec = np.zeros(constants.EMBED_DIM, dtype=np.float32)
        for i, word in enumerate(text.split()):
            vec[i % constants.EMBED_DIM] += 1.0
        return vec / (np.linalg.norm(vec) + 1e-9)

    monkeypatch.setattr(vi, "_semantic_embed", fake_embed)

    states = [
        {
            "entity_id": "light.office_lamp",
            "name": "Office Lamp",
            "attributes": {"friendly_name": "Office Lamp"},
            "area_id": "office",
            "domain": "light",
        },
        {
            "entity_id": "sensor.office_temp",
            "name": "Office Temp",
            "attributes": {"friendly_name": "Office Temp"},
            "area_id": "office",
            "domain": "sensor",
        },
    ]

    persist = tmp_path / "index"
    matrix, docs = vi.build_device_index(states, persist_dir=str(persist))

    assert matrix.shape[1] == constants.EMBED_DIM

    with open(persist / "meta.json", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["embedding_model"] == constants.EMBED_MODEL

    results = vi.query_vector_index((matrix, docs), "office light", k=2)
    assert results[0]["metadata"]["domain"] == "light"
