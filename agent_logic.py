# GET CURRENT STATE OF DEVICES FROM QUERY TO FEED TO LLM
# EX: IF MUSIC IS TO BE PLAYED AND SPEAKERS ARE MUTED OR VERY LOW VOLUME, THERE SHOULD BE ADDITIONAL COMMAND
# USER PREFERENCES INCORPORATION SOMEHOW, WHAT VOLUME LEVEL FOR SPEAKERS FOR EXAMPLE
# MAYBE CHECK HISTORY OF DEVICES IN HOME AND GENERATE PROFILE AUTOMATICALLY?
# IF PLAYLIST ISN'T MATCH USE LLM TO TRY AGAIN

import datetime

from .data_sources import get_ha_states, execute_ha_command, get_devices_by_area
from .vector_index import build_vector_index, query_vector_index, load_vector_index
from .gpt_commands import (
    classify_intent,
    ask_gpt_for_refined_query,
    ask_gpt_if_user_wants_music,
    ask_gpt_for_spotify_query,
    ask_gpt_for_rest_command,
    generate_user_friendly_confirmation
)
from .spotify_integration import get_spotify_access_token, search_spotify
from .logger_helper import log_to_file
from .entity_refinement import filter_irrelevant_entities, rerank_and_filter_docs
from .command_history import log_command

# Session timeout in seconds (5 minutes)
SESSION_TIMEOUT = 300

DOMAIN = "special_agent"

def check_and_cleanup_sessions(pending_dict):
    """
    Check for timed-out sessions and clean them up to prevent memory leaks
    """
    now = datetime.datetime.now()
    expired_sessions = []
    
    # Find all expired sessions
    for device_id, session in pending_dict.items():
        if "timestamp" in session:
            session_time = session["timestamp"]
            # Calculate session age in seconds
            age = (now - session_time).total_seconds()
            if age > SESSION_TIMEOUT:
                expired_sessions.append(device_id)
    
    # Remove expired sessions
    for device_id in expired_sessions:
        log_to_file(f"[AgentLogic] Cleaning up expired session for device_id='{device_id}'")
        pending_dict.pop(device_id, None)
    
    return len(expired_sessions)

