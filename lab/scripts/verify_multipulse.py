#!/usr/bin/env python3
"""Verify FaultyCat's multipulse: repeat=N must produce exactly N pulses.

Counts the crowbar GATE pulses (GP17, 3.3 V logic — no high voltage) with an
Arduino Uno running arduino/pulse_counter/pulse_counter.ino (Timer1 hardware
counter on D5). For each N we zero the counter, fire an immediate crowbar burst
of `repeat=N`, and read the count back — it must equal N.

    python scripts/verify_multipulse.py --counter /dev/ttyACM9   # Arduino port

Wiring: FaultyCat crowbar gate GP17 -> Arduino D5, common GND. (immediate
trigger, LP output, nothing on the crowbar output -> no discharge.)
"""
from __future__ import annotations

import argparse
import time

import serial

import faultycat as fc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--counter", required=True, help="Arduino serial port (pulse_counter.ino)")
    ap.add_argument("--repeats", type=int, nargs="*", default=[1, 2, 4, 10, 35])
    ap.add_argument("--width-ns", type=int, default=1000)
    ap.add_argument("--gap-us", type=int, default=5, help="delay between pulses (us)")
    args = ap.parse_args()

    ard = serial.Serial(args.counter, 115200, timeout=1.0)
    time.sleep(2.0)  # Arduino resets on port open
    ard.reset_input_buffer()

    def counter(cmd: str) -> str:
        ard.write(cmd.encode())
        return ard.readline().decode(errors="replace").strip()

    cat = fc.connect()
    cb = cat.crowbar
    print(f"gate pulses via {args.counter}  |  width={args.width_ns}ns gap={args.gap_us}us\n")
    print(f"  {'repeat':>7}  {'counted':>7}  verdict")
    ok = True
    for n in args.repeats:
        counter("z")  # zero the counter
        cb.trigger, cb.output = "immediate", "lp"
        cb.delay_us, cb.width_ns, cb.repeat = args.gap_us, args.width_ns, n
        cb.arm(); cb.fire(); cb.disarm()
        time.sleep(0.1)
        got = counter("r")
        try:
            match = int(got) == n
        except ValueError:
            match = False
        ok = ok and match
        print(f"  {n:>7}  {got:>7}  {'PASS' if match else 'FAIL'}")

    cat.close(); ard.close()
    print("\n" + ("ALL PASS — repeat produces exactly N pulses." if ok else "MISMATCH — check wiring/level (GP17 is 3.3 V into a 5 V Uno)."))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
