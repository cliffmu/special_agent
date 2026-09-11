"""PCM streaming checks independent of Voice PE hardware and OpenAI access."""

import math
import random
import struct

import pytest

from experimental.live.audio import PCM16Resampler


def pcm(samples):
    return struct.pack(f"<{len(samples)}h", *samples)


def samples(data):
    return struct.unpack(f"<{len(data) // 2}h", data)


def convert_packets(data, sizes, **kwargs):
    resampler = PCM16Resampler(**kwargs)
    chunks, offset = [], 0
    for size in sizes:
        chunks.extend(resampler.feed(data[offset:offset + size]))
        offset += size
    chunks.extend(resampler.feed(data[offset:]))
    return chunks


def test_known_ramp_has_correct_rate_amplitude_and_little_endian_order():
    output = b"".join(PCM16Resampler().feed(pcm([0, 3000, 6000, 9000])))
    assert samples(output) == (0, 2000, 4000, 6000, 8000)


@pytest.mark.parametrize("packet_size", [1, 3, 127, 320, 641, 2048])
def test_fragmented_stream_matches_one_packet_exactly(packet_size):
    rng = random.Random(21)
    data = pcm([rng.randint(-32768, 32767) for _ in range(4097)])
    expected = b"".join(PCM16Resampler(max_chunk_bytes=65536).feed(data))
    packets = [packet_size] * (len(data) // packet_size)
    actual = convert_packets(data, packets)
    assert b"".join(actual) == expected
    assert all(0 < len(chunk) <= 1920 and len(chunk) % 2 == 0 for chunk in actual)


def test_half_sample_and_empty_packets_preserve_alignment_and_history():
    resampler = PCM16Resampler()
    data = pcm([0x1234, -0x1234, 12345, -12345])
    assert resampler.feed(b"") == []
    assert resampler.feed(data[:1]) == []
    assert resampler.feed(b"") == []
    chunks = resampler.feed(data[1:5])
    assert resampler.feed(b"") == []
    chunks.extend(resampler.feed(data[5:]))
    assert b"".join(chunks) == b"".join(PCM16Resampler().feed(data))


@pytest.mark.parametrize("level", [-32768, -12345, 0, 12345, 32767])
def test_constant_signal_keeps_its_amplitude_without_wraparound(level):
    data = pcm([level] * 1600)
    output = b"".join(convert_packets(data, [513, 479, 128, 3, 1021]))
    assert set(samples(output)) == {level}


def test_extreme_transitions_interpolate_without_clipping_or_sign_errors():
    data = pcm([32767, 32767, -32768, -32768])
    output = b"".join(convert_packets(data, [1, 1, 3, 1]))
    assert samples(output) == (32767, 32767, 10922, -32768, -32768)


def test_ten_seconds_of_fragmented_audio_has_no_accumulating_duration_drift():
    input_samples = 160000
    data = pcm([round(20000 * math.sin(2 * math.pi * 440 * i / 16000))
                for i in range(input_samples)])
    chunks = convert_packets(data, [641] * (len(data) // 641))
    output = b"".join(chunks)
    output_samples = len(output) // 2
    assert output_samples == (input_samples - 1) * 3 // 2 + 1
    assert abs(output_samples / 24000 - input_samples / 16000) <= 1 / 24000 + 1e-12
    assert max(samples(output)) <= 20000
    assert min(samples(output)) >= -20000


def test_output_chunks_stay_bounded_even_for_one_large_input_packet():
    chunks = PCM16Resampler(max_chunk_bytes=64).feed(pcm([1000] * 16000))
    assert all(0 < len(chunk) <= 64 and len(chunk) % 2 == 0 for chunk in chunks)
    assert sum(map(len, chunks)) == 23999 * 2


def test_reset_discards_partial_sample_and_old_interpolation_history():
    resampler = PCM16Resampler()
    resampler.feed(pcm([-32768] * 100) + b"\xff")
    resampler.reset()
    data = pcm([1000, 2000, 3000])
    assert resampler.feed(data) == PCM16Resampler().feed(data)


@pytest.mark.parametrize("size", [0, 1, 3, -2, 65538, 64.0, True])
def test_invalid_chunk_limit_is_rejected(size):
    with pytest.raises(ValueError, match="even integer"):
        PCM16Resampler(max_chunk_bytes=size)
