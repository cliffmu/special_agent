import logging
import datetime
from homeassistant.components.conversation import (
    AbstractConversationAgent,
    ConversationEntity,
    ConversationResult,
)
from homeassistant.helpers import intent
from .agent_logic import process_conversation_input
from .logger_helper import log_to_file
from .command_history import log_command

_LOGGER = logging.getLogger(__name__)

class TestConversationAgent(ConversationEntity, AbstractConversationAgent):
    """Conversation agent that processes user input and logs details to a file."""

    @property
    def unique_id(self):
        return "special_agent_unique_id"

    @property
    def name(self):
        return "Special Agent"

    @property
    def available(self):
        return True

    @property
    def state(self):
        return "active"

    @property
    def supported_languages(self):
        return ["en"]

    @property
    def use_device_area(self):
        return True

    @property
    def supported_features(self):
        from homeassistant.components.conversation import ConversationEntityFeature
        return ConversationEntityFeature.CONTROL

    async def async_get_intents(self):
        log_to_file("[Conversation] async_get_intents called.")
        return {
            "default": {
                "name": "default",
                "description": "Default fallback intent for Special Agent.",
                "examples": ["hi", "what's the weather", "play a song"]
            }
        }

    async def async_handle(self, intent_obj, conversation_input, context):
        log_to_file("[Conversation] async_handle called.")
        return await self.async_process(conversation_input, context)


    async def async_process(self, conversation_input, context=None) -> ConversationResult:
        """Main entry point when the user speaks to this conversation device."""
        user_text = conversation_input.text
        
        # Get the conversation_id from the context if available (for multi-turn conversations)
        conversation_id = getattr(context, "conversation_id", None) if context else None
        
        # Get metadata from conversation_input
        metadata = getattr(conversation_input, "metadata", {})
        
        # Extract all potential device identifiers
        source_entity_id = getattr(conversation_input, "source_entity_id", None)
        conversation_id_input = getattr(conversation_input, "conversation_id", None)
        device_id_input = getattr(conversation_input, "device_id", None)
        context_id = getattr(context, "id", None) if context else None
        
        # Look for device info in metadata
        metadata_device_id = None
        if isinstance(metadata, dict):
            # Common device identifiers in Home Assistant metadata
            metadata_device_id = metadata.get("device_id") or metadata.get("device") or metadata.get("source_device")
        
        # If we have metadata, attempt to build a rich device identifier
        if isinstance(metadata, dict) and len(metadata) > 0:
            # Create a device fingerprint from available metadata
            device_parts = []
            # Look for common device identifier fields
            for field in ["device_id", "device", "source_device", "entity_id", "source", "room", "area"]:
                if field in metadata and metadata[field]:
                    device_parts.append(f"{field}:{metadata[field]}")
            
            # If we found device parts, join them to create a unique fingerprint
            if device_parts:
                combined_device_id = "|".join(device_parts)
                # Use this as our primary device identifier
                metadata_device_id = combined_device_id
        
        # Try multiple possible identifiers in order of preference
        # 1. combined metadata device fingerprint or simple device_id (likely the most specific)
        # 2. conversation_id from input (unique per conversation)
        # 3. device_id from input (if available)
        # 4. source_entity_id (if available)
        # 5. context id (if available)
        # 6. self.entity_id (fallback, same for all instances)
        device_id = metadata_device_id or conversation_id_input or device_id_input or source_entity_id or context_id or self.entity_id
        
        # If we still got the default entity_id (which is the same for all instances),
        # try to make it unique by combining with the conversation_id
        if device_id == self.entity_id and conversation_id:
            device_id = f"{device_id}|{conversation_id}"

        try:
            # Record start time for performance logging
            start_time = datetime.datetime.now()
            
            # Because process_conversation_input is synchronous, run it in the executor
            command, success = await self.hass.async_add_executor_job(
                process_conversation_input, user_text, device_id, self.hass
            )
            
            # Calculate processing time
            processing_time = (datetime.datetime.now() - start_time).total_seconds()
            
            if command:
                # Just use the command response directly without adding status text
                response_text = str(command)
            else:
                response_text = "I couldn't process that request."
                
            # Only log commands that aren't confirmations or part of the session flow
            # Commands are logged separately in agent_logic.py during confirmation/execution
            # This only logs initial commands and errors
            if not any(word in user_text.lower() for word in ["yes", "no", "yep", "nope", "yeah", "sure", "proceed"]):
                # Get metadata about the request
                metadata = {
                    "processing_time": processing_time,
                    "source_entity": source_entity_id,
                    "conversation_id": conversation_id
                }
                
                # Log to command history (for initial commands)
                log_command(
                    user_text=user_text,
                    device_id=device_id,
                    session_id=device_id,
                    command_response=response_text,
                    success=success,
                    metadata=metadata
                )
                
        except Exception as e:
            _LOGGER.error("Error processing input '%s': %s", user_text, e, exc_info=True)
            response_text = f"Sorry, I encountered an error processing that request."
            
            # Log the error
            log_command(
                user_text=user_text,
                device_id=device_id,
                session_id=device_id,
                command_response=response_text,
                success=False,
                metadata={"error": str(e)}
            )

        result = intent.IntentResponse(language=conversation_input.language)
        result.async_set_speech(response_text)
        
        # Ensure we maintain the conversation_id for multi-turn conversations
        # Use the device-specific conversation ID if we have one from the input,
        # otherwise use the context one
        response_conversation_id = conversation_id_input or conversation_id
        
        # Pass conversation_id to maintain session across interactions
        return ConversationResult(conversation_id=response_conversation_id, response=result)

        # user_text = conversation_input.text
        # log_to_file(f"[Conversation] Received user text: {user_text}")

        # try:
        #     # Because process_conversation_input is synchronous
        #     command, success = await self.hass.async_add_executor_job(
        #         process_conversation_input, user_text, context, self.hass
        #     )
        #     if command:
        #         response_text = f"{command}"
        #         if success:
        #             response_text += " (executed successfully)"
        #         else:
        #             response_text += " (execution failed)"
        #     else:
        #         response_text = "No command generated."
        # except Exception as e:
        #     _LOGGER.error("Error processing input '%s': %s", user_text, e, exc_info=True)
        #     response_text = f"Error: {e}"

        # result = intent.IntentResponse(language=conversation_input.language)
        # result.async_set_speech(response_text)
        # return ConversationResult(conversation_id=None, response=result)
    
    @property
    def device_info(self):
        return {
            "identifiers": {(self.unique_id,)},
            "name": self.name,
            "manufacturer": "Custom",
            "model": "Special Agent",
        }

