import time
import re
import requests
from .logger_helper import log_to_file

SPOTIFY_API_BASE_URL = "https://api.spotify.com/v1"
spotify_token_cache = {"access_token": "", "expiration_time": 0}

# Map singular search types to the corresponding plural keys in Spotify's response.
SPOTIFY_SEARCH_TYPE_KEY_MAP = {
    "track": "tracks",
    "artist": "artists",
    "album": "albums",
    "playlist": "playlists"
}

def get_spotify_access_token(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, refresh_margin=60):
    """
    Retrieve a Spotify access token using the Client Credentials flow.
    Caches the token to avoid repeated calls.
    """
    global spotify_token_cache
    current_time = time.time()
    
    log_to_file(f"[Spotify] Client ID length: {len(SPOTIFY_CLIENT_ID.strip())}, Secret length: {len(SPOTIFY_CLIENT_SECRET.strip())}")
    
    if (spotify_token_cache.get("access_token") and 
            current_time < spotify_token_cache["expiration_time"] - refresh_margin):
        log_to_file("[Spotify] Using cached access token.")
        return spotify_token_cache["access_token"]

    token_url = "https://accounts.spotify.com/api/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}
    data = {"grant_type": "client_credentials"}
    log_to_file("[Spotify] Requesting new access token.")
    
    response = requests.post(token_url,
                             auth=(SPOTIFY_CLIENT_ID.strip(), SPOTIFY_CLIENT_SECRET.strip()),
                             headers=headers,
                             data=data)
    
    if response.status_code == 200:
        token_info = response.json()
        access_token = token_info["access_token"]
        expires_in = token_info["expires_in"]
        spotify_token_cache["access_token"] = access_token
        spotify_token_cache["expiration_time"] = current_time + expires_in
        log_to_file(f"[Spotify] Access token obtained, expires in {expires_in} seconds.")
        return access_token
    else:
        log_to_file(f"[Spotify] Error obtaining token: {response.status_code} - {response.text}")
        return None

def parse_spotify_query(llm_query):
    """
    Parse the LLM-generated query to extract the intended type and return a tuple:
    (search_type, query_for_search).
    
    If the query starts with one of the prefixes ("track:", "artist:", "album:", "playlist:"),
    it removes that prefix from the query value so that the search parameter 'q' isn’t duplicated.
    If no prefix is present, defaults to "track".
    """
    clean_query = llm_query.strip()
    prefixes = ["track:", "artist:", "album:", "playlist:"]
    for prefix in prefixes:
        if clean_query.lower().startswith(prefix):
            # Return the type (without the colon) and the query string with the prefix removed.
            log_to_file(f"[Spotify] LLM Query: {llm_query}")
            log_to_file(f"[Spotify] Cleaned Query: {clean_query}")
            return prefix[:-1], clean_query[len(prefix):].strip()
    log_to_file(f"[Spotify] LLM Query Issue No Prefix: {llm_query}")
    return "track", clean_query
    # return "playlist", 'artist:"John Mayer"'

def search_spotify(access_token, llm_query, limit=1, market="US"):
    """
    Search Spotify using the parsed query and search type.
    Returns the URI of the first matching item from the specified category.
    """
    search_type, query = parse_spotify_query(llm_query)
    log_to_file(f"[Spotify] Parsed query: type='{search_type}', query='{query}'")
    
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {
        "q": query,       # Use the cleaned query (without the prefix).
        "type": search_type,
        "limit": limit,
        "market": market
    }
    log_to_file(f"[Spotify] Searching for query: {params}")
    response = requests.get(f"{SPOTIFY_API_BASE_URL}/search", headers=headers, params=params)
    
    
    if response.status_code == 200:
        search_results = response.json()
        log_to_file(f"[Spotify] Full query response: {search_results}")
        log_to_file(f"[Spotify] Top-level keys in response: {list(search_results.keys())}")
        plural_key = SPOTIFY_SEARCH_TYPE_KEY_MAP.get(search_type, search_type + "s")
        if plural_key in search_results:
            items = search_results[plural_key].get("items", [])
            if not items:
                # No items at all
                log_to_file(f"[Spotify] No items found for {search_type}")
                return None
            
            # Make sure the first item is not None
            first_item = items[0]
            if first_item is None:
                log_to_file(f"[Spotify] First item is None for {search_type} query {query}.")
                return None
            
            uri = first_item.get("uri")
            log_to_file(f"[Spotify] Found {search_type} URI: {uri}")
            return uri
        else:
            log_to_file(f"[Spotify] No matching items found for type '{search_type}' with query '{query}'.")
            return None

        # plural_key = SPOTIFY_SEARCH_TYPE_KEY_MAP.get(search_type, search_type + "s")
        # if plural_key in search_results and search_results[plural_key]["items"]:
        #     item = search_results[plural_key]["items"][0]
        #     uri = item.get("uri")
        #     log_to_file(f"[Spotify] Found {search_type} URI: {uri}")
        #     return uri
        # else:
        #     log_to_file(f"[Spotify] No matching items found for type '{search_type}' with query '{query}'.")
        #     return None
    else:
        log_to_file(f"[Spotify] Error searching Spotify: {response.status_code} - {response.text}")
        return None





