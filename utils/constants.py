"""Shared constants for device filtering and ranking."""

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