def process_conversation_input(user_text, device_id, hass):
    """
    Updated flow with additional logging:
      1) Classify intent
      2) If 'control': 
         (a) music check -> possible Spotify URI
         (b) refine text -> short query
         (c) build index -> get top docs -> re-rank
         (d) build combined_context & log
         (e) call ask_gpt_for_rest_command -> parse JSON -> execute
      3) If 'weather' or 'question', placeholders
    """

    log_to_file(f"[AgentLogic] START process_conversation_input: device_id='{device_id}', user_text='{user_text}'")

    # Retrieve config
    config_entries = hass.data.get("special_agent", {})
    config_data = next(iter(config_entries.values())) if config_entries else {}
    openai_api_key = config_data.get("openai_api_key", "")
    spotify_client_id = config_data.get("spotify_client_id", "")
    spotify_client_secret = config_data.get("spotify_client_secret", "")

    # Get or create sessions dictionary and clean up expired sessions
    pending_dict = hass.data.setdefault("special_agent_pending", {})
    cleaned_count = check_and_cleanup_sessions(pending_dict)
    if cleaned_count > 0:
        log_to_file(f"[AgentLogic] Cleaned up {cleaned_count} expired sessions")
    
    # Check for existing session for this device
    pending = pending_dict.get(device_id)
    if pending and pending.get("status") == "awaiting_confirmation":
        return handle_confirmation_phase(user_text, hass, pending, device_id)

    # 1) Classify intent
    intent_type = classify_intent(user_text, api_key=openai_api_key)
    log_to_file(f"[AgentLogic] Intent => {intent_type}")  # NEW LOG

    if intent_type == "control":
        log_to_file("[AgentLogic] 'control' branch entered.")  # NEW LOG

        # REFINE RAG QUERY
        refined_text = ask_gpt_for_refined_query(user_text, api_key=openai_api_key)

        # CHECK IF USER WANTS MUSIC
        music_check = ask_gpt_if_user_wants_music(user_text, api_key=openai_api_key)
        spotify_uri = None
        if music_check == "true":
            refined_text += ", media_player, sonos"
            spotify_query = ask_gpt_for_spotify_query(user_text, api_key=openai_api_key)
            spotify_access_token = get_spotify_access_token(spotify_client_id, spotify_client_secret)
            spotify_uri = search_spotify(spotify_access_token, spotify_query)
            log_to_file(f"[AgentLogic] search_spotify => {spotify_uri}")  # NEW LOG

        # FIND RELATED DEVICES
        matrix, docs, dim = load_vector_index(openai_api_key)
        top_docs = query_vector_index((matrix, docs, dim), refined_text, k=50, openai_api_key=openai_api_key, hass=hass)
        final_docs = rerank_and_filter_docs(refined_text, top_docs, filter_qty=20)

        # BUILD COMBINED CONTEXT
        aggregated_context = []
        if spotify_uri:
            aggregated_context.append(f"The user wants music, please play on media player using spotify URI => {spotify_uri}")
        if len(final_docs) == 0:
            aggregated_context.append(
                "No devices matched. If the user wants to control a device, guess from context."
            )
        else:
            for doc in final_docs:
                snippet = doc["page_content"][:1000]  # access via dict key
                aggregated_context.append(snippet)
        combined_context = "\n\n".join(aggregated_context)
        log_to_file(f"[AgentLogic] combined_context (length={len(combined_context)}) => '{combined_context[:500]}'...")

        # (e) ask_gpt_for_rest_command -> parse JSON -> execute
        commands_json_str = ask_gpt_for_rest_command(user_text, combined_context, api_key=openai_api_key)

        # parse JSON
        import json
        try:
            commands_obj = json.loads(commands_json_str)
            if isinstance(commands_obj, dict):
                commands_list = [commands_obj]
            elif isinstance(commands_obj, list):
                commands_list = commands_obj
            else:
                # unknown format
                return None, False
            # if not isinstance(commands_list, list):
            #     log_to_file("[AgentLogic] GPT returned non-list JSON.")
            #     return None, False

            log_to_file(f"[AgentLogic] commands_list => {commands_list}")  # NEW LOG

            # Store pending commands in device-specific session
            timestamp = datetime.datetime.now()
            pending_dict[device_id] = {
                "commands_list": commands_list,
                "status": "awaiting_confirmation",
                "timestamp": timestamp,
                "entity_id": device_id
            }
            
            log_to_file(f"[AgentLogic] Created pending session for device_id='{device_id}' with {len(commands_list)} commands")
            
            # Generate a user-friendly confirmation message using LLM
            friendly_confirmation = generate_user_friendly_confirmation(
                user_text, 
                commands_list, 
                api_key=openai_api_key
            )
            
            # Log the command to history
            log_command(
                user_text=user_text,
                device_id=device_id,
                session_id=device_id,
                command_response=friendly_confirmation,
                commands_list=commands_list,
                success=None,  # Pending confirmation
                metadata={"status": "awaiting_confirmation"}
            )
            
            # Return the user-friendly confirmation prompt
            return friendly_confirmation, False


            # success_flag = True
            # for cmd in commands_list:
            #     # You might want more logging here
            #     log_to_file(f"[AgentLogic] About to execute cmd => {cmd}")  # NEW LOG
            #     ok = execute_ha_command(cmd, hass=hass)
            #     log_to_file(f"[AgentLogic] Command => {cmd}, success => {ok}")  # NEW LOG
            #     if not ok:
            #         success_flag = False

            # log_to_file("[AgentLogic] DONE with control flow.")
            # return commands_list, success_flag

        except Exception as e:
            log_to_file(f"[AgentLogic] JSON parse error => {e}")
            return None, False

    elif intent_type == "weather":
        log_to_file("[AgentLogic] Weather not implemented yet.")
        return "Weather not implemented", True
    elif intent_type == "question":
        log_to_file("[AgentLogic] Q&A not implemented yet.")
        return "Question not implemented", True
    elif intent_type == "rebuild_database":
        log_to_file("[AgentLogic] Rebuild requested => calling HA service in background.")
        hass.services.call(DOMAIN, "rebuild_database", {})
        return "Rebuilding database in the background...", True

        # log_to_file("[AgentLogic] Rebuilding database start")
        # message, success = rebuild_database(hass, openai_api_key=openai_api_key)
        # log_to_file("[AgentLogic] Rebuilding database done")
        # return message, success
    elif intent_type == "test":
        log_to_file("[AgentLogic] Test running.")
        # command, success = await self.hass.async_add_executor_job(
        #     process_conversation_input, user_text, context, self.hass
        # )

        return "Test done", True
    else:
        log_to_file("[AgentLogic] Unknown intent => no action.")
        return None, False

