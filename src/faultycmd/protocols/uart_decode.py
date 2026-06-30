"""UART bus decoder for raw ``uart la`` captures.

Pure stdlib, no serial port involved — works equally well on a live
:class:`~faultycmd.protocols.scanner.UartLaCapture` or on a previously
saved hexdump. Input is the same sample layout the firmware emits: one
byte per sample, each bit corresponds to GP0..GP7 (bit 0 = GP0 = CH0).
Sample ``i`` occurred at ``i * interval_us``.
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


def samples_to_vcd_uart(
    samples: bytes, interval_us: float, rx_gp: int, tx_gp: int
) -> str:
    """Render a raw UART capture as a plain-text VCD.

    Opens directly in GTKWave / PulseView / sigrok. Uses a 1us
    timescale and two 1-bit signals, ``rx`` and ``tx``.
    """
    rx_bit = rx_gp % 8
    tx_bit = tx_gp % 8

    lines = [
        "$timescale 1us $end",
        "$scope module uart_la $end",
        f"$var wire 1 ! rx_gp{rx_gp} $end",
        f'$var wire 1 " tx_gp{tx_gp} $end',
        "$upscope $end",
        "$enddefinitions $end",
    ]

    if not samples:
        lines.append("$dumpvars")
        lines.append("x!")
        lines.append('x"')
        lines.append("$end")
        return "\n".join(lines) + "\n"

    first = samples[0]
    prev_rx = (first >> rx_bit) & 1
    prev_tx = (first >> tx_bit) & 1

    lines.append("$dumpvars")
    lines.append(f"{prev_rx}!")
    lines.append(f'{prev_tx}"')
    lines.append("$end")

    for i in range(1, len(samples)):
        sample = samples[i]
        rx_val = (sample >> rx_bit) & 1
        tx_val = (sample >> tx_bit) & 1
        if rx_val == prev_rx and tx_val == prev_tx:
            continue
        lines.append(f"#{int(i * interval_us)}")
        if rx_val != prev_rx:
            lines.append(f"{rx_val}!")
        if tx_val != prev_tx:
            lines.append(f'{tx_val}"')
        prev_rx = rx_val
        prev_tx = tx_val

    return "\n".join(lines) + "\n"


__all__ = ["UartFrame", "decode_uart", "samples_to_vcd_uart"]
