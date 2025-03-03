from openai import OpenAI
from .logger_helper import log_to_file

def classify_intent(user_text, api_key=None):
    """
    Return ONLY one of the following:
      'control', 'question', or 'weather'
    """
    if api_key:
        try:
            client = OpenAI(api_key=api_key)
            system_prompt = (
                "Analyze the following user text. "
                "Return exactly one of these words in lowercase: 'control', 'question', 'weather', 'rebuild_database', 'test'. "
                "No other text."
            )
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text}
                ],
                temperature=0.0,
            )
            classification = completion.choices[0].message.content.strip().lower()
            log_to_file(f"[GPTCommands] classify_intent => {classification}")
            # validate
            if classification not in ["control", "question", "weather", "test", "rebuild_database"]:
                classification = "test"
            return classification
        except Exception as e:
            log_to_file(f"[GPTCommands] classify_intent error: {e}. Falling back to 'test'.")
            return "test"

    # fallback
    text = user_text.lower()
    # naive logic, e.g. if "weather" in text => ...
    if "weather" in text or "temperature" in text:
        return "weather"
    elif "?" in text or "what" in text:
        return "question"
    else:
        return "control"


def ask_gpt_for_refined_query(user_text, api_key=None):
    """
    Given the user text, extract keywords or short phrase to help with vector search.
    Return only the short phrase or minimal text, no extra commentary.
    """
    if api_key:
        try:
            client = OpenAI(api_key=api_key)
            system_prompt = (
                "Extract the essential keywords from the user's request to find relevant devices with keyword search. "
                "The most important keyword to search for is room name (office, living room, dining room, bedroom, kitchen). "
                "Do not include adjectives, focus on nouns."
                "Focus on device type (light, fan, media_player, climate, switch). "
                "If user is being vague describing a scene then provide keywords "
                "which could achieve the intent of the user. Return only a short phrase in lowercase."
            )
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text}
                ],
                temperature=0.0,
            )
            refined = completion.choices[0].message.content.strip().lower()
            log_to_file(f"[GPTCommands] refined query => {refined}")
            return refined
        except Exception as e:
            log_to_file(f"[GPTCommands] ask_gpt_for_refined_query error: {e}. Using original text.")
            return user_text
    # fallback if no API
    return user_text


def ask_gpt_if_user_wants_music(user_text, api_key=None):
    """
    Return 'true' or 'false' if the user might want music.
    """
    if api_key:
        try:
            client = OpenAI(api_key=api_key)
            system_prompt = (
                "Decide if the user's command implies or would benefit from playing music. "
                "Return 'true' or 'false' only, no extra text."
            )
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text}
                ],
                temperature=0.0,
            )
            classification = completion.choices[0].message.content.strip().lower()
            log_to_file(f"[GPTCommands] ask_gpt_if_user_wants_music => {classification}")
            if classification in ["true", "false"]:
                return classification
            return "false"
        except Exception as e:
            log_to_file(f"[GPTCommands] ask_gpt_if_user_wants_music error: {e}. Using 'false'.")
            return "false"
    # fallback
    return "false"


def ask_gpt_for_spotify_query(prompt, api_key=None):
    """
    Use the LLM to generate a refined Spotify search query from the user prompt.
    """
    log_to_file("[GPTCommands] Generating Spotify command.")
    system_prompt = """
        Based on the user's prompt, generate a concise Spotify search query using field filters.
        Use only 'track:', 'album:', or 'playlist:' as needed.
        Return only the query, no other text. Never return an artist, instead if the user wants music from a specific artist find a playlist or album, unless the user requests a specific song from that artist then the artist should a filter and not the primary search term.
    """
    if api_key:
        try:
            client = OpenAI(api_key=api_key)
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.0,
            )
            spotify_query = completion.choices[0].message.content.strip()
            log_to_file(f"[GPTCommands] Generated Spotify query => {spotify_query}")
            return spotify_query
        except Exception as e:
            log_to_file(f"[GPTCommands] ask_gpt_for_spotify_query error: {e}. Using raw prompt.")
            return prompt
    return prompt