def sync_do_rebuild(hass):
    """
    Synchronous version of the rebuild function that can be called within the same thread.
    Less comprehensive than full async version but works for basic cases.
    """
    log_to_file("[AgentLogic] sync_do_rebuild: START")
    
    try:
        config_entries = hass.data.get("special_agent", {})
        config_data = next(iter(config_entries.values())) if config_entries else {}
        openai_api_key = config_data.get("openai_api_key", "")
        
        from .data_sources import get_ha_states
        from .entity_refinement import filter_irrelevant_entities
        from .vector_index import build_vector_index
        
        # 1) Get all states synchronously
        all_states = get_ha_states(hass)
        refined_states = filter_irrelevant_entities(all_states)
        log_to_file(f"[AgentLogic] sync_do_rebuild: got {len(refined_states)} refined states")
        
        # 2) Build docs
        docs = []
        for s in refined_states:
            content = f"Entity: {s['entity_id']}\nName: {s['name']}\nAttributes: {s['attributes']}\n"
            docs.append({"page_content": content, "metadata": {"entity_id": s["entity_id"]}})
        
        # 3) Build vector index
        result = build_vector_index(
            docs,
            openai_api_key=openai_api_key,
            force_rebuild=True
        )
        log_to_file("[AgentLogic] sync_do_rebuild: success")
        return "done"
    except Exception as e:
        log_to_file(f"[AgentLogic] sync_do_rebuild: error => {e}")
        return f"error: {e}"

async def do_full_rebuild(hass):
    """
    Async method that does the heavy lifting: get devices, states, filter, embed, etc.
    This is called from async_rebuild_database in __init__.py
    or we can import and call it from the service directly.
    """
    log_to_file("[AgentLogic] do_full_rebuild: START")

    config_entries = hass.data.get("special_agent", {})
    config_data = next(iter(config_entries.values())) if config_entries else {}
    openai_api_key = config_data.get("openai_api_key", "")

    try:
        # 1) get devices from data_sources
        from .data_sources import get_devices_by_area, get_ha_states
        from .entity_refinement import filter_irrelevant_entities
        from .vector_index import build_vector_index

        summary, devices = await get_devices_by_area(hass)
        log_to_file(f"[AgentLogic] got {len(devices)} devices")

        all_states = await hass.async_add_executor_job(get_ha_states, hass)
        
        # all_states = get_ha_states(hass)  # sync, but cheap
        refined_states = filter_irrelevant_entities(all_states)
        log_to_file(f"[AgentLogic] refined => {len(refined_states)} states")

        # 2) Build doc dict
        docs = []
        for s in refined_states:
            content = f"Entity: {s['entity_id']}\nName: {s['name']}\nAttributes: {s['attributes']}\n"
            docs.append({"page_content": content, "metadata": {"entity_id": s["entity_id"]}})

        # 3) Because "build_vector_index" might do blocking I/O (OpenAI calls),
        #    we run it in the executor:
        def sync_build_index():
            return build_vector_index(
                docs,
                openai_api_key=openai_api_key,
                force_rebuild=True,
                persist_dir=None
            )

        embedding_matrix, final_docs, dim = await hass.async_add_executor_job(sync_build_index)
        log_to_file(f"[AgentLogic] index built with shape {embedding_matrix.shape if embedding_matrix is not None else None}")

        # 4) Save summary, etc.
        import os, json
        base_dir = os.path.dirname(__file__)
        data_folder = os.path.join(base_dir, "data")
        os.makedirs(data_folder, exist_ok=True)
        summary_file = os.path.join(data_folder, "device_area_summary.json")
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump({"summary": summary, "devices": devices}, f, indent=2)

        log_to_file("[AgentLogic] do_full_rebuild: success")
        return "done"
    except Exception as e:
        log_to_file(f"[AgentLogic] do_full_rebuild: error => {e}")
        raise

