"""Translate Home Assistant app options into the shared Voice PE bridge settings."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re

from aiohttp import web

from experimental.live.device_server import DeviceSettings, create_app

LOG = logging.getLogger("special_agent_live")
OPTIONS_FILE = Path("/data/options.json")


def text_option(options: dict, name: str, default: str = "", *, maximum: int = 1024) -> str:
    value = options.get(name, default)
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError(f"Invalid {name} option")
    return value


def settings_from_options(options: dict, environment: dict) -> DeviceSettings:
    if not isinstance(options, dict):
        raise ValueError("App options must be a JSON object")
    api_key = text_option(options, "api_key")
    if not api_key or not api_key.isascii() or any(c.isspace() for c in api_key):
        raise ValueError("Set api_key to your OpenAI API key")
    token = text_option(options, "device_token", maximum=256)
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
        raise ValueError("Set device_token to 32–256 random URL-safe letters, digits, underscores or hyphens")
    backend = text_option(options, "backend", "demo")
    if backend not in ("demo", "home-assistant"):
        raise ValueError("backend must be demo or home-assistant")
    room = text_option(options, "room", "Office", maximum=120)
    agent_id = text_option(options, "agent_id", "conversation.special_agent", maximum=255)
    if not agent_id.strip():
        raise ValueError("Set agent_id to your available conversation agent")
    limits = {}
    for name, default, low, high in (("idle_timeout", 30, 10, 600), ("max_duration", 0, 0, 1800)):
        value = options.get(name, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{name} must be an integer from {low} to {high}")
        limits[name] = value
    supervisor_token = environment.get("SUPERVISOR_TOKEN", "") if backend == "home-assistant" else ""
    if backend == "home-assistant" and not supervisor_token:
        raise ValueError("Home Assistant did not provide SUPERVISOR_TOKEN; check homeassistant_api permission")
    settings = DeviceSettings(
        api_key=api_key, device_token=token, backend=backend, room=room,
        ha_url="http://supervisor/core", ha_token=supervisor_token, ha_agent_id=agent_id,
        bind="0.0.0.0", port=8099, **limits,
    )
    settings.validate()
    return settings


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    try:
        options = json.loads(OPTIONS_FILE.read_text())
    except (OSError, ValueError):
        LOG.error("Cannot read /data/options.json; save the app configuration first")
        return 1
    try:
        settings = settings_from_options(options, os.environ)
    except ValueError as error:
        # Validation errors name options only; never print their supplied values.
        LOG.error("Configuration error: %s", error)
        return 1
    LOG.info("Starting Voice PE bridge on port 8099 with %s backend", settings.backend)
    # Device authentication is in the WebSocket URL; access logs would expose it.
    web.run_app(create_app(settings), host=settings.bind, port=settings.port, access_log=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
