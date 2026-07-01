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

    frames: list[UartFrame] = []
    i = 1
    while i < len(rx):
        # falling edge = start bit (idle is high, start is low)
        if rx[i - 1] == 1 and rx[i] == 0:
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
            i = round(start_idx + 10 * spb)
        else:
            i += 1

    return frames


__all__ = ["UartFrame", "decode_uart"]
