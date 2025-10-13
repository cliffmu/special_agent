"""Session management helpers for multi-turn conversations."""

from __future__ import annotations

from typing import Any, Dict
import time
import uuid

try:
    from . import logging as log
    from .. import DOMAIN
    from ..session_store import Session
except ImportError:
    from utils import logging as log
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
                # Focus tracking for future features (not currently used in prompts)
                focus = session.focus
                pending = session.pending
                
                # Add new user message with ID
                user_msg = {"role": "user", "content": prompt, "id": generate_message_id()}
                msgs.append(user_msg)
                return msgs, mgr, focus, pending
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

