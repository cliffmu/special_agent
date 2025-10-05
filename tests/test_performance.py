"""Tests for performance tracking module."""
import asyncio
from pathlib import Path
import tempfile

import pytest

from utils.performance import (
    configure,
    track_request,
    track_operation,
    get_records,
    clear_records,
    write_csv,
    get_summary,
    is_enabled,
)


@pytest.fixture
def temp_csv():
    """Create a temporary CSV file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        yield Path(f.name)


@pytest.fixture(autouse=True)
def reset_performance():
    """Reset performance tracking between tests."""
    configure(enabled=False)
    clear_records()
    yield
    clear_records()


@pytest.mark.asyncio
async def test_tracking_disabled_by_default():
    """Test that tracking is disabled by default."""
    assert not is_enabled()
    
    async with track_request("test"):
        async with track_operation("test_op"):
            await asyncio.sleep(0.01)
    
    assert len(get_records()) == 0


@pytest.mark.asyncio
async def test_basic_tracking(temp_csv):
    """Test basic request and operation tracking."""
    configure(enabled=True, csv_path=temp_csv)
    
    async with track_request("test_request") as request_id:
        async with track_operation("operation_1"):
            await asyncio.sleep(0.01)
        
        async with track_operation("operation_2", metadata={"key": "value"}):
            await asyncio.sleep(0.01)
    
    records = get_records()
    assert len(records) == 3  # 1 request + 2 operations
    
    # Check request record
    request_record = [r for r in records if r.operation == "test_request"][0]
    assert request_record.request_id == request_id
    assert request_record.duration_ms > 0
    
    # Check operation records
    op1 = [r for r in records if r.operation == "operation_1"][0]
    assert op1.request_id == request_id
    assert op1.duration_ms > 10  # At least 10ms
    
    op2 = [r for r in records if r.operation == "operation_2"][0]
    assert op2.request_id == request_id
    assert op2.metadata == {"key": "value"}


@pytest.mark.asyncio
async def test_nested_operations(temp_csv):
    """Test hierarchical operation tracking."""
    configure(enabled=True, csv_path=temp_csv)
    
    async with track_request("test_request"):
        async with track_operation("parent_op"):
            await asyncio.sleep(0.01)
            
            async with track_operation("child_op"):
                await asyncio.sleep(0.01)
    
    records = get_records()
    child = [r for r in records if r.operation == "child_op"][0]
    assert child.parent_operation == "parent_op"


@pytest.mark.asyncio
async def test_csv_export(temp_csv):
    """Test CSV export functionality."""
    configure(enabled=True, csv_path=temp_csv)
    
    async with track_request("test_request"):
        async with track_operation("test_op"):
            await asyncio.sleep(0.01)
    
    write_csv()
    
    # Check CSV file was created
    assert temp_csv.exists()
    
    # Read and verify contents
    with open(temp_csv, "r") as f:
        lines = f.readlines()
        assert len(lines) == 3  # header + 2 records
        assert "request_id" in lines[0]
        assert "test_request" in lines[1]
        assert "test_op" in lines[2]


@pytest.mark.asyncio
async def test_summary_statistics(temp_csv):
    """Test summary statistics."""
    configure(enabled=True, csv_path=temp_csv)
    
    # Create multiple requests
    for i in range(3):
        async with track_request("test_request"):
            async with track_operation("fast_op"):
                await asyncio.sleep(0.01)
            async with track_operation("slow_op"):
                await asyncio.sleep(0.02)
    
    summary = get_summary()
    assert summary["total_records"] == 9  # 3 * (1 request + 2 ops)
    assert summary["unique_requests"] == 3
    assert "fast_op" in summary["operations"]
    assert "slow_op" in summary["operations"]
    
    # Fast op should be faster on average
    assert summary["operations"]["fast_op"]["avg_ms"] < summary["operations"]["slow_op"]["avg_ms"]


@pytest.mark.asyncio
async def test_parallel_simulation(temp_csv):
    """Test tracking of parallel operations (simulated)."""
    configure(enabled=True, csv_path=temp_csv)
    
    async def tool_a():
        async with track_operation("tool_a"):
            await asyncio.sleep(0.05)
        return "result_a"
    
    async def tool_b():
        async with track_operation("tool_b"):
            await asyncio.sleep(0.03)
        return "result_b"
    
    async with track_request("parallel_test"):
        # Run tools in parallel
        results = await asyncio.gather(tool_a(), tool_b())
    
    assert results == ["result_a", "result_b"]
    
    records = get_records()
    tool_a_record = [r for r in records if r.operation == "tool_a"][0]
    tool_b_record = [r for r in records if r.operation == "tool_b"][0]
    
    # Both should be part of same request
    assert tool_a_record.request_id == tool_b_record.request_id


@pytest.mark.asyncio
async def test_clear_records(temp_csv):
    """Test clearing records."""
    configure(enabled=True, csv_path=temp_csv)
    
    async with track_request("test"):
        async with track_operation("test_op"):
            await asyncio.sleep(0.01)
    
    assert len(get_records()) > 0
    
    clear_records()
    assert len(get_records()) == 0


@pytest.mark.asyncio
async def test_multiple_requests_different_ids(temp_csv):
    """Test that each request gets a unique ID."""
    configure(enabled=True, csv_path=temp_csv)
    
    request_ids = []
    
    for _ in range(3):
        async with track_request("test") as req_id:
            request_ids.append(req_id)
            await asyncio.sleep(0.01)
    
    # All request IDs should be unique
    assert len(set(request_ids)) == 3


@pytest.mark.asyncio
async def test_csv_append_mode(temp_csv):
    """Test that CSV appends rather than overwrites."""
    configure(enabled=True, csv_path=temp_csv)
    
    # First request
    async with track_request("request_1"):
        pass
    write_csv()
    
    # Second request
    async with track_request("request_2"):
        pass
    write_csv()
    
    # Read CSV and verify both requests are present
    with open(temp_csv, "r") as f:
        content = f.read()
        assert "request_1" in content
        assert "request_2" in content


@pytest.mark.asyncio
async def test_metadata_serialization(temp_csv):
    """Test that metadata is properly serialized to CSV."""
    configure(enabled=True, csv_path=temp_csv)
    
    metadata = {
        "model": "gpt-5",
        "reasoning": "low",
        "tools": ["tool1", "tool2"]
    }
    
    async with track_request("test"):
        async with track_operation("test_op", metadata=metadata):
            await asyncio.sleep(0.01)
    
    write_csv()
    
    with open(temp_csv, "r") as f:
        content = f.read()
        assert "gpt-5" in content
        assert "low" in content

