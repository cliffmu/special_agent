"""Configured satellite identities and connection claims for one bridge."""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field

MAX_DEVICES = 16
_DEVICE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_HA_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,256}\Z")


@dataclass(frozen=True)
class VoiceRegistration:
    id: str
    token: str = field(repr=False)
    room: str = ""
    ha_device_id: str = ""

    @property
    def effective_id(self):
        return self.ha_device_id or "live:" + self.id

    @property
    def device_hash(self):
        return hashlib.sha256(self.effective_id.encode()).hexdigest()[:10]

    def validate(self):
        if not isinstance(self.id, str) or not _DEVICE_ID.fullmatch(self.id):
            raise ValueError("Invalid device_id; use 1–64 letters, digits, underscores or hyphens")
        if not isinstance(self.token, str) or not _TOKEN.fullmatch(self.token):
            raise ValueError("Set VOICE_DEVICE_TOKEN/device_token to 32–256 URL-safe characters")
        if (not isinstance(self.room, str) or len(self.room) > 120
                or any(ord(character) < 32 for character in self.room)):
            raise ValueError("VOICE_ROOM/room must be a short room name")
        if (not isinstance(self.ha_device_id, str)
                or self.ha_device_id and not _HA_ID.fullmatch(self.ha_device_id)):
            raise ValueError("Invalid ha_device_id")


def validate_registrations(registrations):
    if not isinstance(registrations, (tuple, list)) or not 1 <= len(registrations) <= MAX_DEVICES:
        raise ValueError("Configure between 1 and 16 Voice devices")
    identities, tokens, effective_ids = set(), set(), set()
    for registration in registrations:
        if not isinstance(registration, VoiceRegistration):
            raise ValueError("Invalid Voice device registration")
        registration.validate()
        if registration.id in identities:
            raise ValueError("Voice device IDs must be unique")
        if registration.token in tokens:
            raise ValueError("Voice device tokens must be unique")
        if registration.effective_id in effective_ids:
            raise ValueError("Voice device HA identities must be unique")
        identities.add(registration.id)
        tokens.add(registration.token)
        effective_ids.add(registration.effective_id)
    return tuple(registrations)


class VoiceRegistry:
    """Claims remain occupied until disconnected satellites finish accepted work."""

    def __init__(self, registrations):
        self.registrations = validate_registrations(registrations)
        self.claims = {}
        self.backends = {}
        self.last_stops = {}

    def authenticate(self, supplied):
        if not isinstance(supplied, str) or len(supplied) > 256:
            return None
        supplied_bytes = supplied.encode("utf-8")
        matched = None
        for registration in self.registrations:
            if hmac.compare_digest(supplied_bytes, registration.token.encode("ascii")):
                matched = registration
        return matched

    def release(self, registration, device):
        if self.claims.get(registration.id) is device:
            self.last_stops[registration.id] = (device.last_stop_at, device.last_stop_reason)
            del self.claims[registration.id]

    def health(self):
        rows, stops = [], list(self.last_stops.values())
        for registration in self.registrations:
            device = self.claims.get(registration.id)
            connected = bool(device and device.socket.prepared and not device.socket.closed)
            reason = self.last_stops.get(registration.id, (0, None))[1]
            if device:
                reason = device.last_stop_reason
                stops.append((device.last_stop_at, reason))
            rows.append({"device": registration.device_hash, "connected": connected,
                         "audio_active": bool(connected and device.accept_audio),
                         "draining": bool(device and (device.closing or device.socket.closed)),
                         "last_stop_reason": reason})
        connected_count = sum(row["connected"] for row in rows)
        active_count = sum(row["audio_active"] for row in rows)
        return {"device_connected": connected_count > 0, "audio_active": active_count > 0,
                "last_stop_reason": max(stops, default=(0, None), key=lambda item: item[0])[1],
                "configured_devices": len(rows), "connected_devices": connected_count,
                "active_devices": active_count, "devices": rows}
