"""Shared defaults for model selection, device filtering and ranking."""

DEFAULT_AGENT_MODEL = "gpt-5.6-terra"
AGENT_MODELS = (
    "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.6-sol", "gpt-6-astra",
    "gpt-5", "gpt-5-mini", "gpt-5-nano",
)
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def normalize_reasoning_effort(model: str, effort: str) -> str:
    """Keep a model change compatible with an effort saved for another model."""
    if model.startswith("gpt-5.6"):
        supported = {"none", "low", "medium", "high", "xhigh", "max"}
    elif model.startswith("gpt-6"):
        supported = {"low", "medium", "high", "xhigh", "max"}
    else:
        supported = {"minimal", "low", "medium", "high"}
    return effort if effort in supported else "low"

EXCLUDED_DOMAINS = {
    "sensor",
    "binary_sensor", #MAYBE WANT
    "update",
    "number",
    "select",
    "button",
    "event",
    "alarm_control_panel",
    "assist_satellite",
    "automation",
    "camera",
    "device_tracker", #MAYBE WANT
    "input_select",
    "scene",
    "script",
    "stt",
    "sun",
    "time",
    "tts",
    "wake_word",
    "zone",
}

PREFERRED_DOMAINS = {
    "light",
    "fan",
    "media_player",
    "climate",
    # "switch",
    "cover",
    "lock",
    "remote",
    "vacuum",
    "weather",
}

INCLUDED_ENTITY_IDS = {
    # Allowlist for critical entities we want even if their domain is excluded
    "button.lex_2_0_scan_clients",
}

LOCATION_WORDS = {
    "kitchen",
    "office",
    "bedroom",
    "living",
    "bath",
    "foyer",
    "garage",
    "gym",
    "theater",
    "garage",
    "playroom",
    "nursery",
    "backyard",
    "front exterior",
}

EXCLUDED_SUFFIXES = ("_led", "_powertype", "_internaltemperature")

# ALL
# alarm_control_panel
# assist_satellite
# automation
# binary_sensor
# button
# camera
# climate
# conversation
# cover
# device_tracker
# event
# fan
# input_select
# light
# lock
# media_player
# number
# remote
# scene
# script
# select
# sensor
# stt
# sun
# switch
# time
# tts
# update
# vacuum
# wake_word
# weather
# zone

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536
FALLBACK_MODEL = "all-MiniLM-L6-v2"
BOOST_DOMAIN = 0.20
BOOST_AREA = 0.10
BOOST_OVERLAP = 0.30
MAX_SEARCH_K = 50
