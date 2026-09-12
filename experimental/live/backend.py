"""Delegate to Special Agent's authenticated HA API and relay safe activity."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass

import aiohttp
from utils.logging import activity

LOG = logging.getLogger(__name__ + ".activity")
LOG.setLevel(logging.INFO)
POLL_LOG = logging.getLogger(__name__)
HA_ACTIVITY_LOG = logging.getLogger(__name__ + ".home_assistant")
HA_ACTIVITY_LOG.setLevel(logging.INFO)
PROCESS_PATH = "/api/special_agent/live/process"
ACTIVITY_PATH = "/api/special_agent/live/activity"
_ACTIVITY_FIELDS = frozenset({
    "event", "request", "phase", "status", "source", "model", "tool", "backend", "error_type",
    "elapsed_ms", "http_status", "iteration", "attempt", "tools", "tool_count",
    "input_tokens", "output_tokens", "cached_tokens", "total_tokens", "session", "job",
    "effort", "requested_tier", "effective_tier", "function_count", "function_names",
    "verification", "verified_steps", "completed_steps", "total_steps",
})
_ACTIVITY_ATOM = re.compile(r"[A-Za-z0-9_.:-]{1,100}\Z")
_EPOCH = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def _safe_activity_line(line):
    """Accept the bounded identifier/count format, never arbitrary HA log text."""
    if not isinstance(line, str) or not 1 <= len(line) <= 2048:
        return False
    fields = line.split(" ")
    if len(fields) < 2 or not fields[0].startswith("event=") or not fields[1].startswith("request="):
        return False
    names = set()
    for field in fields:
        name, separator, value = field.partition("=")
        if not separator or name not in _ACTIVITY_FIELDS or name in names:
            return False
        names.add(name)
        values = value.split(",") if name == "function_names" else [value]
        if not 1 <= len(values) <= 10 or any(
            not _ACTIVITY_ATOM.fullmatch(atom) or atom.startswith(("sk-", "sk_")) for atom in values
        ):
            return False
    return True


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
    def __init__(self, http: aiohttp.ClientSession, url: str, token: str, agent_id: str, room: str = "", *,
                 model: str | None = None, reasoning_effort: str | None = None, fast_mode: bool | None = None):
        self.http, self.url, self.token, self.agent_id = http, url.rstrip("/"), token, agent_id
        self.room = room
        self.model, self.reasoning_effort, self.fast_mode = model, reasoning_effort, fast_mode
        self.activity_cursor = 0
        self.activity_epoch = None
        self.activity_poll_interval = 1.0
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
        body.update({name: value for name, value in (
            ("model", self.model), ("reasoning_effort", self.reasoning_effort), ("fast_mode", self.fast_mode)
        ) if value is not None})
        if conversation_id:
            body["conversation_id"] = conversation_id
        # The bridge owns speaker audio. Omitting device_id avoids duplicate satellite TTS.
        async with self.lock:
            started = time.monotonic()
            http_status = None
            status = "error"
            error_type = None
            activity("ha_request", logger=LOG, phase="sent", backend="home-assistant")
            try:
                async with self.http.post(
                    self.url + PROCESS_PATH,
                    headers={"Authorization": f"Bearer {self.token}"},
                    json=body, timeout=aiohttp.ClientTimeout(total=120), allow_redirects=False,
                ) as response:
                    http_status = response.status
                    if response.status == 404:
                        raise BackendError("Update the Special Agent Home Assistant integration to version 0.3.2 "
                                           "or later and restart Home Assistant. The Live endpoint is unavailable; "
                                           "the request was not retried.")
                    if response.status != 200:
                        raise BackendError(f"Home Assistant returned HTTP {response.status}. "
                                           "The request was not retried; check device state before retrying.", uncertain=True)
                    data = await response.json()
                reply = data.get("response", {})
                speech = reply.get("speech", {}).get("plain", {}).get("speech", "")
                if reply.get("response_type") == "error":
                    raise BackendError(speech or "Home Assistant could not process this request.")
                if not isinstance(speech, str) or not speech.strip():
                    raise BackendError("Home Assistant returned no spoken result; check its conversation logs.", uncertain=True)
                status = "completed"
                return BackendResult(speech, data.get("conversation_id"))
            except asyncio.CancelledError:
                status = "cancelled"
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
                error_type = type(error).__name__
                raise BackendError("Home Assistant's result could not be confirmed. "
                                   "An action may still finish; check device state before retrying.", uncertain=True) from error
            except Exception as error:
                error_type = type(error).__name__
                raise
            finally:
                fields = {"phase": "received" if http_status is not None else "failed", "status": status,
                          "elapsed_ms": round((time.monotonic() - started) * 1000)}
                if http_status is not None:
                    fields["http_status"] = http_status
                if error_type:
                    fields["error_type"] = error_type
                activity("ha_request", logger=LOG, **fields)

    async def poll_activity(self):
        """Relay one shared HA activity stream until the owning app cancels us."""
        failed, retry_delay = False, self.activity_poll_interval
        while True:
            try:
                has_more = await self._poll_activity_page()
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError):
                if not failed:
                    POLL_LOG.warning("Home Assistant activity unavailable; retrying. "
                                     "Requires Special Agent integration 0.3.2 or later.")
                failed = True
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30)
                continue
            if failed:
                POLL_LOG.info("Home Assistant activity connection restored")
            failed, retry_delay = False, self.activity_poll_interval
            if not has_more:
                await asyncio.sleep(self.activity_poll_interval)

    async def _poll_activity_page(self):
        params = {"cursor": str(self.activity_cursor), "limit": "200"}
        if self.activity_epoch is not None:
            params["epoch"] = self.activity_epoch
        async with self.http.get(
            self.url + ACTIVITY_PATH, params=params,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=aiohttp.ClientTimeout(total=10), allow_redirects=False,
        ) as response:
            if response.status != 200:
                raise ValueError("Activity endpoint unavailable")
            page = await response.json()
        if not isinstance(page, dict):
            raise ValueError("Invalid activity page")
        epoch, cursor = page.get("epoch"), page.get("cursor")
        records, reset, has_more = page.get("records"), page.get("reset"), page.get("has_more")
        if (not isinstance(epoch, str) or not _EPOCH.fullmatch(epoch)
                or type(cursor) is not int or not 0 <= cursor <= 2**63 - 1
                or type(reset) is not bool or type(has_more) is not bool
                or not isinstance(records, list) or len(records) > 200):
            raise ValueError("Invalid activity page")
        previous = -1
        for record in records:
            if (not isinstance(record, dict) or type(record.get("cursor")) is not int
                    or not 1 <= record["cursor"] <= cursor or record["cursor"] < previous
                    or not _safe_activity_line(record.get("line"))):
                raise ValueError("Invalid activity record")
            previous = record["cursor"]
        changed_epoch = epoch != self.activity_epoch
        rolled_back = reset and cursor < self.activity_cursor
        seen = 0 if changed_epoch or rolled_back else self.activity_cursor
        if cursor < seen or (has_more and cursor <= seen):
            raise ValueError("Activity cursor did not advance")
        if reset and self.activity_epoch is not None:
            POLL_LOG.info("Home Assistant activity stream reset; resuming available records")
        for record in records:
            if record["cursor"] > seen:
                # The HA endpoint owns sanitization; this format check adds a
                # second boundary. Do not recapture forwarded records via activity().
                HA_ACTIVITY_LOG.info("%s", record["line"])
                seen = record["cursor"]
        self.activity_cursor, self.activity_epoch = cursor, epoch
        return has_more