def ask_gpt_for_rest_command(user_text, context, api_key=None):
    """
    Use an LLM to generate a list of Home Assistant commands in JSON format.
    The LLM should return ONLY valid JSON. Example:

    [
      {
        "service": "light.turn_on",
        "data": {
          "entity_id": "light.office_lamp",
          "hs_color": [39, 100]
        }
      },
      ...
    ]

    We'll parse this JSON in agent_logic and call each command.
    """
    if not api_key:
        # fallback: just return empty JSON
        return "[]"

    client = OpenAI(api_key=api_key)
    system_prompt = (
        "You are a Home Assistant command generator. "
        "The user wants to perform some action. We also have the following device info:\n"
        f"{context}\n"
        "Output a JSON array of commands. Always return an array even if there is only 1 item. "
        "Each command is an object see example of desired output below:\n"
        '[\n'
        '   {\n'
        '       "service": "light.turn_on",\n'
        '       "data": {\n'
        '           "entity_id": "light.office_outdoor_spotlight_left",\n'
        '           "hs_color": [39, 100]\n'
        '       }\n'
        '   },\n'
        '   {\n'
        '       "service": "media_player.play_media",\n'
        '       "data": {\n'
        '          "entity_id": "media_player.kitchen_sonos", \n'
        '           "media_content_id": "spotify:playlist:6Jk1rXWdpLQaMiWaM9Tjor",\n'
        '           "media_content_type": "music",\n'
        '          "enqueue": "replace"\n'
        '       }\n'
        '   }\n'
        ']\n'
        "If the user wants multiple devices changed, output multiple items in the array. "
        "If any color/brightness/temperature is implied by user, set them. "
        "Use numeric arrays for color. If domain is climate, use 'temperature', etc. "
        "If the user requests music only use the provided spotify URI, do not make one up. "
        "IMPORTANT: Return ONLY valid JSON, no extra text or code fences or commented out text."
    )
    # system_prompt = (f"Name all the types of devices shared: {context}")
    log_to_file(f"[GPTCommands] system prompt: {system_prompt}")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text}
    ]
    # log_to_file(f"[GPTCommands] messages: {messages}")
    try:
        completion = client.chat.completions.create(
            model="o3-mini",
            messages=messages
        )
        # completion = client.chat.completions.create(
        #     model="gpt-4o",
        #     messages=messages,
        #     temperature=0.2,
        #     response_format={ "type": "json_object" }
        # )
        
        commands_json = completion.choices[0].message.content.strip()
        log_to_file(f"[GPTCommands] ask_gpt_for_rest_command => {commands_json}")
        return commands_json
    except Exception as e:
        log_to_file(f"[GPTCommands] ask_gpt_for_rest_command error => {e}")
        # fallback to empty array
        return "[]"

# def ask_gpt_for_rest_command(user_text, context):
#     """
#     Generate a REST command for Home Assistant. Return a minimal text or JSON that describes
#     the service calls to be executed. For now, it's a dummy function.
#     """
#     result = f"HA command based on '{user_text}' with context:\n{context}"
#     log_to_file(f"[GPTCommands] ask_gpt_for_rest_command => {result}")
#     return result


# from openai import OpenAI
# from .logger_helper import log_to_file

# def classify_intent(user_text, api_key=None):
#     """
#     Classify intent based on user text.
#     If an OpenAI API key is provided, use the new ChatCompletion interface with model "gpt-4o-mini".
#     Otherwise, fall back to dummy classification.
#     """

#     if api_key:
#         try:
#             client = OpenAI(api_key=api_key)
#             system_prompt = (
#                 "Classify the following input as either 'spotify' (for music-related commands) or 'general' for other commands."
#             )
#             completion = client.chat.completions.create(
#                 model="gpt-4o-mini",
#                 messages=[
#                     {"role": "system", "content": system_prompt},
#                     {"role": "user", "content": user_text }
#                 ],
#                 temperature=0.0,
#             )
#             log_to_file(f"[GPTCommands] Full API response: {completion}")
#             classification = completion.choices[0].message.content.strip().lower()
#             log_to_file(f"[GPTCommands] classify_intent: classification = {classification}")
#             return "spotify" if "spotify" in classification else "general"
#         except Exception as e:
#             log_to_file(f"[GPTCommands] OpenAI API error: {e}. Falling back to dummy classification.")