def handle_confirmation_phase(user_text, hass, pending, device_id):
    """
    If user says "yes", we execute pending commands for this device.
    If "no", we discard them.
    """
    lowered = user_text.strip().lower()
    pending_dict = hass.data.setdefault("special_agent_pending", {})
    
    log_to_file(f"[AgentLogic] handle_confirmation_phase for device_id='{device_id}', user_text='{user_text}'")
    commands_list = pending.get("commands_list", [])

    if lowered in ["yes", "yep", "yeah", "sure", "go ahead"]:
        # Track successful and failed commands
        success_flag = True
        failed_cmds = []
        
        # Execute each command and record failures
        for cmd in commands_list:
            service_name = cmd.get("service", "unknown")
            entity_id = cmd.get("data", {}).get("entity_id", "unknown")
            ok = execute_ha_command(cmd, hass=hass)
            
            if not ok:
                success_flag = False
                failed_cmds.append(f"{service_name} for {entity_id}")

        # Clear the pending state for this device
        log_to_file(f"[AgentLogic] Executing commands and clearing session for device_id='{device_id}'")
        pending_dict.pop(device_id, None)
        
        # Generate appropriate response based on success/failure
        if success_flag:
            response = "Done."
        else:
            # Create a more friendly error message
            if len(failed_cmds) == len(commands_list):
                response = "Sorry, I couldn't complete any of the requested actions."
            else:
                response = f"Completed some actions, but had trouble with: {', '.join(failed_cmds[:2])}"
                if len(failed_cmds) > 2:
                    response += f" and {len(failed_cmds) - 2} more"
        
        # Log to command history
        log_command(
            user_text=user_text,
            device_id=device_id,
            session_id=device_id,
            command_response=response,
            commands_list=commands_list,
            success=success_flag,
            metadata={"status": "executed", "failed_commands": failed_cmds if not success_flag else []}
        )
        
        return (response, success_flag)

    elif lowered in ["no", "nope", "nah"]:
        # Discard the pending request
        log_to_file(f"[AgentLogic] Canceling request and clearing session for device_id='{device_id}'")
        pending_dict.pop(device_id, None)
        
        # Log to command history
        log_command(
            user_text=user_text,
            device_id=device_id,
            session_id=device_id,
            command_response="Request canceled.",
            commands_list=commands_list,
            success=None,
            metadata={"status": "canceled"}
        )
        
        return ("Request canceled.", True)
    else:
        # Unrecognized confirmation response
        log_to_file(f"[AgentLogic] Unrecognized confirmation response for device_id='{device_id}': '{user_text}'")
        
        return ("Please say yes or no.", False)

# def handle_confirmation_phase(user_text, hass, pending, device_id):
#     """
#     If the user_text is "yes", we finalize and run the pending commands.
#     If "no", we can discard or ask for clarifications.
#     Otherwise, do more advanced checks.
#     """
#     # Simplest approach: yes/no check
#     lowered = user_text.strip().lower()
#     pending_dict = hass.data.setdefault("special_agent_pending", {})
#     if lowered in ["yes", "yep", "yeah", "sure", "go ahead"]:
#         # Execute the pending commands
#         commands_list = pending["commands_list"]
#         success_flag = True
#         for cmd in commands_list:
#             ok = execute_ha_command(cmd, hass=hass)
#             if not ok:
#                 success_flag = False
        
#         # Clear the pending state
#         hass.data["special_agent"].pop("pending", None)
#         return ("Commands executed.", success_flag)

#     elif lowered in ["no", "nope", "nah"]:
#         # Discard the pending
#         hass.data["special_agent"].pop("pending", None)
#         return ("Alright, I'll cancel the request.", True)

#     else:
#         # Maybe the user asked for partial changes or more info
#         # For now, we just ask them for a yes/no again:
#         return ("Sorry, please say 'yes' or 'no'.", False)



