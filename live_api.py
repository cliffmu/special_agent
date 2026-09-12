"""Authenticated Live bridge endpoints, using HA's conversation dispatcher."""

from __future__ import annotations

import json
import re
import uuid

from aiohttp import web
from homeassistant.components import conversation, http

from . import DOMAIN
from .conversation import SpecialAgentConversation, live_model_settings
from .session_store import SessionBusyError
from .utils.constants import AGENT_MODELS, REASONING_EFFORTS
from .utils.logging import read_activity

_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_LANGUAGE = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")
_FIELDS = {"agent_id", "text", "language", "conversation_id", "device_id", "model", "reasoning_effort", "fast_mode"}
_MAX_BODY = 1024 * 1024


def register_views(hass):
    """Register once, including when HA loads a config entry without YAML setup."""
    state = hass.data.setdefault(DOMAIN, {})
    if not state.get("live_api_registered"):
        hass.http.register_view(LiveProcessView())
        hass.http.register_view(LiveActivityView())
        state["live_api_registered"] = True


async def _request_data(request):
    if request.content_length is not None and request.content_length > _MAX_BODY:
        raise web.HTTPRequestEntityTooLarge(max_size=_MAX_BODY, actual_size=request.content_length)
    body = bytearray()
    async for chunk in request.content.iter_chunked(16384):
        body.extend(chunk)
        if len(body) > _MAX_BODY:
            raise web.HTTPRequestEntityTooLarge(max_size=_MAX_BODY, actual_size=len(body))
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError) as error:
        raise web.HTTPBadRequest(text="Invalid JSON request") from error
    if not isinstance(data, dict) or set(data) - _FIELDS:
        raise web.HTTPBadRequest(text="Unsupported request fields")
    if not isinstance(data.get("text"), str) or not data["text"].strip() or len(data["text"]) > 65536:
        raise web.HTTPBadRequest(text="Text must contain between 1 and 65536 characters")
    for field in ("agent_id", "conversation_id", "device_id"):
        if field != "agent_id" and field not in data:
            continue
        if not isinstance(data.get(field), str) or not _IDENTIFIER.fullmatch(data[field]):
            raise web.HTTPBadRequest(text=f"Invalid {field}")
    if "language" in data and (not isinstance(data["language"], str) or not _LANGUAGE.fullmatch(data["language"])):
        raise web.HTTPBadRequest(text="Invalid language")
    if "model" in data and (not isinstance(data["model"], str) or data["model"] not in AGENT_MODELS):
        raise web.HTTPBadRequest(text="Unsupported model")
    if "reasoning_effort" in data and (not isinstance(data["reasoning_effort"], str) or data["reasoning_effort"] not in REASONING_EFFORTS):
        raise web.HTTPBadRequest(text="Unsupported reasoning effort")
    if "fast_mode" in data and type(data["fast_mode"]) is not bool:
        raise web.HTTPBadRequest(text="fast_mode must be a boolean")
    return data


class LiveProcessView(http.HomeAssistantView):
    url = "/api/special_agent/live/process"
    name = "api:special_agent:live:process"
    requires_auth = True

    async def post(self, request):
        data = await _request_data(request)
        hass = request.app[http.KEY_HASS]
        agent = conversation.async_get_agent(hass, data["agent_id"])
        active = hass.data.get(DOMAIN, {}).get("conversation_agents", {})
        if not isinstance(agent, SpecialAgentConversation) or not any(agent is item for item in active.values()):
            raise web.HTTPBadRequest(text="agent_id must select the active Special Agent integration")
        settings = {key: data[key] for key in ("model", "reasoning_effort", "fast_mode") if key in data}
        # Supplying a real ID before dispatch avoids the integration's None session key.
        conversation_id = data.get("conversation_id") or str(uuid.uuid4())
        try:
            with live_model_settings(settings):
                result = await conversation.async_converse(
                    hass, text=data["text"], conversation_id=conversation_id,
                    context=self.context(request), language=data.get("language", "en"),
                    agent_id=data["agent_id"], device_id=data.get("device_id"),
                )
        except SessionBusyError as error:
            raise web.HTTPTooManyRequests(text="Too many active conversations; try again shortly") from error
        return self.json(result.as_dict())


class LiveActivityView(http.HomeAssistantView):
    url = "/api/special_agent/live/activity"
    name = "api:special_agent:live:activity"
    requires_auth = True

    async def get(self, request):
        try:
            if set(request.query) - {"cursor", "epoch", "limit"}:
                raise ValueError("Unsupported activity query")
            cursor = int(request.query.get("cursor", "0"))
            limit = int(request.query.get("limit", "200"))
            epoch = request.query.get("epoch")
            if epoch is not None and (len(epoch) > 128 or not _IDENTIFIER.fullmatch(epoch)):
                raise ValueError("Invalid activity epoch")
            data = read_activity(cursor=cursor, epoch=epoch, limit=limit)
        except ValueError as error:
            raise web.HTTPBadRequest(text="Invalid activity cursor, epoch, or limit") from error
        return self.json(data)
