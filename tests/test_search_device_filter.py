import asyncio
import importlib

from special_agent.utils.vector_index import build_device_index


def test_search_device_filter(tmp_path, monkeypatch):
    states = [
        {
            "entity_id": "light.office_sconces",
            "name": "Office Sconces",
            "attributes": {
                "friendly_name": "Office Sconces",
                "brightness": 128,
            },
            "domain": "light",
        },
        {
            "entity_id": "sensor.office_sconces_led_effect",
            "name": "LED Effect",
            "attributes": {"friendly_name": "Office LED"},
            "domain": "sensor",
        },
    ]

    persist = tmp_path / "index"
    build_device_index(states, persist_dir=str(persist))

    monkeypatch.setenv("SPECIAL_AGENT_PERSIST_DIR", str(persist))
    import special_agent.utils.vector_index as vi
    import special_agent.tool_specs.search_devices as sd

    importlib.reload(vi)
    importlib.reload(sd)

    async def run_search():
        return await sd.search_devices("turn on the office light", k=5)

    results = asyncio.run(run_search())

    ids = [r["entity_id"] for r in results]
    assert "light.office_sconces" in ids
    assert all("sensor.office_sconces_led_effect" != r for r in ids)

    info = next(r["info"] for r in results if r["entity_id"] == "light.office_sconces")
    attr_keys = next(r.get("attribute_keys") for r in results if r["entity_id"] == "light.office_sconces")
    assert "128" not in info
    assert "brightness" not in info and "friendly_name" not in info
    assert info.endswith("Attributes:")
    assert set(attr_keys) == {"friendly_name", "brightness"}
