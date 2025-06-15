"""Shared constants for device filtering and ranking."""

EXCLUDED_DOMAINS = {
    "sensor",
    "binary_sensor",
    "update",
    "number",
    "select",
    "button",
    "event",
}

PREFERRED_DOMAINS = {
    "light",
    "fan",
    "media_player",
    "climate",
    "switch",
    "cover",
}

LOCATION_WORDS = {
    "kitchen",
    "office",
    "bedroom",
    "living",
    "bath",
    "foyer",
    "garage",
}

EXCLUDED_SUFFIXES = ("_led", "_powertype", "_internaltemperature")
