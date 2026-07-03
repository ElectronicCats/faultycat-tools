"""UART bus decoder for raw ``la`` captures.

Pure stdlib, no serial port involved — works equally well on a live
:class:`~faultycmd.protocols.scanner.LaCapture` or on a previously
saved hexdump. Input is the same sample layout the firmware emits: one
byte per sample, each bit corresponds to GP0..GP7 (bit 0 = GP0 = CH0).
Sample ``i`` occurred at ``i * interval_us``. VCD export is
protocol-agnostic and lives in :mod:`la_decode`; this module just
decodes whichever channel carries RX.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class UartFrame:
    t_us: float
    byte: int
    framing_error: bool = False


def decode_uart(
    samples: bytes,
    interval_us: float,
    rx_bit: int,
    baud: int,
) -> list[UartFrame]:
    """Decode UART frames from a raw logic-analyzer capture.

    ``rx_bit`` is the bit position (0-7) of the RX signal within each
    sample byte — typically ``rx_gp % 8`` (same as ``rx_gp`` when the
    scanner header uses GP0..GP7).

    Implements standard 8N1 UART decoding: start bit, 8 data bits
    (LSB first), stop bit. A stop bit of 0 sets ``framing_error``.
    """
    if not samples:
        return []

    spb = (1_000_000 / baud) / interval_us
    rx = [(s >> rx_bit) & 1 for s in samples]

    # Finding the *first* start bit is ambiguous: any lone `1` data bit
    # followed by a `0` one looks identical to a stop bit followed by a
    # start bit, so a mid-byte transition can masquerade as a start edge.
    # This requires 1.5 bit-times of stable idle-high right before a
    # candidate edge, which no single data-bit transition inside a byte
    # can produce — a probabilistic mitigation (it can still misframe in
    # the first bit-time of a capture, where no prior samples exist to
    # check), now backstopped by a real firmware trigger for the raw `la
    # capture` path — see
    # faultycat-firmware/docs/LA_CAPTURE_TRIGGER_IMPLEMENTATION_PLAN.md.
    #
    # Once a clean frame is found, though, this guard must NOT be re-applied
    # to every subsequent byte: standard UART only guarantees one stop bit
    # (1 bit-time) of high between back-to-back frames, which is short of
    # the 1.5-bit-time idle-guard — re-scanning with the guard here would
    # silently drop every byte after the first in a continuous burst (the
    # original bug this comment used to gloss over). So after a clean
    # frame, `locked` projects where the next start bit *should* be
    # (`expected_start`) instead of re-running the guard.
    #
    # That projection is never trusted outright, though: a real
    # transmitter (especially an MCU UART peripheral clocked from an
    # integer baud-rate divider) is rarely running at *exactly* the
    # requested baud, so trusting a fixed bit clock projected across many
    # bytes accumulates error until it misses a bit boundary entirely —
    # this reproduced as a long synthetic burst decoding cleanly for
    # ~27 bytes and then turning to garbage. A real UART receiver instead
    # re-synchronizes its bit clock at every start bit; `find_edge` does
    # the same by re-locking onto the actual observed 1->0 transition
    # within `resync_window` samples of the projection, so drift never
    # compounds past a single frame's worth of mismatch.
    idle_span = max(1, round(1.5 * spb))
    resync_window = max(1, round(0.25 * spb))

    def _is_real_start(cand: int) -> bool:
        # A single noisy/floating-line sample dipping low looks exactly
        # like a start-bit edge to a 1-sample check, so require the low
        # to still hold at the bit's midpoint (same deglitch a real UART
        # receiver's majority-vote sampling gives for free) — otherwise a
        # lone glitch during idle gets decoded as a full bogus frame.
        if not (0 < cand < len(rx) and rx[cand - 1] == 1 and rx[cand] == 0):
            return False
        mid = cand + round(0.5 * spb)
        return mid < len(rx) and rx[mid] == 0

    def find_edge(center: int) -> int | None:
        if _is_real_start(center):
            return center
        for delta in range(1, resync_window + 1):
            for cand in (center - delta, center + delta):
                if _is_real_start(cand):
                    return cand
        return None

    frames: list[UartFrame] = []
    i = 1
    locked = False
    expected_start = 0.0
    while i < len(rx):
        if locked:
            est = round(expected_start)
            found = find_edge(est)
            if found is None:
                # No edge near the projected slot — either a genuine gap
                # (idle) or the mismatch exceeded the resync window;
                # either way, drop the lock and resume idle-guarded
                # scanning rather than trusting an unverified position.
                locked = False
                i = est + 1
                continue
            start_idx = found
        else:
            # falling edge = start bit (idle is high, start is low),
            # preceded by a genuine idle period rather than just the one
            # prior sample.
            idle_start = max(0, i - idle_span)
            if not (_is_real_start(i) and all(b == 1 for b in rx[idle_start:i])):
                i += 1
                continue
            start_idx = i

        stop_idx = round(start_idx + 9.5 * spb)
        if stop_idx >= len(rx):
            break  # incomplete frame at end of capture

        value = 0
        for k in range(8):
            di = round(start_idx + (k + 1.5) * spb)
            if di < len(rx):
                value |= rx[di] << k  # LSB first

        framing_error = rx[stop_idx] == 0
        frames.append(
            UartFrame(
                t_us=start_idx * interval_us,
                byte=value,
                framing_error=framing_error,
            )
        )
        # Project from THIS frame's confirmed true start, not from a
        # multi-frame-old anchor — bounds the next resync search to one
        # frame's worth of baud mismatch instead of letting it compound.
        expected_start = start_idx + 10 * spb
        i = round(expected_start)
        # Stay locked into the next bit-clock slot only if this frame
        # looked clean — a bad stop bit means the bit clock itself may
        # have drifted more than the resync window tolerates, so re-earn
        # the lock via the idle-guard instead.
        locked = not framing_error

    return frames


__all__ = ["UartFrame", "decode_uart"]
