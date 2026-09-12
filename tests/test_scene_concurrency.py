"""Concurrent scene saves preserve entries and expose complete index snapshots."""

import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from functools import partial
import json
import threading

import numpy as np
import pytest

from special_agent.utils import scene_memory_store as memory, vector_index


def test_two_store_instances_save_independent_entries_without_losing_existing_file(tmp_path, monkeypatch):
    path = tmp_path / "scenes.json"
    path.write_text(json.dumps({"entries": {"existing": {"id": "existing"}}}))
    first, second = memory.SceneMemoryStore(str(path)), memory.SceneMemoryStore(str(path))
    loaded, release = threading.Event(), threading.Event()
    original_load = first._load

    def paused_load():
        entries = original_load()
        loaded.set()
        assert release.wait(2)
        return entries

    monkeypatch.setattr(first, "_load", paused_load)
    with ThreadPoolExecutor(max_workers=2) as executor:
        save_a = executor.submit(first.upsert, {"id": "scene-a"})
        save_b = None
        try:
            assert loaded.wait(1)
            save_b = executor.submit(second.upsert, {"id": "scene-b"})
            with pytest.raises(FutureTimeout):
                save_b.result(timeout=0.05)
        finally:
            release.set()
            save_a.result(timeout=1)
            if save_b is not None:
                save_b.result(timeout=1)
    assert set(json.loads(path.read_text())["entries"]) == {"existing", "scene-a", "scene-b"}


async def test_parallel_async_scene_saves_leave_index_matching_complete_store(tmp_path, monkeypatch):
    store = memory.SceneMemoryStore(str(tmp_path / "scenes.json"))
    store.upsert({"id": "existing", "intent": "existing", "steps": []})
    monkeypatch.setattr(memory, "_store_instance", store)
    monkeypatch.setattr(vector_index, "_semantic_embed", lambda text: np.ones(3, dtype=np.float32))
    build = vector_index.build_scene_index
    index_dir = str(tmp_path / "index")
    monkeypatch.setattr(vector_index, "build_scene_index", partial(build, persist_dir=index_dir))

    await asyncio.gather(*(vector_index.async_upsert_scene({"id": identity, "intent": identity, "steps": []})
                           for identity in ("scene-a", "scene-b")))

    matrix, documents = await vector_index.async_load_scene_index(index_dir)
    ids = {entry["id"] for entry in store.get_all()}
    assert ids == {"existing", "scene-a", "scene-b"}
    assert matrix.shape == (3, 3)
    assert {doc["metadata"]["entry_id"] for doc in documents} == ids


async def test_index_reader_cannot_mix_new_matrix_with_old_mapping(tmp_path, monkeypatch):
    monkeypatch.setattr(vector_index, "_semantic_embed", lambda text: np.ones(3, dtype=np.float32))
    index_dir = str(tmp_path / "index")
    old_docs = [{"page_content": "first", "metadata": {"id": "first"}}]
    new_docs = old_docs + [{"page_content": "second", "metadata": {"id": "second"}}]
    vector_index.build_scene_index(old_docs, persist_dir=index_dir, force_rebuild=True)
    old_matrix, old_mapping = vector_index.load_scene_index(index_dir)
    matrix_written, release = threading.Event(), threading.Event()
    save_matrix = vector_index.np.save

    def paused_save(*args, **kwargs):
        save_matrix(*args, **kwargs)
        matrix_written.set()
        assert release.wait(2)

    monkeypatch.setattr(vector_index.np, "save", paused_save)
    writer = asyncio.create_task(asyncio.to_thread(
        vector_index.build_scene_index, new_docs, index_dir, True))
    reader = None
    try:
        assert await asyncio.to_thread(matrix_written.wait, 1)
        reader = asyncio.create_task(vector_index.async_load_scene_index(index_dir))
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(reader), 0.05)
        # Previously loaded snapshots stay usable while shared files are rebuilt.
        assert old_matrix.shape == (1, 3) and old_mapping == old_docs
    finally:
        release.set()
        await asyncio.wait_for(writer, 1)
        if reader is not None:
            matrix, mapping = await asyncio.wait_for(reader, 1)
    assert matrix.shape == (2, 3) and mapping == new_docs


def test_failed_atomic_scene_save_preserves_existing_file(tmp_path, monkeypatch):
    path = tmp_path / "scenes.json"
    store = memory.SceneMemoryStore(str(path))
    store.upsert({"id": "existing"})
    original = path.read_bytes()

    def fail_replace(*args):
        raise OSError("test replacement failure")

    monkeypatch.setattr(memory.os, "replace", fail_replace)
    with pytest.raises(OSError):
        store.upsert({"id": "new"})
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
