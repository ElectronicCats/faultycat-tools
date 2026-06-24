"""I2C bus decoder for raw `i2c la` captures.

Pure stdlib, no serial port involved — works equally well on a live
:class:`~faultycmd.protocols.scanner.I2cLaCapture` or on a previously
saved hexdump, so it's split out of ``scanner.py``. Input is the same
sample layout the firmware emits (``i2c_la.h``): one byte per sample,
bit0=SDA bit1=SCL (1=high, 0=low), no per-sample timestamp — sample
``i`` occurred at ``i * interval_us``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

_SDA_MASK = 0x01
_SCL_MASK = 0x02

I2cEventKind = Literal["START", "STOP", "BYTE", "ACK", "NACK"]


@dataclass
class I2cEvent:
    t_us: float
    kind: I2cEventKind
    value: int | None = None


def decode_i2c(samples: bytes, interval_us: float) -> list[I2cEvent]:
    """Decode a raw SDA/SCL trace into I2C bus events.

    State machine driven by edges:
      - SDA falls while SCL is high -> START
      - SDA rises while SCL is high -> STOP
      - data bits are sampled on SCL's rising edge, MSB first; once
        8 bits are collected they emit a BYTE event, and the 9th
        clock samples the ACK/NACK bit
    Bit sampling only happens between a START and the matching STOP —
    edges outside that window are ignored (idle bus / partial frame
    at the start of the capture).
    """
    events: list[I2cEvent] = []
    if not samples:
        return events

    in_frame = False
    bit_count = 0
    shift = 0
    prev_sda = samples[0] & _SDA_MASK
    prev_scl = (samples[0] & _SCL_MASK) >> 1

    for i in range(1, len(samples)):
        sample = samples[i]
        sda = sample & _SDA_MASK
        scl = (sample & _SCL_MASK) >> 1
        t_us = i * interval_us

        if scl == 1 and prev_scl == 1 and sda != prev_sda:
            if prev_sda == 1 and sda == 0:
                events.append(I2cEvent(t_us, "START"))
                in_frame = True
                bit_count = 0
                shift = 0
            elif prev_sda == 0 and sda == 1:
                events.append(I2cEvent(t_us, "STOP"))
                in_frame = False
                bit_count = 0
                shift = 0
        elif in_frame and scl == 1 and prev_scl == 0:
            # Rising SCL edge during a frame: sample SDA.
            if bit_count < 8:
                shift = (shift << 1) | sda
                bit_count += 1
                if bit_count == 8:
                    events.append(I2cEvent(t_us, "BYTE", shift))
            else:
                events.append(I2cEvent(t_us, "NACK" if sda else "ACK", sda))
                bit_count = 0
                shift = 0

        prev_sda = sda
        prev_scl = scl

    return events


def samples_to_vcd(samples: bytes, interval_us: float, sda_gp: int, scl_gp: int) -> str:
    """Render a raw capture as a plain-text VCD (no library needed).

    Opens directly in GTKWave / PulseView / sigrok. Uses a 1us
    timescale (matching ``interval_us``'s unit) and two 1-bit
    signals, ``sda`` and ``scl``, identified by VCD codes ``!``/``"``.
    """
    lines = [
        "$timescale 1us $end",
        "$scope module i2c_la $end",
        f"$var wire 1 ! sda_gp{sda_gp} $end",
        f'$var wire 1 " scl_gp{scl_gp} $end',
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
    prev_sda = first & _SDA_MASK
    prev_scl = (first & _SCL_MASK) >> 1

    lines.append("$dumpvars")
    lines.append(f"{prev_sda}!")
    lines.append(f'{prev_scl}"')
    lines.append("$end")

    for i in range(1, len(samples)):
        sample = samples[i]
        sda = sample & _SDA_MASK
        scl = (sample & _SCL_MASK) >> 1
        if sda == prev_sda and scl == prev_scl:
            continue
        lines.append(f"#{int(i * interval_us)}")
        if sda != prev_sda:
            lines.append(f"{sda}!")
        if scl != prev_scl:
            lines.append(f'{scl}"')
        prev_sda = sda
        prev_scl = scl

    return "\n".join(lines) + "\n"


__all__ = ["I2cEvent", "decode_i2c", "samples_to_vcd"]
