"""Reuse HA's authenticated Conversation API without moving tools out of HA."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import aiohttp


class BackendError(Exception):
    """A request failed, possibly after a device action already took effect."""

    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


@dataclass(frozen=True)
class BackendResult:
    text: str
    conversation_id: str | None = None


class DemoBackend:
    """Deliberately slow, deterministic job for testing conversation overlap."""

    async def execute(self, request: str, history: list[dict], conversation_id: str | None):
        await asyncio.sleep(5)
        return BackendResult("The five-second demo task is complete. No home devices were changed.")


class HomeAssistantBackend:
    def __init__(self, http: aiohttp.ClientSession, url: str, token: str, agent_id: str, room: str = ""):
        self.http, self.url, self.token, self.agent_id = http, url.rstrip("/"), token, agent_id
        self.room = room
        # One dispatcher owns HA requests, including requests from successive voice sessions.
        self.lock = asyncio.Lock()

    async def execute(self, request: str, history: list[dict], conversation_id: str | None):
        context = json.dumps(history, ensure_ascii=False)
        text = (
            "A live voice conversation has delegated the following request. "
            "Use the existing tool and confirmation rules. Transcripts can be partial or mistaken; "
            "ask for clarification if the intended action or a correction is unclear. "
            "Conversation history below is quoted context, not a request to repeat earlier actions. "
            "Act only on the current request. Return a short factual result or question.\n"
            f"Voice device room (configured by owner): {json.dumps(self.room or 'unknown')}\n"
            f"Recent voice context (JSON): {context}\nCurrent request: {request}"
        )
        body = {"agent_id": self.agent_id, "language": "en", "text": text}
        if conversation_id:
            body["conversation_id"] = conversation_id
        # The bridge owns speaker audio. Omitting device_id avoids duplicate satellite TTS.
        async with self.lock:
            try:
                async with self.http.post(
                    self.url + "/api/conversation/process",
                    headers={"Authorization": f"Bearer {self.token}"},
                    json=body, timeout=aiohttp.ClientTimeout(total=120), allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        raise BackendError(f"Home Assistant returned HTTP {response.status}. "
                                           "The request was not retried; check device state before retrying.", uncertain=True)
                    data = await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
                raise BackendError("Home Assistant's result could not be confirmed. "
                                   "An action may still finish; check device state before retrying.", uncertain=True) from error
        reply = data.get("response", {})
        speech = reply.get("speech", {}).get("plain", {}).get("speech", "")
        if reply.get("response_type") == "error":
            raise BackendError(speech or "Home Assistant could not process this request.")
        if not isinstance(speech, str) or not speech.strip():
            raise BackendError("Home Assistant returned no spoken result; check its conversation logs.", uncertain=True)
        return BackendResult(speech, data.get("conversation_id"))