# import time
# import requests
# from .logger_helper import log_to_file

# SPOTIFY_API_BASE_URL = "https://api.spotify.com/v1"
# spotify_token_cache = {"access_token": "", "expiration_time": 0}

# def get_spotify_access_token(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, refresh_margin=60):
#     """
#     Retrieve a Spotify access token using the Client Credentials flow.
#     Caches the token to avoid repeated calls.
#     """
#     global spotify_token_cache
#     current_time = time.time()
    
#     # Log the lengths of credentials for debugging (avoid logging full secrets)
#     log_to_file(f"[Spotify] Client ID length: {len(SPOTIFY_CLIENT_ID.strip())}, Secret length: {len(SPOTIFY_CLIENT_SECRET.strip())}")
    
#     if (spotify_token_cache.get("access_token") and 
#             current_time < spotify_token_cache["expiration_time"] - refresh_margin):
#         log_to_file("[Spotify] Using cached access token.")
#         return spotify_token_cache["access_token"]

#     token_url = "https://accounts.spotify.com/api/token"
#     headers = {"Content-Type": "application/x-www-form-urlencoded"}
#     data = {"grant_type": "client_credentials"}
#     log_to_file("[Spotify] Requesting new access token.")
    
#     response = requests.post(token_url, auth=(SPOTIFY_CLIENT_ID.strip(), SPOTIFY_CLIENT_SECRET.strip()), headers=headers, data=data)
    
#     if response.status_code == 200:
#         token_info = response.json()
#         access_token = token_info["access_token"]
#         expires_in = token_info["expires_in"]
#         spotify_token_cache["access_token"] = access_token
#         spotify_token_cache["expiration_time"] = current_time + expires_in
#         log_to_file(f"[Spotify] Access token obtained, expires in {expires_in} seconds.")
#         return access_token
#     else:
#         log_to_file(f"[Spotify] Error obtaining token: {response.status_code} - {response.text}")
#         return None

# def search_spotify(access_token, query):
#     """
#     Search Spotify for the given query and return the URI of the first result.
#     Searches across tracks, playlists, artists, and albums.
#     """

#     clean_query = query.strip("'\"")

#     headers = {"Authorization": f"Bearer {access_token}"}
#     params = {
#         "q": clean_query,
#         "type": "track,playlist,artist,album",
#         "limit": 1
#     }
#     search_endpoint = f"{SPOTIFY_API_BASE_URL}/search"
#     log_to_file(f"[Spotify] Searching for query: {params}")
#     response = requests.get(search_endpoint, headers=headers, params=params)
#     log_to_file(f"[Spotify] Search results: {response.json()}")
    
#     if response.status_code == 200:
#         search_results = response.json()
#         for key in ["tracks", "playlists", "artists", "albums"]:
#             if key in search_results and search_results[key]["items"]:
#                 item = search_results[key]["items"][0]
#                 uri = item.get("uri")
#                 log_to_file(f"[Spotify] Found {key} URI: {uri}")
#                 # return uri
#         log_to_file("[Spotify] No matching items found.")
#         return None
#     else:
#         log_to_file(f"[Spotify] Error searching Spotify: {response.status_code} - {response.text}")
#         return None
