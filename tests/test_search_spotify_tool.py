import asyncio

from special_agent.tool_specs.search_spotify import search_spotify


async def run_tool():
    return await search_spotify("hello", type="track")


def test_search_spotify_missing_creds(monkeypatch):
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    result = asyncio.run(run_tool())
    assert result is None
