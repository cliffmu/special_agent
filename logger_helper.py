import asyncio

def log_to_file(message):
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
    with open("special_agent_log.txt", "a") as logfile:
        logfile.write(message + "\n")
