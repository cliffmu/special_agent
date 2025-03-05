import asyncio
import os
import sys

# Get the directory where the current file (logger_helper.py) is located
COMPONENT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(COMPONENT_DIR, "special_agent_log.txt")

# Check if we're running in a test environment
IN_TESTING = os.environ.get('SPECIAL_AGENT_TESTING') == 'true'

def log_to_file(message):
    # When testing, just print to stdout instead of file operations
    if IN_TESTING:
        print(f"[LOG] {message}")
        return
        
    try:
        # Try to get the current running event loop.
        loop = asyncio.get_running_loop()
        # Schedule asynchronous logging.
        loop.create_task(_async_log(message))
    except RuntimeError:
        # No running event loop, fallback to synchronous logging.
        _sync_log(message)

async def _async_log(message):
    await asyncio.to_thread(_sync_log, message)

def _sync_log(message):
    try:
        with open(LOG_FILE, "a") as logfile:
            logfile.write(message + "\n")
    except Exception as e:
        # Fallback if we can't write to the component directory
        print(f"Error writing to log: {e}")
        with open("/tmp/special_agent_log.txt", "a") as logfile:
            logfile.write(f"Original error: {e}\n")
            logfile.write(message + "\n")
