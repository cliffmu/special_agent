import logging
import asyncio

import pytest
from utils import logging as log


def test_info_log_level(hass, caplog):
    caplog.set_level(logging.INFO)
    log.info("Hello")
    assert "Hello" in caplog.text


def test_debug_toggle(hass, caplog):
    caplog.set_level(logging.DEBUG)
    log.debug("Verbose")
    assert "Verbose" in caplog.text


def test_activity_is_visible_without_enabling_legacy_info(caplog):
    previous = log._LOGGER.level
    try:
        log._LOGGER.setLevel(logging.WARNING)
        caplog.set_level(logging.INFO, logger=log._ACTIVITY_LOGGER.name)
        log.info("LEGACY_ARGUMENT_DUMP")
        log.activity("request", phase="received")
        assert "event=request" in caplog.text
        assert "LEGACY_ARGUMENT_DUMP" not in caplog.text
    finally:
        log._LOGGER.setLevel(previous)


def test_activity_allowlist_drops_payloads_and_redacts_unsafe_values(caplog):
    caplog.set_level(logging.INFO)

    class PrivateObject:
        def __str__(self):
            raise AssertionError("Private objects must never be stringified")

    log.activity("tool", tool="registered_tool", phase="finished", status="error", elapsed_ms=12,
                 prompt="PRIVATE_PROMPT", arguments={"secret": "PRIVATE_ARGUMENT"},
                 result="PRIVATE_RESULT", api_key="PRIVATE_API_KEY", model="sk-private-model-value",
                 error_type="ValueError\nPRIVATE_EXCEPTION", function_names=["registered_tool", PrivateObject()],
                 total_tokens=10**1000)
    line = caplog.records[-1].getMessage()
    assert "tool=registered_tool" in line and "elapsed_ms=12" in line
    assert "function_names=registered_tool,redacted" in line
    assert "model=redacted" in line and "total_tokens=redacted" in line
    assert "PRIVATE" not in line and "sk-" not in line and "\n" not in line


async def test_activity_context_is_isolated_across_parallel_requests_and_child_tasks(caplog):
    caplog.set_level(logging.INFO)
    entered, release = asyncio.Event(), asyncio.Event()

    async def child(tool):
        await release.wait()
        log.activity("tool", tool=tool, phase="started")

    async def request(tool, first=False):
        token = log.begin_request()
        try:
            log.activity("request", tool=tool, phase="received")
            task = asyncio.create_task(child(tool))
            if first:
                entered.set()
            await task
        finally:
            log.end_request(token)

    first = asyncio.create_task(request("first", True))
    await entered.wait()
    second = asyncio.create_task(request("second"))
    release.set()
    await asyncio.gather(first, second)
    lines = [dict(field.split("=", 1) for field in record.getMessage().split())
             for record in caplog.records if record.name == log._ACTIVITY_LOGGER.name]
    first_ids = {item["request"] for item in lines if item.get("tool") == "first"}
    second_ids = {item["request"] for item in lines if item.get("tool") == "second"}
    assert len(first_ids) == len(second_ids) == 1 and first_ids != second_ids
    log.activity("outside")
    assert "request=-" in caplog.records[-1].getMessage()