# WORKING 2/15/25 11pm
# from .data_sources import get_ha_states, execute_ha_command
# from .vector_index import build_vector_index, query_vector_index
# from .gpt_commands import (classify_intent, ask_gpt_for_rest_command, ask_gpt_for_spotify_query)
# from .spotify_integration import get_spotify_access_token, search_spotify
# from .logger_helper import log_to_file
# from .entity_refinement import (filter_irrelevant_entities, rerank_and_filter_docs, refine_sub_entities)

# def process_conversation_input(user_text, context, hass):
#     log_to_file(f"[AgentLogic] Started processing input: {user_text}")
#     """
#     Primary orchestrator for the 'special_agent':
#       1) Retrieve HA states
#       2) Filter out unneeded sub-entities
#       3) Build or load vector index
#       4) Query vector index
#       5) Re-rank docs
#       6) Classify intent
#       7) If 'spotify', generate music command
#          else, pass final docs to GPT for an HA command
#       8) (Optional) refine sub-entities if advanced controls are indicated
#       9) Execute the command
#     """

#     # Retrieve config from hass
#     config_entries = hass.data.get("special_agent", {})
#     config_data = next(iter(config_entries.values())) if config_entries else {}
#     openai_api_key = config_data.get("openai_api_key", "")
#     spotify_client_id = config_data.get("spotify_client_id", "")
#     spotify_client_secret = config_data.get("spotify_client_secret", "")

#     # 1) Get all states
#     all_ha_states = get_ha_states(hass)
    
#     # 2) Filter out obviously irrelevant items
#     primary_states = filter_irrelevant_entities(all_ha_states)

#     # 3) Build or load vector index
#     index_data = build_vector_index(primary_states, openai_api_key=openai_api_key)

#     # 4) Query index for top matches
#     top_docs = query_vector_index(index_data, user_text, k=50, openai_api_key=openai_api_key)
    
#     # 5) Re-rank
#     final_docs = rerank_and_filter_docs(user_text, top_docs)
    
#     # 6) Classify intent
#     intent_type = classify_intent(user_text, api_key=openai_api_key)

#     # 7) Generate command
#     if intent_type == "spotify":
#         spotify_query = ask_gpt_for_spotify_query(user_text, api_key=openai_api_key)
#         spotify_access_token = get_spotify_access_token(spotify_client_id, spotify_client_secret)
#         spotify_uri = search_spotify(spotify_access_token, spotify_query)

#     else:
#         # General HA command
#         log_to_file("[AgentLogic] Generating HA command.")

#         # combined_context to include if music should be played, spotify URI, and devices with atrributes
#         # command = ask_gpt_for_rest_command(user_text, combined_context)
#         # log_to_file(f"[AgentLogic] Generated HA command: {command}")

#         # Hardcoded HA Command
#         command = {
#             "service": "light.turn_on",
#             "data": {
#                 "entity_id": "light.office_outdoor_spotlight_left",
#                 "hs_color": [39, 100]  # approx. orange
#             }
#         }
#         log_to_file(f"[AgentLogic] Overriding with HARD-CODED command")
#         # -------------------------------------------------------------------------------

#     # 8) Execute
#     execution_success = execute_ha_command(command, hass=hass)
#     log_to_file(f"[AgentLogic] Command execution returned: {execution_success}")
#     log_to_file("[AgentLogic] Finished processing input.")
#     return command, execution_success














        # # (Optional) refine sub-entities if advanced controls are requested
        # best_doc = final_docs[0] if final_docs else None
        # sub_entities = []
        # if best_doc:
        #     sub_entities = refine_sub_entities(user_text, best_doc.metadata["entity_id"], all_ha_states, openai_api_key=openai_api_key)
        #     log_to_file(f"[AgentLogic] refine_sub_entities found {len(sub_entities)} advanced items.")

        # Combine all final docs + sub-entities into a single context
        # aggregated_context = []
        # for doc in final_docs:
        #     aggregated_context.append(doc.page_content)
        # for se in sub_entities:
        #     snippet = (
        #         f"SubEntity: {se['entity_id']}\n"
        #         f"Name: {se.get('name', '')}\n"
        #         f"State: {se.get('state', '')}\n"
        #         f"Attributes: {se.get('attributes', {})}\n"
        #     )
        #     aggregated_context.append(snippet)

        # combined_context = "\n\n".join(aggregated_context)
        # command = ask_gpt_for_rest_command(user_text, combined_context)








