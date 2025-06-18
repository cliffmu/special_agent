"""Placeholder conversation agent."""

from __future__ import annotations

import logging
from homeassistant.components.conversation import (
    AbstractConversationAgent,
    ConversationEntity,
    ConversationResult,
)
from homeassistant.helpers import intent

from .agent_core import Agent
from . import DOMAIN

_LOGGER = logging.getLogger(__package__)


class SpecialAgentConversation(ConversationEntity, AbstractConversationAgent):
    """Minimal conversation agent stub."""

    def __init__(self) -> None:
        super().__init__()
        self.agent = Agent()

    @property
    def unique_id(self) -> str:
        return "special_agent"

    @property
    def name(self) -> str:
        return "Special Agent"

    @property
    def supported_languages(self) -> list[str]:
        return ["en"]

    async def async_get_intents(self) -> dict:
        return {
            "default": {
                "name": "default",
                "description": "Default intent",
                "examples": ["hi"],
            }
        }

    async def async_handle(self, intent_obj, conversation_input, context):
        return await self.async_process(conversation_input, context)

    async def async_process(
        self, conversation_input, context=None
    ) -> ConversationResult:
        user_text = getattr(conversation_input, "text", "")
        device_id = conversation_input.device_id or ""
        sess_key = (conversation_input.conversation_id, device_id)
        result = await self.agent.plan(user_text, hass=self.hass, session_key=sess_key)

        mgr = self.hass.data[DOMAIN]["sessions"]

        if isinstance(result, dict) and "prompt_payload" in result:
            await mgr.save()
            service_domain = "assist_pipeline"
            service_name = "run"
            if self.hass.services.has_service(service_domain, service_name):
                await self.hass.services.async_call(
                    service_domain,
                    service_name,
                    {
                        "conversation_id": conversation_input.conversation_id,
                        "device_id": device_id,
                        "tts_input": result["prompt_payload"]["speak"],
                        "start_stage": "tts",
                        "end_stage": "stt",
                    },
                    blocking=False,
                )
            else:
                _LOGGER.error(
                    "Service %s.%s not found", service_domain, service_name
                )
            response = intent.IntentResponse(language=conversation_input.language)
            response.async_set_speech(result["prompt_payload"]["speak"])
            return ConversationResult(
                conversation_id=conversation_input.conversation_id,
                response=response,
            )
        response = intent.IntentResponse(language=conversation_input.language)
        response.async_set_speech(str(result))
        return ConversationResult(
            conversation_id=conversation_input.conversation_id,
            response=response,
        )


async def async_setup_entry(hass, config_entry, async_add_entities):
    agent = SpecialAgentConversation()
    async_add_entities([agent])
    from homeassistant.components.conversation import async_set_agent

    async_set_agent(hass, config_entry, agent)
    _LOGGER.debug("Special Agent conversation initialized")
    return True
