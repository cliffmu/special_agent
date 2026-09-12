"""Session management helpers for multi-turn conversations."""

from __future__ import annotations

from typing import Any, Dict
import time
import uuid

try:
    from . import logging as log
    from . import performance
    from .. import DOMAIN
    from ..session_store import Session
except ImportError:
    from utils import logging as log
    from utils import performance
    from session_store import Session
    DOMAIN = "special_agent"


def generate_message_id(msg_type: str = "msg") -> str:
    """
    Generate a unique message ID for Responses API.
    
    Args:
        msg_type: Type prefix for the ID. 
                  - 'fc' for function_call_output
                  - 'msg' for other messages (default)
    """
    return f"{msg_type}_{uuid.uuid4().hex[:16]}"


async def get_device_context(hass: Any | None, session_key: tuple[str, str] | None) -> Dict[str, str | None]:
    """
    Get device name and room from session key (only if available).
    
    Returns:
        Dict with 'device_name' and 'room' keys (None if not found)
    """
    if not session_key or not hass:
        return {"device_name": None, "room": None}
    
    conversation_id, device_id = session_key
    
    # Skip if no device_id provided
    if not device_id or device_id == "":
        return {"device_name": None, "room": None}
    
    try:
        from homeassistant.helpers import device_registry as dr, area_registry as ar
        
        dev_reg = dr.async_get(hass)
        area_reg = ar.async_get(hass)
        
        dev = dev_reg.devices.get(device_id)
        if dev is None and not device_id.startswith("live:"):
            # Legacy callers sometimes sent names. Accept only an unambiguous
            # exact match; a substring could silently choose a different room.
            matches = [item for item in dev_reg.devices.values()
                       if (item.name_by_user or item.name or "").casefold() == device_id.casefold()]
            dev = matches[0] if len(matches) == 1 else None
        if dev is not None:
            device_name = dev.name_by_user or dev.name
            area = area_reg.async_get_area(dev.area_id) if dev.area_id else None
            return {"device_name": device_name, "room": area.name if area else None}
        
        # Not found - return None (skip device context in prompt)
        return {"device_name": None, "room": None}
    
    except Exception as error:
        log.warning("Could not get device context (%s)", type(error).__name__)
        return {"device_name": None, "room": None}


def load_session(
    hass: Any | None,
    session_key: tuple[str, str] | None,
    system_prompt: str,
    prompt: str,
    session_timeout_minutes: int = 5,
) -> tuple[list, Any | None, Dict[str, Any] | None, Dict[str, Any] | None]:
    """Restore a previous session or create a new one. Returns (messages, mgr, focus, pending)."""
    mgr = None
    focus: Dict[str, Any] | None = None
    pending: Dict[str, Any] | None = None
    if hass:
        mgr = hass.data.get(DOMAIN, {}).get("sessions")
        if session_key and mgr:
            session = mgr.get(session_key)
            if session:
                # Clean loaded messages - remove fields not valid for Responses API input
                # KEEP 'id' field - it's required by Responses API
                msgs = []
                for msg in session.messages:
                    if isinstance(msg, dict):
                        # SKIP old system prompt - we'll prepend fresh one
                        if msg.get("role") == "system":
                            continue
                        
                        # Remove output-only fields but KEEP id
                        clean_msg = {k: v for k, v in msg.items() if k not in ('status', 'encrypted_content')}
                        # Ensure message has an id (add one if missing from old sessions)
                        if 'id' not in clean_msg:
                            # Use correct prefix based on message type
                            msg_type_prefix = "fc" if clean_msg.get("type") == "function_call_output" else "msg"
                            clean_msg['id'] = generate_message_id(msg_type_prefix)
                        msgs.append(clean_msg)
                    else:
                        msgs.append(msg)
                
                # PREPEND fresh system prompt (with updated datetime, device context, etc.)
                fresh_system = {"role": "system", "content": system_prompt, "id": generate_message_id()}
                msgs.insert(0, fresh_system)
                
                # Focus tracking for future features (not currently used in prompts)
                focus = session.focus
                
                # Clear pending when new user prompt arrives - user has moved on
                # Old pending responses (from ask_user, prepare_voice_response) are now stale
                pending = None
                
                # Add new user message with ID
                user_msg = {"role": "user", "content": prompt, "id": generate_message_id()}
                msgs.append(user_msg)
                
                # Log the user's prompt for debugging
                log.info(f"User_Prompt: {prompt}")
                
                # Track for performance analysis
                performance.track_sync_operation(
                    "load_session",
                    metadata={"args": {
                        "prompt": prompt,
                        "session_msg_count": len(msgs),
                        "is_continuation": True
                    }},
                    status="ok"
                )
                
                return msgs, mgr, focus, pending
    # New session - log the user's prompt for debugging
    log.info(f"User_Prompt (new session): {prompt}")
    
    # Track for performance analysis
    performance.track_sync_operation(
        "load_session",
        metadata={"args": {
            "prompt": prompt,
            "session_msg_count": 2,
            "is_continuation": False
        }},
        status="ok"
    )
    
    return [
        {"role": "system", "content": system_prompt, "id": generate_message_id()},
        {"role": "user", "content": prompt, "id": generate_message_id()},
    ], mgr, focus, None


def store_session(
    mgr: Any | None,
    session_key: tuple[str, str] | None,
    messages: list,
    pending: Dict[str, Any] | None,
    focus: Dict[str, Any] | None,
) -> None:
    """Persist session state if a manager is available."""
    if mgr and session_key:
        # Convert Pydantic objects to dicts for JSON serialization
        serializable_messages = []
        for msg in messages:
            if hasattr(msg, 'model_dump'):
                serializable_messages.append(msg.model_dump())
            else:
                serializable_messages.append(msg)
        
        mgr.set(
            session_key,
            Session(
                messages=serializable_messages,
                pending=pending,
                focus=focus,
                device_id=session_key[1],
                updated=time.time(),
            ),
        )


def clear_session(mgr: Any | None, session_key: tuple[str, str] | None) -> None:
    """Remove a persisted session."""
    if mgr and session_key:
        mgr.pop(session_key)
