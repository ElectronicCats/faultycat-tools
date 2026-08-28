#!/usr/bin/env python3
"""Real voltage-glitch against an Arduino Uno target (glitch_target.ino).

Crowbar OUTPUT -> Arduino 5V rail. Firing shorts VCC briefly; a deep/long
enough dip browns the MCU out and it reboots, reprinting "BOOT #n". We escalate
pulse width & repeat until we see that reset, reading the target's USB serial.

    python scripts/glitch_arduino.py --target /dev/ttyACM5

Safety: shorting a USB-powered rail can trip the Uno's polyfuse or the PC USB
port (recoverable — cooldown / replug). Start gentle; --max caps the width.
"""
from __future__ import annotations

import argparse
import time

import serial

import faultycat as fc

# (width_ns, repeat) — gentle -> aggressive.
PLAN = [
    (500, 1), (2_000, 1), (10_000, 1), (50_000, 1),
    (50_000, 5), (50_000, 20), (50_000, 100),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, help="Arduino USB serial port")
    ap.add_argument("--output", default="hp", choices=["lp", "hp"])
    ap.add_argument("--gap-us", type=int, default=1)
    ap.add_argument("--max", type=int, default=50_000, help="cap width_ns")
    ap.add_argument("--cooldown", type=float, default=0.4)
    args = ap.parse_args()

    tgt = serial.Serial(args.target, 115200, timeout=0.3)
    time.sleep(2.0)  # Uno resets on open
    tgt.readline()   # consume the boot banner from opening the port
    tgt.reset_input_buffer()

    cat = fc.connect()
    cb = cat.crowbar
    print(f"target {args.target}  output={args.output}\n")
    print(f"  {'width_ns':>9} {'rep':>4}  outcome")

    hit = False
    for width, rep in PLAN:
        if width > args.max:
            break
        tgt.reset_input_buffer()
        cb.trigger, cb.output = "immediate", args.output
        cb.delay_us, cb.width_ns, cb.repeat = args.gap_us, width, rep
        cb.arm(); cb.fire(); cb.disarm()
        time.sleep(args.cooldown)
        try:
            out = tgt.read(256)  # a reboot lands "BOOT #n" here
        except serial.SerialException:
            # USB dropped: only a full-board power brownout does this (a DTR
            # reset never drops the port) -> the crowbar reached the rail.
            print(f"  {width:>9} {rep:>4}  RESET (USB brownout — whole board power-cycled)")
            hit = True
            break
        if b"BOOT" in out:
            print(f"  {width:>9} {rep:>4}  RESET -> {out.split(b'BOOT', 1)[1].decode('latin1').strip()[:16]}")
            hit = True
            break
        print(f"  {width:>9} {rep:>4}  no reset")

    cat.close(); tgt.close()
    print("\n" + ("Got a reset — crowbar reaches the rail." if hit
                  else "No reset within the plan; raise --max or check the wire is on the 5V pin."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
