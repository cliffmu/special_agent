"""Performance monitoring utilities for tracking execution timing across async operations.

This module provides a context manager-based timing system that:
- Tracks hierarchical operations with minimal code clutter
- Handles parallel operations correctly
- Exports to CSV for analysis
- Can be easily enabled/disabled
"""

from __future__ import annotations

import asyncio
import csv
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Context variable to track current request, session, and parent operation
_current_request: ContextVar[Optional[str]] = ContextVar("current_request", default=None)
_current_session: ContextVar[Optional[str]] = ContextVar("current_session", default=None)
_operation_stack: ContextVar[list[str]] = ContextVar("operation_stack", default=[])

# Global storage for all timing records
_timing_records: list[TimingRecord] = []

# Configuration
_enabled: bool = False
_csv_path: Optional[Path] = None


@dataclass
class TimingRecord:
    """Single timing record for analysis.
    
    Schema V2: Analysis-ready CSV format with stable columns.
    Wall-clock times for timestamps, monotonic duration for accuracy.
    """
    request_id: str
    operation: str
    start_time: float  # Wall-clock epoch seconds (for timestamp correlation)
    end_time: float    # Wall-clock epoch seconds
    duration_s: float  # Monotonic clock duration in seconds
    
    # Core tracking
    session_id: Optional[str] = None
    parent_operation: Optional[str] = None
    parallel_group: Optional[str] = None
    
    # Operation context
    status: Optional[str] = None  # ok, error, timeout
    args: Optional[str] = None    # JSON string of parameters (truncated)
    llm_depth: Optional[int] = None  # 0, 1, 2... for llm_call operations
    
    # LLM-specific fields
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    
    # Metadata catchall for additional context
    metadata: dict[str, Any] = field(default_factory=dict)


def configure(enabled: bool = True, csv_path: str | Path | None = None) -> None:
    """Configure performance monitoring.
    
    Args:
        enabled: Whether to track performance
        csv_path: Path to CSV file for output. If None, uses default in config dir
    """
    global _enabled, _csv_path
    _enabled = enabled
    if csv_path:
        _csv_path = Path(csv_path)
    elif enabled and _csv_path is None:
        # Default to config directory
        _csv_path = Path("performance_metrics.csv")


def is_enabled() -> bool:
    """Check if performance monitoring is enabled."""
    return _enabled


def get_current_request_id() -> Optional[str]:
    """Get the current request ID from context."""
    return _current_request.get()


def set_session_id(session_id: str) -> None:
    """Set the session ID for the current context."""
    _current_session.set(session_id)


def _extract_llm_fields(metadata: dict[str, Any]) -> dict[str, Any]:
    """Extract LLM-specific fields from metadata for TimingRecord."""
    return {
        "model": metadata.get("model"),
        "reasoning_effort": metadata.get("reasoning"),
        "llm_depth": metadata.get("depth"),
        "prompt_tokens": metadata.get("prompt_tokens"),
        "completion_tokens": metadata.get("completion_tokens"),
        "total_tokens": metadata.get("total_tokens"),
    }


def _truncate_json(data: Any, max_length: int = 500) -> str:
    """Convert data to JSON string and truncate if needed."""
    import json
    try:
        json_str = json.dumps(data, default=str, ensure_ascii=False)
        if len(json_str) > max_length:
            return json_str[:max_length-3] + "..."
        return json_str
    except Exception:
        return str(data)[:max_length]