# WORKING DECENT 2/14 11:59pm
# from .data_sources import get_ha_states
# from .vector_index import build_vector_index, query_vector_index
# from .gpt_commands import (classify_intent, ask_gpt_for_rest_command, execute_ha_command, ask_gpt_for_spotify_query)
# from .spotify_integration import get_spotify_access_token, search_spotify
# from .logger_helper import log_to_file
# from .entity_refinement import (filter_irrelevant_entities, rerank_and_filter_docs, refine_sub_entities)


# def process_conversation_input(user_text, context, hass):
#     """
#     High-level logic flow:
#       1) Retrieve HA states.
#       2) Filter out unneeded items (e.g., LED param controls) for main index.
#       3) Build & query vector index for top matches.
#       4) Re-rank or filter those results (prioritize relevant domains, location, etc.).
#       5) Classify intent using GPT.
#          - If Spotify, do music logic.
#          - Otherwise, generate HA command using final device docs.
#       6) (Optional) refine sub-entities if advanced controls are requested.
#       7) Execute the HA command.
#       8) Return the command + success status.
#     """
#     log_to_file(f"[AgentLogic] Started processing input: {user_text}")

#     # Retrieve config
#     config_entries = hass.data.get("special_agent", {})
#     config_data = next(iter(config_entries.values())) if config_entries else {}
#     openai_api_key = config_data.get("openai_api_key", "")
#     spotify_client_id = config_data.get("spotify_client_id", "")
#     spotify_client_secret = config_data.get("spotify_client_secret", "")

#     # 1) Get device states
#     all_ha_states = get_ha_states(hass)
#     log_to_file(f"[AgentLogic] Retrieved {len(all_ha_states)} exposed HA device states.")

#     # 2) Filter out known irrelevant sub-entities
#     primary_states = filter_irrelevant_entities(all_ha_states)
#     log_to_file(
#         f"[AgentLogic] Filtered out {len(all_ha_states)-len(primary_states)} sub-entities. "
#         f"Indexing {len(primary_states)} entities in the main vector index."
#     )

#     # 3) Build or load vector index
#     index_data = build_vector_index(primary_states, openai_api_key=openai_api_key)
#     if index_data is None:
#         log_to_file("[AgentLogic] Error: Vector index could not be built.")
#         return None, None

#     # Query the vector index
#     # We'll ask for top 30 matches, so we have enough to re-rank
#     top_docs = query_vector_index(index_data, user_text, k=50, openai_api_key=openai_api_key)
#     log_to_file(f"[AgentLogic] Vector index query returned {len(top_docs)} documents.")

#     # 4) Re-rank and filter those docs based on domain & location relevance
#     final_docs = rerank_and_filter_docs(user_text, top_docs)
#     log_to_file(f"[AgentLogic] Re-ranked docs down to {len(final_docs)} final docs for GPT")

#     # 5) Classify intent
#     intent_type = classify_intent(user_text, api_key=openai_api_key)
#     log_to_file(f"[AgentLogic] Intent classified as: {intent_type}")

#     if intent_type == "spotify":
#         # 5A) If user wants Spotify/music
#         log_to_file("[AgentLogic] Generating Spotify command.")
#         spotify_query = ask_gpt_for_spotify_query(user_text, api_key=openai_api_key)
#         log_to_file(f"[AgentLogic] Spotify query returned from GPT: {spotify_query}")

#         # Do the search
#         spotify_access_token = get_spotify_access_token(spotify_client_id, spotify_client_secret)
#         spotify_uri = search_spotify(spotify_access_token, spotify_query)

#         command = f"Spotify search query: {spotify_query} | URI: {spotify_uri}"
#         log_to_file(f"[AgentLogic] Generated Spotify command: {command}")

#     else:
#         # 5B) General HA command
#         log_to_file("[AgentLogic] Generating HA command.")

#         # 6) (Optional) refine sub-entities if advanced request (led, bass, night mode, etc.)
#         # For demonstration, we refine only the single top doc if we suspect advanced controls
#         best_doc = final_docs[0] if final_docs else None
#         sub_entities = []
#         if best_doc:
#             parent_eid = best_doc.metadata.get("entity_id")
#             # refine_sub_entities will check if it's advanced and do embedding among device siblings
#             sub_entities = refine_sub_entities(
#                 user_text, parent_eid, all_ha_states, openai_api_key=openai_api_key
#             )
#             log_to_file(f"[AgentLogic] refine_sub_entities found {len(sub_entities)} possible advanced entities.")

