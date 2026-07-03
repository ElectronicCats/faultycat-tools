"""Unit tests for faultycmd.protocols.uart_decode."""

from __future__ import annotations

from faultycmd.protocols.uart_decode import decode_uart


def _build_capture(
    bytes_to_send: list[int],
    baud: int,
    interval_us: int,
    idle_before: int = 300,
    idle_between_bits: float = 0,
    actual_baud: float | None = None,
) -> bytes:
    """Build a raw GP0..GP7 capture (rx wired to bit 0) with UART bytes
    back to back, separated by `idle_between_bits` bit-times of
    idle-high.

    `actual_baud`, if given, is the *real* bit rate the signal is built
    at (default: `baud`) while decode_uart is still told `baud` — this
    simulates a target whose UART peripheral (typically an integer
    clock divider) doesn't run at exactly the nominal rate.

    Levels are laid out from a running float position, only rounded at
    the point of emitting samples — an earlier version of this helper
    rounded each bit independently, which silently baked in its own
    baud mismatch and produced false failures unrelated to the decoder.
    """
    spb = (1_000_000 / (actual_baud if actual_baud is not None else baud)) / interval_us
    bits: list[int] = []
    pos = 0.0

    def push(level: int, duration_bits: float) -> None:
        nonlocal pos
        end = pos + duration_bits * spb
        bits.extend([level] * (round(end) - round(pos)))
        pos = end

    push(1, idle_before)
    for i, byte in enumerate(bytes_to_send):
        push(0, 1)  # start bit
        for k in range(8):
            push((byte >> k) & 1, 1)
        push(1, 1)  # stop bit
        if i != len(bytes_to_send) - 1:
            push(1, idle_between_bits)
    return bytes(bits)


def test_decode_uart_back_to_back_bytes_no_gap():
    # Standard UART framing guarantees only one stop bit (1 bit-time) of
    # high between frames — no idle gap required. A capture with three
    # bytes sent with zero gap between them must still decode all three,
    # not just the first (regression guard: an over-eager idle-guard
    # re-applied to every frame used to silently drop everything after
    # byte 1 in a continuous burst).
    samples = _build_capture(
        [0x41, 0x42, 0x43], baud=9600, interval_us=1, idle_between_bits=0
    )
    frames = decode_uart(samples, interval_us=1, rx_bit=0, baud=9600)
    assert [f.byte for f in frames] == [0x41, 0x42, 0x43]
    assert not any(f.framing_error for f in frames)


def test_decode_uart_bytes_with_idle_gap():
    samples = _build_capture(
        [0x41, 0x42, 0x43], baud=9600, interval_us=1, idle_between_bits=2
    )
    frames = decode_uart(samples, interval_us=1, rx_bit=0, baud=9600)
    assert [f.byte for f in frames] == [0x41, 0x42, 0x43]


def test_decode_uart_resyncs_after_gap_following_locked_run():
    # A locked run followed by a real idle gap, then another burst: the
    # decoder must drop the lock on the gap and reacquire it via the
    # idle-guard for the next burst, rather than getting stuck.
    first = _build_capture([0x41, 0x42], baud=9600, interval_us=1, idle_between_bits=0)
    gap = bytes([0xFF] * 50)  # idle-high padding between bursts
    second = _build_capture(
        [0x43], baud=9600, interval_us=1, idle_before=50, idle_between_bits=0
    )
    samples = first + gap + second
    frames = decode_uart(samples, interval_us=1, rx_bit=0, baud=9600)
    assert [f.byte for f in frames] == [0x41, 0x42, 0x43]


def test_decode_uart_empty_samples_returns_no_frames():
    assert decode_uart(b"", interval_us=1, rx_bit=0, baud=9600) == []


def test_decode_uart_long_continuous_burst_stays_locked():
    # Regression guard for the drift bug: re-deriving each locked frame's
    # start from the previous frame's already-rounded position let
    # quantization error random-walk off the true bit centers over a
    # long burst (spb = 104.1667 samples/bit at 9600 baud / 1us sampling
    # is never an integer). A ~50-byte continuous message used to decode
    # correctly for ~27 bytes and then turn to garbage.
    msg = list(b"Hello World! This is a UART test message 0123456789." * 5 + b"\r\n")
    samples = _build_capture(msg, baud=9600, interval_us=1)
    frames = decode_uart(samples, interval_us=1, rx_bit=0, baud=9600)
    assert bytes(f.byte for f in frames) == bytes(msg)
    assert not any(f.framing_error for f in frames)


def test_decode_uart_tolerates_realistic_baud_mismatch():
    # Real UART targets (especially MCU peripherals clocked from an
    # integer baud-rate divider) rarely run at exactly the nominal rate.
    # A per-frame resync (find_edge) should tolerate a couple of percent
    # of mismatch — comfortably inside typical UART tolerance specs —
    # without losing the whole rest of the burst.
    msg = list(b"Hello World! This is a UART test message 0123456789." * 5 + b"\r\n")
    samples = _build_capture(msg, baud=9600, interval_us=1, actual_baud=9756)  # +1.6%
    frames = decode_uart(samples, interval_us=1, rx_bit=0, baud=9600)
    assert bytes(f.byte for f in frames) == bytes(msg)
