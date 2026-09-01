#!/usr/bin/env python3
"""Find the usable target-UART baud range on FaultyCat, against a real peer.

Loopback CH0<->CH1 is NOT used — the TXS0108E is auto-direction and tying two
of its channels causes contention (it can wedge the board), and a same-UART
loopback matches itself at any requested baud anyway. Instead wire a real
serial peer (e.g. an FT232R) with SEPARATE lines and sweep both ends:

    FaultyCat CH0 (GP0, TX) -> peer RX
    FaultyCat CH1 (GP1, RX) <- peer TX
    GND <-> GND     (peer at 3.3 V logic)

For each baud we set BOTH ends, round-trip a bit-stressing pattern each way,
and require an exact match. The clean band's ends are the practical min/max
(bounded by the RP2040 PL011 divisor, the peer, and the level shifter).

    python scripts/uart_baud_sweep.py --peer /dev/ttyUSB0
"""

from __future__ import annotations

import argparse
import time

import serial

import faultycat as fc

# 0x55/0xAA = alternating bits (worst case for baud accuracy); 0x00/0xFF =
# longest low/high runs (worst case for edge recovery through the shifter).
PATTERN = bytes([0x55, 0xAA, 0x00, 0xFF, 0x0F, 0xF0, 0x55, 0xAA])

DEFAULT_BAUDS = [
    300, 1200, 9600, 19200, 38400, 57600, 115200,
    230400, 460800, 921600, 1000000, 1500000, 2000000, 3000000,
]


def _drain(read_fn, n, timeout):
    """Read up to n bytes within timeout using a byte-available callback."""
    deadline = time.monotonic() + timeout
    buf = b""
    while len(buf) < n and time.monotonic() < deadline:
        buf += read_fn(n - len(buf))
    return buf


def round_trip(cat, peer, baud, tries=3):
    # Change only the UART *wire* baud via the shell (set_baud). Do NOT
    # reopen the CDC3 data port at this baud — opening a FaultyCat CDC at
    # 1200 baud triggers the RP2040 magic-touch BOOTSEL and drops the board
    # to the bootloader. The CDC3 line-coding is irrelevant to the wire
    # speed anyway (USB CDC), so keep it fixed at the open() default.
    cat.uart.set_baud(baud)
    peer.baudrate = baud
    time.sleep(0.08)
    peer.reset_input_buffer()
    cat.uart.reset_input()
    tmo = max(0.05, len(PATTERN) * 10.0 / baud * 5 + 0.03)
    ab = ba = 0
    for _ in range(tries):
        # peer -> FaultyCat
        peer.reset_input_buffer(); cat.uart.reset_input()
        peer.write(PATTERN); peer.flush()
        if _drain(lambda k: cat.uart.read(k), len(PATTERN), tmo) == PATTERN:
            ab += 1
        # FaultyCat -> peer
        peer.reset_input_buffer(); cat.uart.reset_input()
        cat.uart.write(PATTERN)
        if _drain(peer.read, len(PATTERN), tmo) == PATTERN:
            ba += 1
    return ab, ba, tries


def main() -> int:
    ap = argparse.ArgumentParser(description="Sweep target-UART baud against a real serial peer.")
    ap.add_argument("--peer", default="/dev/ttyUSB0", help="peer serial device (default /dev/ttyUSB0)")
    ap.add_argument("--bauds", type=int, nargs="*", default=DEFAULT_BAUDS)
    ap.add_argument("--stop-after-fail", type=int, default=2, help="stop after N consecutive failing bauds")
    args = ap.parse_args()

    cat = fc.connect()
    cat.uart.open(baud=115200)
    peer = serial.Serial(args.peer, 115200, timeout=0)
    print(f"FaultyCat CH0/CH1  <->  peer {args.peer}\n")
    print(f"{'baud':>9}  peer->FC   FC->peer   verdict")

    clean, fails = [], 0
    for b in sorted(args.bauds):
        try:
            ab, ba, n = round_trip(cat, peer, b)
        except OSError as e:
            print(f"{b:>9}  I/O error: {e} — check wiring / replug")
            break
        ok = ab == n and ba == n
        print(f"{b:>9}  {ab}/{n}       {ba}/{n}       {'PASS' if ok else 'FAIL'}")
        if ok:
            clean.append(b); fails = 0
        else:
            fails += 1
            if fails >= args.stop_after_fail:
                print("  (stopping — consecutive failures, likely past the max)")
                break

    if clean:
        print(f"\nClean band: MIN {min(clean)} baud  ...  MAX {max(clean)} baud")
        if max(clean) >= max(args.bauds):
            print("  (max is >= the highest tested — raise --bauds or peer caps here)")
    else:
        print("\nNo clean baud — check the wiring (TX/RX crossed? GND? peer at 3.3 V?).")

    cat.uart.set_baud(115200)
    peer.close()
    cat.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