#         # Build a combined context from top final_docs
#         # This ensures GPT sees multiple devices, so it can coordinate them
#         aggregated_context = []
#         for doc in final_docs:
#             aggregated_context.append(doc.page_content)
#         # Include sub-entities in context if relevant
#         for se in sub_entities:
#             text_snippet = (
#                 f"SubEntity: {se['entity_id']}\n"
#                 f"Name: {se.get('name')}\n"
#                 f"State: {se.get('state')}\n"
#                 f"Attributes: {se.get('attributes')}\n"
#             )
#             aggregated_context.append(text_snippet)

#         full_context_for_gpt = "\n\n".join(aggregated_context)

#         # Ask GPT for the final HA command
#         log_to_file(f"[AgentLogic] Full context for gpt: {full_context_for_gpt}")
#         command = ask_gpt_for_rest_command(user_text, full_context_for_gpt)
#         log_to_file(f"[AgentLogic] Generated HA command: {command}")

#     # 7) Execute
#     execution_success = execute_ha_command(command)
#     log_to_file(f"[AgentLogic] Command execution returned: {execution_success}")
#     log_to_file("[AgentLogic] Finished processing input.")

#     return command, execution_success



# from .data_sources import get_ha_states
# from .vector_index import build_vector_index, query_vector_index
# from .gpt_commands import classify_intent, ask_gpt_for_rest_command, execute_ha_command, ask_gpt_for_spotify_query
# from .spotify_integration import get_spotify_access_token, search_spotify
# from .logger_helper import log_to_file

# def process_conversation_input(user_text, context, hass):
#     log_to_file(f"[AgentLogic] Started processing input: {user_text}")

#     # GET API KEYS from hass.data
#     config_entries = hass.data.get("special_agent", {})
#     config_data = next(iter(config_entries.values())) if config_entries else {}
#     openai_api_key = config_data.get("openai_api_key", "")
#     spotify_client_id = config_data.get("spotify_client_id", "")
#     spotify_client_secret = config_data.get("spotify_client_secret", "")

#     # Retrieve HA device states
#     ha_states = get_ha_states(hass)
#     log_to_file(f"[AgentLogic] Retrieved {len(ha_states)} exposed HA device states.")

#     # Build vector index (pass the API key)
#     index_data = build_vector_index(ha_states, openai_api_key=openai_api_key)
#     if index_data is None:
#         log_to_file("[AgentLogic] Error: Vector index could not be built.")
#         return None, None

#     # Query the vector index (pass the API key)
#     relevant_context = query_vector_index(index_data, user_text, openai_api_key=openai_api_key)
#     log_to_file(f"[AgentLogic] Vector index query returned: {relevant_context}")

#     # Classify intent using GPT
#     intent_type = classify_intent(user_text, api_key=openai_api_key)
#     log_to_file(f"[AgentLogic] Intent classified as: {intent_type}")

#     if intent_type == "spotify":
#         log_to_file("[AgentLogic] Generating Spotify command.")
#         spotify_query = ask_gpt_for_spotify_query(user_text, api_key=openai_api_key)
#         log_to_file(f"[AgentLogic] Spotify query returned from GPT: {spotify_query}")
#         spotify_access_token = get_spotify_access_token(spotify_client_id, spotify_client_secret)
#         spotify_uri = search_spotify(spotify_access_token, spotify_query)
#         command = f"Spotify search query: {spotify_query} | URI: {spotify_uri}"
#         log_to_file(f"[AgentLogic] Generated Spotify command: {command}")
#     else:
#         log_to_file("[AgentLogic] Generating HA command.")
#         command = ask_gpt_for_rest_command(user_text, relevant_context)
#         log_to_file(f"[AgentLogic] Generated HA command: {command}")

#     execution_success = execute_ha_command(command)
#     log_to_file(f"[AgentLogic] Command execution returned: {execution_success}")
#     log_to_file("[AgentLogic] Finished processing input.")
#     return command, execution_success
