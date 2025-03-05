import os
import json
import datetime
from .logger_helper import log_to_file

# Get the directory where this file is located
COMPONENT_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(COMPONENT_DIR, "command_history.json")

def log_command(
    user_text: str,
    device_id: str = "",
    session_id: str = "",
    command_response: str = "",
    commands_list = None,
    success: bool = None,
    metadata: dict = None
):
    """
    Log command history to a structured JSON file.
    
    Args:
        user_text: The original user request
        device_id: ID of the device that initiated the request
        session_id: Session ID if available
        command_response: Text response sent back to the user
        commands_list: List of commands that were or would be executed
        success: Whether command execution succeeded
        metadata: Additional metadata to include
    """
    try:
        # Create a timestamp
        timestamp = datetime.datetime.now().isoformat()
        
        # Create the history entry
        entry = {
            "timestamp": timestamp,
            "user_text": user_text,
            "device_id": device_id,
            "session_id": session_id,
            "response": command_response,
            "success": success
        }
        
        # Add metadata if provided
        if metadata:
            entry["metadata"] = metadata
            
        # Add commands list if provided (simplified version)
        if commands_list:
            simplified_commands = []
            for cmd in commands_list:
                if isinstance(cmd, dict):
                    service = cmd.get("service", "unknown")
                    entity_id = cmd.get("data", {}).get("entity_id", "unknown")
                    simplified_cmd = {
                        "service": service,
                        "entity_id": entity_id
                    }
                    simplified_commands.append(simplified_cmd)
            entry["commands"] = simplified_commands
        
        # Load existing history if it exists
        history = []
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except Exception as e:
                log_to_file(f"[CommandHistory] Error loading history file: {e}")
                # Start with empty history if file is corrupted
                history = []
        
        # Add new entry
        history.append(entry)
        
        # Limit history size (keep last 1000 entries)
        if len(history) > 1000:
            history = history[-1000:]
        
        # Save history
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
            
        log_to_file(f"[CommandHistory] Logged command: {user_text}")
        
    except Exception as e:
        log_to_file(f"[CommandHistory] Error logging command: {e}")