@asynccontextmanager
async def track_request(request_name: str = "session", metadata: Optional[dict[str, Any]] = None):
    """Track an entire user request lifecycle.
    
    This should wrap the outermost operation (e.g., the conversation handler).
    Creates a unique request_id that all nested operations will use.
    
    Usage:
        async with track_request("session", metadata={"device": "office"}):
            result = await agent.process(user_input)
    """
    if not _enabled:
        yield
        return
    
    request_id = str(uuid.uuid4())[:8]
    token = _current_request.set(request_id)
    
    start_mono = time.perf_counter()
    start_wall = time.time()
    status = "ok"
    
    try:
        yield request_id
    except Exception:
        status = "error"
        raise
    finally:
        end_mono = time.perf_counter()
        end_wall = time.time()
        duration_s = end_mono - start_mono
        
        session_id = _current_session.get()
        llm_fields = _extract_llm_fields(metadata or {})
        
        # Extract args if present
        args = None
        if metadata:
            if "response" in metadata:
                args = _truncate_json({"response": metadata["response"]}, 500)
            elif "prompt" in metadata:
                args = _truncate_json({"prompt": metadata["prompt"]}, 200)
        
        _timing_records.append(TimingRecord(
            request_id=request_id,
            session_id=session_id,
            operation=request_name,
            start_time=start_wall,
            end_time=end_wall,
            duration_s=duration_s,
            status=status,
            args=args,
            metadata=metadata or {},
            **llm_fields,
        ))
        
        _current_request.reset(token)


@asynccontextmanager
async def track_operation(
    operation_name: str,
    metadata: Optional[dict[str, Any]] = None,
    status: Optional[str] = None,
):
    """Track a single operation within a request.
    
    Automatically captures the current request_id and parent operation from context.
    Tracks both wall-clock time (for timestamp correlation) and monotonic duration.
    
    Usage:
        async with track_operation("llm_call", metadata={"model": "gpt-5", "depth": 0}):
            response = await client.responses.create(...)
        
        # Or with args:
        async with track_operation("tool_search_devices", metadata={"args": {...}}):
            result = await search_devices(...)
    """
    if not _enabled:
        yield
        return
    
    request_id = _current_request.get()
    if not request_id:
        # No active request, skip tracking
        yield
        return
    
    # Get current operation stack for hierarchical tracking
    stack = _operation_stack.get([])
    parent = stack[-1] if stack else None
    
    # Push current operation onto stack
    new_stack = stack + [operation_name]
    token = _operation_stack.set(new_stack)
    
    start_mono = time.perf_counter()
    start_wall = time.time()
    final_status = status or "ok"
    
    try:
        yield
    except Exception:
        final_status = "error"
        raise
    finally:
        end_mono = time.perf_counter()
        end_wall = time.time()
        duration_s = end_mono - start_mono
        
        session_id = _current_session.get()
        llm_fields = _extract_llm_fields(metadata or {})
        
        # Extract args if present in metadata
        args = None
        if metadata and "args" in metadata:
            args = _truncate_json(metadata["args"], 500)
        
        _timing_records.append(TimingRecord(
            request_id=request_id,
            session_id=session_id,
            operation=operation_name,
            start_time=start_wall,
            end_time=end_wall,
            duration_s=duration_s,
            parent_operation=parent,
            status=final_status,
            args=args,
            metadata=metadata or {},
            **llm_fields,
        ))
        
        _operation_stack.reset(token)


def track_sync_operation(
    operation_name: str,
    metadata: Optional[dict[str, Any]] = None,
    status: str = "ok",
) -> None:
    """Track a synchronous operation (e.g., load_session).
    
    For operations that don't need async context manager overhead.
    
    Usage:
        track_sync_operation("load_session", metadata={
            "args": {"prompt": "turn on lights", "tools_count": 15}
        })
    """
    if not _enabled:
        return
    
    request_id = _current_request.get()
    if not request_id:
        return
    
    session_id = _current_session.get()
    stack = _operation_stack.get([])
    parent = stack[-1] if stack else None
    
    # Extract args
    args = None
    if metadata and "args" in metadata:
        args = _truncate_json(metadata["args"], 500)
    
    # For sync operations, we just record a point-in-time marker (no duration)
    wall_time = time.time()
    
    _timing_records.append(TimingRecord(
        request_id=request_id,
        session_id=session_id,
        operation=operation_name,
        start_time=wall_time,
        end_time=wall_time,
        duration_s=0.0,
        parent_operation=parent,
        status=status,
        args=args,
        metadata=metadata or {},
    ))


