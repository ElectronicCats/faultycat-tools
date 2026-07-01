"""Protocol-agnostic helpers for raw logic-analyzer (`la`) captures.

The firmware's ``la`` command streams the full GP0..GP7 bank verbatim
and never interprets it (see
``faultycat-firmware/docs/LOGIC_ANALYZER.md``); all decoding is
host-side. This module renders such a capture to VCD so it opens in
GTKWave / PulseView / sigrok with any decoder the operator picks. The
per-protocol decoders (I2C in :mod:`i2c_decode`, UART in
:mod:`uart_decode`) still work on the same sample bytes when the wiring
is known.

Input is the sample layout the firmware emits: one byte per sample,
each bit a channel (bit 0 = GP0 = CH0, …, 1=high 0=low), no per-sample
timestamp — sample ``i`` occurred at ``i * interval_us``.
"""

from __future__ import annotations

# VCD identifier codes for the 8 channels — one printable char each,
# starting at '!' (0x21), matching the per-protocol VCD writers.
_CODES = [chr(0x21 + i) for i in range(8)]


def samples_to_vcd(samples: bytes, interval_us: float, channels: int = 8) -> str:
    """Render a raw GP0..GP7 capture as a plain-text VCD (no library).

    Emits all ``channels`` (default 8) as 1-bit signals ``gp0``..``gp7``
    on a 1us timescale (matching ``interval_us``'s unit). Only channels
    that change are re-emitted, so the file stays compact.
    """
    channels = max(1, min(channels, 8))
    lines = ["$timescale 1us $end", "$scope module logic_analyzer $end"]
    for ch in range(channels):
        lines.append(f"$var wire 1 {_CODES[ch]} gp{ch} $end")
    lines.append("$upscope $end")
    lines.append("$enddefinitions $end")

    if not samples:
        lines.append("$dumpvars")
        lines.extend(f"x{_CODES[ch]}" for ch in range(channels))
        lines.append("$end")
        return "\n".join(lines) + "\n"

    def _bits(sample: int) -> list[int]:
        return [(sample >> ch) & 1 for ch in range(channels)]

    prev = _bits(samples[0])
    lines.append("$dumpvars")
    lines.extend(f"{prev[ch]}{_CODES[ch]}" for ch in range(channels))
    lines.append("$end")

    for i in range(1, len(samples)):
        cur = _bits(samples[i])
        if cur == prev:
            continue
        lines.append(f"#{int(i * interval_us)}")
        for ch in range(channels):
            if cur[ch] != prev[ch]:
                lines.append(f"{cur[ch]}{_CODES[ch]}")
        prev = cur

    return "\n".join(lines) + "\n"


__all__ = ["samples_to_vcd"]
