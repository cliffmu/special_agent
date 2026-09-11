"""Placeholder conversation agent."""

from __future__ import annotations

import asyncio
import logging
import time
from homeassistant.components.conversation import (
    AbstractConversationAgent,
    ConversationEntity,
    ConversationResult,
)
from homeassistant.helpers import intent

from .agent_core import Agent
from . import DOMAIN
from .utils import performance
from .utils import logging as activity_log
from .utils.constants import DEFAULT_AGENT_MODEL

_LOGGER = logging.getLogger(__package__)


class SpecialAgentConversation(ConversationEntity, AbstractConversationAgent):
    """Minimal conversation agent stub."""

    def __init__(self, config_entry) -> None:
        super().__init__()
        self.config_entry = config_entry
        # Create agent with current config
        config = dict(config_entry.data)
        config.update(config_entry.options)
        self.agent = Agent(config)

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
        token = activity_log.begin_request()
        started = time.perf_counter()
        status = "error"
        error_type = None
        try:
            activity_log.activity("request", phase="received",
                                  source="satellite" if getattr(conversation_input, "device_id", None) else "text")
            result = await self._async_process(conversation_input, context)
            status = "completed"
            return result
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception as error:
            error_type = type(error).__name__
            raise
        finally:
            try:
                fields = {"phase": "finished", "status": status,
                          "elapsed_ms": round((time.perf_counter() - started) * 1000)}
                if error_type:
                    fields["error_type"] = error_type
                activity_log.activity("request", **fields)
            finally:
                activity_log.end_request(token)

    async def _async_process(self, conversation_input, context=None) -> ConversationResult:
        # Reuse loaded tools until options change. Existing requests retain their
        # own agent when a new configuration is installed.
        config = dict(self.config_entry.data)
        config.update(self.config_entry.options)
        if config != self.agent.config:
            self.agent = Agent(config)
        agent = self.agent
        
        user_text = getattr(conversation_input, "text", "")
        device_id = conversation_input.device_id or ""
        sess_key = (conversation_input.conversation_id, device_id)
        
        _LOGGER.debug("Agent config reload: model=%s, require_confirmation=%s", 
                     config.get("agent_model", DEFAULT_AGENT_MODEL),
                     config.get("require_confirmation", True))
        
        # Track entire request lifecycle with user prompt in metadata
        async with performance.track_request(
            "session",
            metadata={"prompt": user_text[:100]}  # First 100 chars
        ):
            result = await agent.plan(user_text, hass=self.hass, session_key=sess_key)
        
        # Write performance metrics after track_request completes (ensures session record is captured)
        if performance.is_enabled():
            record_count = len(performance.get_records())
            if record_count > 0:
                try:
                    await self.hass.async_add_executor_job(performance.write_csv)
                    _LOGGER.debug("Performance metrics written: %d records", record_count)
                except Exception as err:
                    _LOGGER.error("Failed to write performance metrics: %s", err, exc_info=True)

        mgr = self.hass.data[DOMAIN]["sessions"]

        if isinstance(result, dict) and "prompt_payload" in result:
            await mgr.save()
            
            # Track final response
            final_response = result["prompt_payload"]["speak"]
            performance.track_sync_operation(
                "end_session",
                metadata={"args": {"response": final_response}},
                status="ok"
            )
            
            service_domain = "assist_pipeline"
            service_name = "run"
            if device_id and self.hass.services.has_service(service_domain, service_name):
                await self.hass.services.async_call(
                    service_domain,
                    service_name,
                    {
                        "conversation_id": conversation_input.conversation_id,
                        "device_id": device_id,
                        "tts_input": final_response,
                        "start_stage": "tts",
                        "end_stage": "stt",
                    },
                    blocking=False,
                )
            else:
                _LOGGER.debug(
                    "Service %s.%s not available - voice pipeline continuation disabled",
                    service_domain, service_name
                )
            response = intent.IntentResponse(language=conversation_input.language)
            response.async_set_speech(final_response)
            return ConversationResult(
                conversation_id=conversation_input.conversation_id,
                response=response,
            )
        
        # Error or non-standard result path - still save session to preserve context
        await mgr.save()
        
        # Track final response (error case)
        final_response = str(result)
        performance.track_sync_operation(
            "end_session",
            metadata={"args": {"response": final_response}},
            status="error" if "error" in final_response.lower() else "ok"
        )
        
        response = intent.IntentResponse(language=conversation_input.language)
        response.async_set_speech(final_response)
        return ConversationResult(
            conversation_id=conversation_input.conversation_id,
            response=response,
        )


async def async_setup_entry(hass, config_entry, async_add_entities):
    agent = SpecialAgentConversation(config_entry)
    async_add_entities([agent])
    from homeassistant.components.conversation import async_set_agent

    async_set_agent(hass, config_entry, agent)
    
    # Log config for debugging
    config = dict(config_entry.data)
    config.update(config_entry.options)
    _LOGGER.debug("Special Agent conversation initialized with config: %s", 
                  {k: "***" if "key" in k or "secret" in k else v for k, v in config.items()})
    return True
