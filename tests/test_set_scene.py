"""Scene memory disk I/O stays off HA's event loop for saves and updates."""

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from special_agent.tool_specs.set_scene import set_scene
from special_agent.utils import performance, scene_memory_store, vector_index


STEPS = [{
    "type": "service_call", "service": "light.turn_on",
    "data": {"entity_id": "light.test"},
}]


@pytest.fixture
def checked_store(monkeypatch, tmp_path):
    """Use real JSON storage, failing if construction or reads/writes block HA."""
    path = tmp_path / "scenes.json"
    event_loop_thread = threading.get_ident()
    operations = []
    original_store = scene_memory_store.SceneMemoryStore

    class CheckedStore(original_store):
        def check_thread(self, operation):
            assert threading.get_ident() != event_loop_thread
            operations.append(operation)

        def __init__(self):
            self.check_thread("construct")
            super().__init__(str(path))

        def _load(self):
            self.check_thread("read")
            return super()._load()

        def _save(self, entries):
            self.check_thread("write")
            return super()._save(entries)

    monkeypatch.setattr(scene_memory_store, "SceneMemoryStore", CheckedStore)
    monkeypatch.setattr(scene_memory_store, "_store_instance", None)
    monkeypatch.setattr(performance, "_enabled", False)
    return path, operations, event_loop_thread


def home_assistant(use_hass):
    if not use_hass:
        return None
    return SimpleNamespace(async_add_executor_job=AsyncMock(side_effect=asyncio.to_thread))


@pytest.mark.parametrize("use_hass", [False, True])
@pytest.mark.parametrize("area", [None, "office"])
@pytest.mark.parametrize("provide_steps", [False, True])
async def test_set_scene_reads_in_executor_and_uses_same_scene_id(
    monkeypatch, checked_store, use_hass, area, provide_steps,
):
    path, operations, _ = checked_store
    entry_id = f"evening_{area or 'global'}"
    if not provide_steps:
        path.write_text(json.dumps({"entries": {entry_id: {"id": entry_id, "steps": STEPS}}}))
    upsert = AsyncMock()
    monkeypatch.setattr(vector_index, "async_upsert_scene", upsert)
    hass = home_assistant(use_hass)

    result = await set_scene(
        "evening", "success", steps=STEPS if provide_steps else None, area=area, hass=hass,
    )

    assert result["entry_id"] == entry_id
    assert operations[0] == "construct"
    assert operations.count("read") >= 1
    if provide_steps:
        assert result["status"] == "ok"
        upsert.assert_awaited_once()
        assert upsert.await_args.args[0]["id"] == entry_id
        assert upsert.await_args.args[0]["steps"] == STEPS
    else:
        # A successful replay reuses the stored steps and avoids redundant writes.
        assert result["status"] == "skipped"
        upsert.assert_not_awaited()
    if use_hass:
        assert hass.async_add_executor_job.await_count == operations.count("read")


@pytest.mark.parametrize("use_hass", [False, True])
async def test_async_upsert_offloads_store_creation_write_and_index_rebuild(
    monkeypatch, checked_store, use_hass,
):
    path, operations, event_loop_thread = checked_store
    indexed = []

    def build_index(docs, **kwargs):
        assert threading.get_ident() != event_loop_thread
        indexed.extend(docs)

    monkeypatch.setattr(vector_index, "build_scene_index", build_index)
    entry = {"id": "evening_global", "intent": "evening", "steps": STEPS}
    await vector_index.async_upsert_scene(entry, hass=home_assistant(use_hass))

    assert json.loads(path.read_text())["entries"][entry["id"]] == entry
    assert operations == ["construct", "read", "write", "read"]
    assert indexed[0]["metadata"]["steps"] == STEPS


@pytest.mark.parametrize("condition", [
    {"entity_id": "light.test", "state": "on", "timeout_ms": 2000},
    {"entity_id": "light.test", "attribute": "brightness", "value": 128, "timeout_ms": 3000},
])
@pytest.mark.parametrize("shorthand", [False, True])
def test_scene_normalization_preserves_explicit_post_condition(condition, shorthand):
    step = {**STEPS[0], "post_condition": condition}
    if shorthand:
        step = {"service": "light.turn_on", "entity_id": "light.test", "post_condition": condition}
    normalized, errors = scene_memory_store.normalize_and_validate_steps([step])
    assert errors == []
    assert normalized == [{**STEPS[0], "post_condition": condition}]
    assert normalized[0]["post_condition"] is not condition


@pytest.mark.parametrize("condition", [None, {}, "on", ["on"]])
def test_scene_normalization_rejects_malformed_post_condition(condition):
    normalized, errors = scene_memory_store.normalize_and_validate_steps([
        {**STEPS[0], "post_condition": condition},
    ])
    assert normalized == []
    assert len(errors) == 1 and "post_condition" in errors[0]


@pytest.mark.parametrize("previous", [None, {"entity_id": "light.test", "state": "off"}])
async def test_adding_or_changing_verification_updates_saved_scene(monkeypatch, checked_store, previous):
    path, _, _ = checked_store
    old_step = dict(STEPS[0])
    if previous is not None:
        old_step["post_condition"] = previous
    path.write_text(json.dumps({"entries": {"evening_global": {
        "id": "evening_global", "steps": [old_step],
    }}}))
    condition = {"entity_id": "light.test", "state": "on", "timeout_ms": 2000}
    new_step = {**STEPS[0], "post_condition": condition}
    upsert = AsyncMock()
    monkeypatch.setattr(vector_index, "async_upsert_scene", upsert)

    result = await set_scene("evening", "success", steps=[new_step])

    assert result["status"] == "ok"
    assert upsert.await_args.args[0]["steps"] == [new_step]
    assert scene_memory_store.should_update_scene({"steps": [new_step]}, [new_step], "success") == (False, "unchanged")
