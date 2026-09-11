"""Verify Responses requests with the real SDK and an in-memory HTTP transport."""

import asyncio
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from special_agent.utils.llm_client import call_llm, handle_llm_error


@pytest.fixture
def sdk_transport():
    openai = pytest.importorskip("openai")
    # SDK 3 uses httpx2; SDK 1/2 use httpx. No network or paid API calls.
    http_module = next(base.__module__.split(".")[0]
                       for base in openai.DefaultAsyncHttpxClient.__mro__
                       if base.__module__.split(".")[0] in {"httpx", "httpx2"})
    return openai, importlib.import_module(http_module)


@pytest.mark.parametrize("fast_mode,effective_tier", [(False, "default"), (True, "priority"), (True, "default")])
@pytest.mark.parametrize("strict", [False, True])
async def test_sdk_sends_explicit_tier_preserves_strict_and_reports_responses_usage(
    sdk_transport, caplog, fast_mode, effective_tier, strict,
):
    openai, httpx = sdk_transport
    payloads = []

    def respond(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "resp_test", "object": "response", "created_at": 1,
            "model": "gpt-5.6-terra", "status": "completed", "service_tier": effective_tier,
            "output": [
                {"type": "reasoning", "id": "reasoning_test", "summary": []},
                {"type": "function_call", "id": "fn_1", "call_id": "call_1",
                 "name": "registered_tool", "arguments": '{"secret":"PRIVATE_ARGUMENT"}'},
                {"type": "function_call", "id": "fn_2", "call_id": "call_2",
                 "name": "PRIVATE_UNREGISTERED_NAME", "arguments": "{}"},
                {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                 "content": [{"type": "output_text", "text": "PRIVATE_RESPONSE", "annotations": []}]},
            ],
            "usage": {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150,
                      "input_tokens_details": {"cached_tokens": 80},
                      "output_tokens_details": {"reasoning_tokens": 10}},
        })

    parameters = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    tools = [{"type": "function", "function": {
        "name": "registered_tool", "description": "PRIVATE_TOOL_DESCRIPTION",
        "parameters": parameters, "strict": strict,
    }}]
    async with openai.AsyncOpenAI(
        api_key="test-only", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    ) as client:
        result = await call_llm(client, [
            {"role": "system", "content": "PRIVATE_INSTRUCTIONS"},
            {"role": "user", "content": "PRIVATE_PROMPT"},
        ], tools, "gpt-5.6-terra", "minimal", 2, fast_mode=fast_mode)

    assert len(payloads) == 1
    sent = payloads[0]
    assert sent["model"] == "gpt-5.6-terra"
    assert sent["service_tier"] == ("fast" if fast_mode else "default")
    assert sent["reasoning"] == {"effort": "low"}
    assert sent["instructions"] == "PRIVATE_INSTRUCTIONS"
    assert sent["input"] == [{"role": "user", "content": "PRIVATE_PROMPT"}]
    assert sent["tools"][0] == {"type": "function", **tools[0]["function"]}
    assert sent["tools"][1] == {"type": "web_search"}
    assert result.usage == {
        "input_tokens": 120, "output_tokens": 30, "cached_tokens": 80, "total_tokens": 150,
        "prompt_tokens": 120, "completion_tokens": 30,
    }
    assert result.reasoning_count == 1 and result.final_text == "PRIVATE_RESPONSE"
    lines = [record.getMessage() for record in caplog.records
             if record.name == "custom_components.special_agent.activity"]
    assert len(lines) == 2
    assert "phase=sent" in lines[0] and "phase=received" in lines[1]
    assert f"effective_tier={effective_tier}" in lines[1]
    assert "function_count=2" in lines[1]
    assert "function_names=registered_tool,unknown" in lines[1]
    assert "input_tokens=120" in lines[1] and "output_tokens=30" in lines[1]
    assert "PRIVATE" not in caplog.text


async def test_rejected_fast_request_does_not_retry_another_tier_or_log_error_body(sdk_transport, caplog):
    openai, httpx = sdk_transport
    payloads = []

    def reject(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {
            "message": "PRIVATE_ERROR_BODY", "type": "invalid_request_error", "code": "unsupported_value",
        }})

    async with openai.AsyncOpenAI(
        api_key="test-only", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(reject)),
    ) as client:
        with pytest.raises(openai.BadRequestError) as caught:
            await call_llm(client, [], [], "gpt-6-astra", "low", 0, fast_mode=True)
    handle_llm_error(caught.value, [])
    assert len(payloads) == 1 and payloads[0]["service_tier"] == "fast"
    assert "phase=failed" in caplog.text and "error_type=BadRequestError" in caplog.text
    assert "PRIVATE_ERROR_BODY" not in caplog.text


async def test_cancelled_llm_request_logs_completion_without_retry(caplog):
    create = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await call_llm(SimpleNamespace(responses=SimpleNamespace(create=create)), [], [], "gpt-5", "low", 0)
    create.assert_awaited_once()
    assert "phase=failed" in caplog.text and "status=cancelled" in caplog.text