async def async_setup_entry(hass, config_entry, async_add_entities):
    log_to_file("[Conversation] async_setup_entry called.")
    agent = TestConversationAgent()
    async_add_entities([agent])
    from homeassistant.components.conversation import async_set_agent
    async_set_agent(hass, config_entry, agent)
    log_to_file("[Conversation] Special Agent setup complete.")
    return True






    # async def async_process(self, conversation_input, context=None):
    #     user_text = conversation_input.text

    #     try:
    #         command, success = await self.hass.async_add_executor_job(
    #             process_conversation_input, user_text, context, self.hass
    #         )
    #         if command:
    #             response_text = f"Generated command: {command}"
    #             response_text += " (executed successfully)" if success else " (execution failed)"
    #         else:
    #             response_text = "No command generated."
        
    #     except Exception as e:
    #         response_text = f"Sorry, error occurred: {e}"
        
    #     from homeassistant.helpers import intent
    #     response = intent.IntentResponse(language=conversation_input.language)
    #     response.async_set_speech(response_text)
    #     from homeassistant.components.conversation import ConversationResult
    #     return ConversationResult(conversation_id=None, response=response)


    # async def async_process(self, conversation_input, context=None):
    #     user_text = conversation_input.text
    #     log_to_file(f"[Conversation] Received user text: {user_text}")
    #     try:
    #         log_to_file("[Conversation] Calling process_conversation_input.")
    #         # Pass self.hass to business logic so that we can retrieve device states.
    #         command, success = await self.hass.async_add_executor_job(
    #             process_conversation_input, user_text, context, self.hass
    #         )

    #         response_text = f"Generated command: {command}"
    #         response_text += " (executed successfully)" if success else " (execution failed)"
    #         log_to_file(f"[Conversation] process_conversation_input returned: {command}, success={success}")
    #     except Exception as e:
    #         _LOGGER.error("Error processing input '%s': %s", user_text, e)
    #         response_text = "Sorry, I encountered an error processing your request."
    #         command = f"Error: {e}"
    #         log_to_file(f"[Conversation] Error occurred: {e}")

    #     log_to_file(f"[Conversation] User: {user_text} | Response: {response_text}")

    #     response = intent.IntentResponse(language=conversation_input.language)
    #     response.async_set_speech(response_text)
    #     return ConversationResult(conversation_id=None, response=response)



    # async def async_process(self, conversation_input, context=None) -> ConversationResult:
    #     """Handle an incoming conversation input from Home Assistant's conversation system."""
    #     user_text = conversation_input.text
    #     log_to_file(f"[Conversation] Received user text: {user_text}")

    #     try:
    #         # 1. Gather device info
    #         summary_dict, devices_list = await get_devices_by_area(self.hass)
    #         log_to_file(f"[Conversation] Found {len(devices_list)} devices in registry.")

    #         # 2. Optionally do more advanced logic or pass to your LLM
    #         #    For example, if you have a function `process_conversation_input`
    #         #    that is synchronous, you'd do something like:
    #         # command, success = await self.hass.async_add_executor_job(
    #         #     process_conversation_input, user_text, devices_list, summary_dict
    #         # )
    #         #
    #         # If it's async, just do:
    #         # command, success = await process_conversation_input(user_text, devices_list, summary_dict)

    #         # For demonstration, let's assume you simply return the number of devices
    #         response_text = f"Found {len(devices_list)} devices. Summary by area/domain: {summary_dict}"
    #         log_to_file(response_text)

    #         # You can generate an HA conversation result
    #         response = intent.IntentResponse(language=conversation_input.language)
    #         response.async_set_speech(response_text)

    #         return ConversationResult(conversation_id=None, response=response)

    #     except Exception as e:
    #         _LOGGER.error("Error processing input '%s': %s", user_text, e, exc_info=True)
    #         # Return a fallback error response
    #         response = intent.IntentResponse(language=conversation_input.language)
    #         response.async_set_speech("Sorry, an error occurred.")
    #         return ConversationResult(conversation_id=None, response=response)