@asynccontextmanager
async def track_llm_call(
    model: str,
    reasoning_effort: str,
    depth: int,
    message_count: int,
):
    """Track LLM call with token counts from response.
    
    Context manager that captures timing and yields timing info.
    After the call completes, provide the response to capture token counts.
    
    Usage:
        async with track_llm_call(model, reasoning, depth, len(messages)) as tracker:
            response = await call_llm(...)
            tracker.set_response(response)
    
    Args:
        model: Model name (e.g., "gpt-5")
        reasoning_effort: Reasoning level ("minimal", "low", "medium", "high")
        depth: Current loop depth
        message_count: Number of messages in conversation
        
    Yields:
        Tracker object with set_response() method to capture token counts
    """
    
    class LLMTracker:
        """Helper to capture response details after LLM call."""
        def __init__(self):
            self.response = None
            
        def set_response(self, response):
            """Set response to extract token counts."""
            self.response = response
    
    if not _enabled:
        tracker = LLMTracker()
        yield tracker
        return
    
    request_id = _current_request.get()
    if not request_id:
        tracker = LLMTracker()
        yield tracker
        return
    
    # Capture context before call
    session_id = _current_session.get()
    stack = _operation_stack.get([])
    parent = stack[-1] if stack else None
    
    # Start timing
    start_mono = time.perf_counter()
    start_wall = time.time()
    status = "ok"
    tracker = LLMTracker()
    
    try:
        yield tracker
    except Exception:
        status = "error"
        raise
    finally:
        # End timing
        end_mono = time.perf_counter()
        end_wall = time.time()
        
        # Build metadata
        token_metadata = {
            "model": model,
            "reasoning": reasoning_effort,
            "depth": depth,
            "input_messages": message_count,
        }
        
        # Extract token counts from response if available
        prompt_tokens = None
        completion_tokens = None
        total_tokens = None
        
        if status == "ok" and tracker.response:
            usage = getattr(tracker.response, 'usage', {})
            if isinstance(usage, dict):
                prompt_tokens = usage.get("prompt_tokens")
                completion_tokens = usage.get("completion_tokens")
                total_tokens = usage.get("total_tokens")
                token_metadata["reasoning_count"] = getattr(tracker.response, 'reasoning_count', 0)
        
        # Record timing with token counts
        _timing_records.append(TimingRecord(
            request_id=request_id,
            session_id=session_id,
            operation="llm_call",
            start_time=start_wall,
            end_time=end_wall,
            duration_s=end_mono - start_mono,
            parent_operation=parent,
            status=status,
            llm_depth=depth,
            model=model,
            reasoning_effort=reasoning_effort,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            metadata=token_metadata,
        ))


async def track_parallel_operations(
    operations: list[tuple[str, Any]],
    group_name: str = "parallel_group",
):
    """Track multiple operations running in parallel.
    
    Args:
        operations: List of (operation_name, coroutine) tuples
        group_name: Name for this group of parallel operations
        
    Returns:
        List of results in the same order as operations
        
    Usage:
        results = await track_parallel_operations([
            ("search_spotify", search_spotify_func()),
            ("search_devices", search_devices_func()),
        ], group_name="parallel_search")
    """
    if not _enabled:
        # Just run them in parallel without tracking
        _, coroutines = zip(*operations) if operations else ([], [])
        return await asyncio.gather(*coroutines)
    
    request_id = _current_request.get()
    if not request_id:
        # No active request, just run without tracking
        _, coroutines = zip(*operations) if operations else ([], [])
        return await asyncio.gather(*coroutines)
    
    parallel_id = str(uuid.uuid4())[:8]
    
    async def track_one(op_name: str, coro):
        """Track a single operation within the parallel group."""
        stack = _operation_stack.get([])
        parent = stack[-1] if stack else None
        
        start_mono = time.perf_counter()
        start_wall = time.time()
        status = "ok"
        
        try:
            result = await coro
            return result
        except Exception:
            status = "error"
            raise
        finally:
            end_mono = time.perf_counter()
            end_wall = time.time()
            duration_s = end_mono - start_mono
            
            session_id = _current_session.get()
            _timing_records.append(TimingRecord(
                request_id=request_id,
                session_id=session_id,
                operation=op_name,
                start_time=start_wall,
                end_time=end_wall,
                duration_s=duration_s,
                parent_operation=parent,
                parallel_group=parallel_id,
                status=status,
                metadata={"group_name": group_name},
            ))
    
    # Run all operations in parallel with tracking
    results = await asyncio.gather(
        *[track_one(name, coro) for name, coro in operations]
    )
    
    return results


