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
    """Single timing record for analysis."""
    request_id: str
    operation: str
    start_time: float
    end_time: float
    duration_ms: float
    parent_operation: Optional[str] = None
    parallel_group: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    # Session tracking
    session_id: Optional[str] = None
    # LLM-specific fields (extracted from metadata for easier analysis)
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None
    depth: Optional[int] = None
    input_messages: Optional[int] = None
    reasoning_count: Optional[int] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


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
    """Extract LLM-specific fields from metadata for easier CSV analysis."""
    return {
        "model": metadata.get("model"),
        "reasoning_effort": metadata.get("reasoning"),
        "depth": metadata.get("depth"),
        "input_messages": metadata.get("input_messages"),
        "reasoning_count": metadata.get("reasoning_count"),
        "prompt_tokens": metadata.get("prompt_tokens"),
        "completion_tokens": metadata.get("completion_tokens"),
        "total_tokens": metadata.get("total_tokens"),
    }


@asynccontextmanager
async def track_request(request_name: str = "user_request", metadata: Optional[dict[str, Any]] = None):
    """Track an entire user request lifecycle.
    
    This should wrap the outermost operation (e.g., the plan_execute call).
    Creates a unique request_id that all nested operations will use.
    
    Usage:
        async with track_request("user_query", metadata={"prompt": "turn on lights"}):
            result = await agent.plan(user_input)
    """
    if not _enabled:
        yield
        return
    
    request_id = str(uuid.uuid4())[:8]
    token = _current_request.set(request_id)
    
    start = time.perf_counter()
    try:
        yield request_id
    finally:
        end = time.perf_counter()
        duration_ms = (end - start) * 1000
        
        session_id = _current_session.get()
        llm_fields = _extract_llm_fields(metadata or {})
        _timing_records.append(TimingRecord(
            request_id=request_id,
            session_id=session_id,
            operation=request_name,
            start_time=start,
            end_time=end,
            duration_ms=duration_ms,
            metadata=metadata or {},
            **llm_fields,
        ))
        
        _current_request.reset(token)


@asynccontextmanager
async def track_operation(
    operation_name: str,
    metadata: Optional[dict[str, Any]] = None,
):
    """Track a single operation within a request.
    
    Automatically captures the current request_id and parent operation from context.
    
    Usage:
        async with track_operation("llm_call", metadata={"model": "gpt-5"}):
            response = await client.responses.create(...)
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
    
    start = time.perf_counter()
    try:
        yield
    finally:
        end = time.perf_counter()
        duration_ms = (end - start) * 1000
        
        session_id = _current_session.get()
        llm_fields = _extract_llm_fields(metadata or {})
        _timing_records.append(TimingRecord(
            request_id=request_id,
            session_id=session_id,
            operation=operation_name,
            start_time=start,
            end_time=end,
            duration_ms=duration_ms,
            parent_operation=parent,
            metadata=metadata or {},
            **llm_fields,
        ))
        
        _operation_stack.reset(token)


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
        
        start = time.perf_counter()
        try:
            result = await coro
            return result
        finally:
            end = time.perf_counter()
            duration_ms = (end - start) * 1000
            
            session_id = _current_session.get()
            _timing_records.append(TimingRecord(
                request_id=request_id,
                session_id=session_id,
                operation=op_name,
                start_time=start,
                end_time=end,
                duration_ms=duration_ms,
                parent_operation=parent,
                parallel_group=parallel_id,
                metadata={"group_name": group_name},
            ))
    
    # Run all operations in parallel with tracking
    results = await asyncio.gather(
        *[track_one(name, coro) for name, coro in operations]
    )
    
    return results


def write_csv(path: Optional[Path] = None) -> None:
    """Write all timing records to CSV file.
    
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
    file_exists = output_path.exists()
    
    with open(output_path, "a", newline="", encoding="utf-8") as f:
        fieldnames = [
            "timestamp",
            "request_id",
            "session_id",
            "operation",
            "duration_ms",
            "parent_operation",
            "parallel_group",
            # LLM-specific columns
            "model",
            "reasoning_effort",
            "depth",
            "input_messages",
            "reasoning_count",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            # Catch-all for other metadata
            "metadata",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        
        if not file_exists:
            writer.writeheader()
        
        for record in _timing_records:
            writer.writerow({
                "timestamp": record.timestamp,
                "request_id": record.request_id,
                "session_id": record.session_id or "",
                "operation": record.operation,
                "duration_ms": f"{record.duration_ms:.2f}",
                "parent_operation": record.parent_operation or "",
                "parallel_group": record.parallel_group or "",
                # LLM columns
                "model": record.model or "",
                "reasoning_effort": record.reasoning_effort or "",
                "depth": str(record.depth) if record.depth is not None else "",
                "input_messages": str(record.input_messages) if record.input_messages is not None else "",
                "reasoning_count": str(record.reasoning_count) if record.reasoning_count is not None else "",
                "prompt_tokens": str(record.prompt_tokens) if record.prompt_tokens is not None else "",
                "completion_tokens": str(record.completion_tokens) if record.completion_tokens is not None else "",
                "total_tokens": str(record.total_tokens) if record.total_tokens is not None else "",
                # Remaining metadata
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

