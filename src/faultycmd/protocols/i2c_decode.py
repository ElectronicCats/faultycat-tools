"""I2C bus decoder for raw `la` captures.

Pure stdlib, no serial port involved — works equally well on a live
:class:`~faultycmd.protocols.scanner.LaCapture` or on a previously
saved hexdump, so it's split out of ``scanner.py``. Input is the same
sample layout the firmware emits: one byte per sample, bit0=SDA
bit1=SCL (1=high, 0=low), no per-sample timestamp — sample ``i``
occurred at ``i * interval_us``. VCD export is protocol-agnostic and
lives in :mod:`la_decode`; this module just decodes the SDA/SCL pair.
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


def _debounce(bits: list[int]) -> list[int]:
    """Drop isolated single-sample spikes (electrical noise/ringing).

    A sample that differs from both its neighbors, while those
    neighbors agree with each other, is a one-sample blip rather than
    a real level change — replace it with the surrounding level.
    Doesn't shift timestamps; a real transition that holds for >=2
    samples is untouched.
    """
    if len(bits) < 3:
        return list(bits)
    out = list(bits)
    for i in range(1, len(bits) - 1):
        if bits[i] != bits[i - 1] and bits[i - 1] == bits[i + 1]:
            out[i] = bits[i - 1]
    return out


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

    SDA/SCL are first run through :func:`_debounce` — at a few µs per
    sample, a single noisy sample (ringing, EMI from the same board's
    fault-injection hardware) reads as a real edge and the state
    machine otherwise reports phantom back-to-back START/STOP pairs.
    """
    events: list[I2cEvent] = []
    if not samples:
        return events

    sda_bits = _debounce([s & _SDA_MASK for s in samples])
    scl_bits = _debounce([(s & _SCL_MASK) >> 1 for s in samples])

    in_frame = False
    bit_count = 0
    shift = 0
    prev_sda = sda_bits[0]
    prev_scl = scl_bits[0]

    for i in range(1, len(samples)):
        sda = sda_bits[i]
        scl = scl_bits[i]
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


__all__ = ["I2cEvent", "decode_i2c"]