def write_csv(path: Optional[Path] = None) -> None:
    """Write all timing records to CSV file.
    
    Schema V2 format with stable column order for analysis.
    Handles CSV escaping automatically via DictWriter.
    
    Args:
        path: Optional path override. If None, uses configured path.
    """
    if not _timing_records:
        return
    
    output_path = path or _csv_path
    if not output_path:
        return
    
    # Ensure parent directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Check if file exists to determine if we need headers
    file_exists = output_path.exists() and output_path.stat().st_size > 0
    
    # Column order is critical for analysis tools
    fieldnames = [
        "date",
        "start_time",
        "end_time",
        "duration_s",
        "request_id",
        "session_id",
        "operation",
        "llm_depth",
        "args",
        "model",
        "reasoning",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "status",
        "parent_op",
        "parallel_group",
        "metadata",
    ]
    
    with open(output_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
        
        if not file_exists:
            writer.writeheader()
        
        for record in sorted(_timing_records, key=lambda r: r.start_time):
            # Format datetime strings from timestamps
            start_dt = datetime.fromtimestamp(record.start_time)
            end_dt = datetime.fromtimestamp(record.end_time)
            
            writer.writerow({
                "date": start_dt.strftime("%Y-%m-%d"),
                "start_time": start_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],  # Include milliseconds
                "end_time": end_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "duration_s": f"{record.duration_s:.4f}",
                "request_id": record.request_id,
                "session_id": record.session_id or "",
                "operation": record.operation,
                "llm_depth": str(record.llm_depth) if record.llm_depth is not None else "",
                "args": record.args or "",
                "model": record.model or "",
                "reasoning": record.reasoning_effort or "",
                "prompt_tokens": str(record.prompt_tokens) if record.prompt_tokens is not None else "",
                "completion_tokens": str(record.completion_tokens) if record.completion_tokens is not None else "",
                "total_tokens": str(record.total_tokens) if record.total_tokens is not None else "",
                "status": record.status or "",
                "parent_op": record.parent_operation or "",
                "parallel_group": record.parallel_group or "",
                "metadata": str(record.metadata) if record.metadata else "",
            })
    
    _timing_records.clear()


def get_records() -> list[TimingRecord]:
    """Get all timing records (for testing/debugging)."""
    return _timing_records.copy()


def clear_records() -> None:
    """Clear all timing records."""
    _timing_records.clear()


def get_summary(request_id: Optional[str] = None) -> dict[str, Any]:
    """Get a summary of timing data.
    
    Args:
        request_id: Optional request ID to filter by. If None, summarizes all.
        
    Returns:
        Dictionary with summary statistics
    """
    records = _timing_records
    if request_id:
        records = [r for r in records if r.request_id == request_id]
    
    if not records:
        return {"total_records": 0}
    
    operations = {}
    for record in records:
        if record.operation not in operations:
            operations[record.operation] = []
        operations[record.operation].append(record.duration_ms)
    
    summary = {
        "total_records": len(records),
        "unique_requests": len(set(r.request_id for r in records)),
        "operations": {
            op: {
                "count": len(durations),
                "total_ms": sum(durations),
                "avg_ms": sum(durations) / len(durations),
                "min_ms": min(durations),
                "max_ms": max(durations),
            }
            for op, durations in operations.items()
        }
    }
    
    return summary