#     # Fallback dummy classification
#     text = user_text.lower()
#     result = "spotify" if any(word in text for word in ["play", "song", "music"]) else "general"
#     log_to_file(f"[GPTCommands] classify_intent (dummy): result = {result}")
#     return result

# def ask_gpt_if_user_wants_music(user_text, api_key=None):
#     """
#     Classify if user wants music based on user text.
#     If an OpenAI API key is provided, use the new ChatCompletion interface with model "gpt-4o-mini".
#     Otherwise, fall back to dummy classification.
#     """
#     if api_key:
#         try:
#             client = OpenAI(api_key=api_key)
#             system_prompt = f"Categorize if the command below would benefit if a song is played. Return 'true' or 'false' string only, lowercase and no other characters."
#             completion = client.chat.completions.create(
#                 model="gpt-4o-mini",
#                 messages=[
#                     {"role": "system", "content": system_prompt},
#                     {"role": "user", "content": user_text }
#                 ],
#                 temperature=0.0,
#             )
#             # log_to_file(f"[GPTCommands] Full API response: {completion}")
#             classification = completion.choices[0].message.content.strip().lower()
#             log_to_file(f"[GPTCommands] Does user want music: {classification}")
#             return classification
#         except Exception as e:
#             log_to_file(f"[GPTCommands] OpenAI API error: {e}. Falling back to no music.")
            
#     # Fallback dummy classification
#     classification = "false"
#     return result

# def ask_gpt_for_rest_command(user_text, context):
#     """
#     Generate a dummy Home Assistant command for general queries.
#     """
#     result = f"HA command based on '{user_text}' with context: {context}"
#     log_to_file(f"[GPTCommands] ask_gpt_for_rest_command: {result}")
#     return result

# def ask_gpt_for_spotify_query(prompt, api_key=None):
#     """
#     Use the LLM to generate a refined Spotify search query from the user prompt.
#     The system prompt instructs the LLM to return only a concise music search query.
#     """
#     log_to_file("[GPTCommands] Generating Spotify command.")
#     # system_prompt = ("Based on the context of this prompt generate a query related to music in the following format, return nothing else except for the music query. You are not being asked to play music, you are only being asked to design a query to search for music based on the prompt. Do not include the room or space in query. Include if users asks for playlist, track, song, album, or artist. Include moods, feelings, activites or events if not specific.\nFormat:\n'Bohemian Rhapsody by Queen'\n'Smooth Criminal'\n'artist:Imagine Dragons track:Radioactive'\n'album:Thriller'\n'genre:'electronic dance music' year:2015'")
#     system_prompt = """
#         Based on the context of the user's prompt, generate a concise Spotify search query using field filters. Use one of the following prefixes if applicable: "track:", "album:", "artist:", or "playlist:". When a field value contains multiple words, enclose it in double quotes. Here are some examples:

#         1. Track query:
#         track:"Shape of You" artist:"Ed Sheeran"

#         2. Album query:
#         album:"Continuum" artist:"John Mayer" year:"2006"

#         3. Artist query:
#         artist:"Beyoncé" genre:"pop"

#         4. Playlist query:
#         playlist:"Workout Mix" mood:"energetic"

#         5. Combined track query:
#         track:"Blinding Lights" artist:"The Weeknd" year:"2020"

#         6. Advanced album query:
#         album:"Random Access Memories" artist:"Daft Punk" genre:"electronic" year:"2013"

#         7. Playlist with mood and activity:
#         playlist:"Party Hits" mood:"upbeat"

#         8. Artist query with era:
#         artist:"Radiohead" genre:"alternative" year:"1997"

#         Generate only the Spotify search query in the correct format.
#         """

#     try:
#         client = OpenAI(api_key=api_key)
#         messages = [
#             {"role": "system", "content": system_prompt},
#             {"role": "user", "content": prompt}
#         ]
#         completion = client.chat.completions.create(
#             model="gpt-4o-mini",
#             messages=messages,
#             temperature=0.0,
#         )
#         spotify_query = completion.choices[0].message.content.strip()
#         log_to_file(f"[GPTCommands] Generated Spotify query: {spotify_query}")
#         return spotify_query
#     except Exception as e:
#         log_to_file(f"[GPTCommands] OpenAI API error in ask_gpt_for_spotify_query: {e}. Using raw prompt as query.")
#         return prompt