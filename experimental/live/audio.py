"""Streaming microphone conversion for Voice PE (16 kHz) and Live (24 kHz)."""

from __future__ import annotations

import asyncio
import audioop  # stdlib through Python 3.12; audioop-lts on Python 3.13+.
import sys
import time


class AudioPacer:
    """Pace PCM16 mono24k on an audio clock that does not accumulate loop jitter.

    Ordinary late wakeups shorten the next wait. A stall longer than 40 ms
    rebases the clock instead of replaying accumulated audio in a fast burst.
    The caller still owns any queue-age and transport-timeout limits.
    """

    def __init__(self, *, clock=time.monotonic, sleep=asyncio.sleep):
        self._clock, self._sleep = clock, sleep
        self._deadline = None

    async def wait_for_chunk(self, byte_count: int) -> None:
        """Wait until this chunk is due, then advance by its sample duration."""
        if byte_count <= 0 or byte_count % 2:
            raise ValueError("A paced PCM16 chunk must contain complete samples")
        now = self._clock()
        if self._deadline is None or now - self._deadline > 0.04:
            self._deadline = now
        await self._sleep(max(0, self._deadline - now))
        # The event loop can also stall during sleep, so check on both sides.
        now = self._clock()
        if now - self._deadline > 0.04:
            self._deadline = now
        self._deadline += byte_count / 48000


class PCM16Resampler:
    """Convert mono PCM16LE without resetting interpolation at packet boundaries.

    ``feed`` returns available audio immediately in chunks of at most 40 ms by
    default. A packet may end halfway through a sample; its last byte is retained
    until the next feed. Internal input buffers and the retained state are bounded.

    Linear interpolation cannot extrapolate beyond the last input sample. After
    N complete input samples, output has floor((N - 1) * 3 / 2) + 1 samples (N > 0).
    This endpoint delay is at most one output sample and does not accumulate.
    ``reset`` starts a new stream and discards any incomplete input sample.
    """

    input_rate = 16000
    output_rate = 24000

    def __init__(self, *, max_chunk_bytes: int = 1920):
        if (not isinstance(max_chunk_bytes, int) or isinstance(max_chunk_bytes, bool)
                or max_chunk_bytes < 2 or max_chunk_bytes > 65536 or max_chunk_bytes % 2):
            raise ValueError("max_chunk_bytes must be an even integer between 2 and 65536")
        self.max_chunk_bytes = max_chunk_bytes
        self._input_chunk_bytes = max(2, max_chunk_bytes // 2 * self.input_rate // self.output_rate * 2)
        self.reset()

    def reset(self) -> None:
        """Clear interpolation history and partial input when a session ends."""
        self._state = None
        self._tail = b""

    def feed(self, data: bytes) -> list[bytes]:
        """Return aligned PCM16LE chunks, retaining at most one input byte."""
        view = memoryview(data).cast("B")
        chunks = []
        for offset in range(0, len(view), self._input_chunk_bytes):
            fragment = self._tail + view[offset:offset + self._input_chunk_bytes].tobytes()
            aligned_length = len(fragment) & ~1
            self._tail = fragment[aligned_length:]
            if not aligned_length:
                continue
            fragment = fragment[:aligned_length]
            if sys.byteorder == "big":
                fragment = audioop.byteswap(fragment, 2)
            converted, self._state = audioop.ratecv(
                fragment, 2, 1, self.input_rate, self.output_rate, self._state,
            )
            if sys.byteorder == "big":
                converted = audioop.byteswap(converted, 2)
            chunks.extend(converted[start:start + self.max_chunk_bytes]
                          for start in range(0, len(converted), self.max_chunk_bytes))
        return chunks
