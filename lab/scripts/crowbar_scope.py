#!/usr/bin/env python3
"""Measure the crowbar pulse on a Siglent SDS1000X-E, driven by FaultyCat.

Probe: scope CH1 on the crowbar gate (GP17 = LP, GP16 = HP) + GND. The PIO
drives the gate as a clean 0->3.3 V pulse of the configured width, so this
measures the real electrical width/edges (not just the firmware's quantized
value). FaultyCat fires (immediate), the scope single-triggers on the edge,
we read the width via SCPI.

Talks to the scope over USBTMC via pyvisa-py. Two Siglent quirks handled:
its output buffer keeps stale query responses (we flush at open), and SAST?
pipe-errors (we use a fixed settle delay instead of polling). Run with sudo
(raw USB access).

    sudo .venv/bin/python scripts/crowbar_scope.py --output lp
"""
from __future__ import annotations

import argparse
import time

import pyvisa


class Scope:
    def __init__(self):
        rm = pyvisa.ResourceManager("@py")
        res = [x for x in rm.list_resources() if x.startswith("USB0::")]
        if not res:
            raise RuntimeError("no USB scope found (is it on the rear USB-B?)")
        self.s = rm.open_resource(res[0])
        self.s.read_termination = "\n"
        self.s.write_termination = "\n"
        self.flush()

    def flush(self):
        self.s.timeout = 200
        try:
            while True:
                self.s.read()
        except Exception:
            pass
        self.s.timeout = 5000

    def w(self, cmd):
        self.s.write(cmd)

    def q(self, cmd):
        self.s.write(cmd)
        try:
            return self.s.read().strip()
        except Exception:
            self.flush()
            self.s.write(cmd)
            return self.s.read().strip()


def tdiv_for(width_ns):
    for w, td in [(150, "20NS"), (300, "50NS"), (700, "100NS"), (1500, "200NS"),
                  (7000, "1US"), (30000, "5US"), (200000, "20US")]:
        if width_ns <= w:
            return td
    return "50US"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", choices=["lp", "hp"], default="lp")
    ap.add_argument("--widths", type=int, nargs="*",
                    default=[100, 200, 500, 1000, 5000, 20000, 50000])
    ap.add_argument("--settle", type=float, default=0.35, help="wait after fire for the single capture")
    args = ap.parse_args()

    sc = Scope()
    idn = sc.q("*IDN?")
    print("scope:", idn)
    if "SDS" not in idn:
        print("!! IDN wrong — replug the scope USB and retry.")
        return 1

    sc.w("CHDR OFF")
    sc.w("C1:TRA ON"); sc.w("C1:VDIV 1V"); sc.w("C1:OFST 0V"); sc.w("C1:CPL D1M")
    sc.w("TRSE EDGE,SR,C1,HT,OFF"); sc.w("C1:TRLV 1.5V"); sc.w("C1:TRSL POS")

    import faultycat as fc
    cat = fc.connect(); cb = cat.crowbar

    def meas(param):
        sc.flush()                          # drain any stale response first
        r = sc.q(f"C1:PAVA? {param}")        # e.g. "WID,1.36E-07"
        try:
            return float(r.split(",")[-1])
        except ValueError:
            return None

    print(f"\n  {'req_ns':>8}  {'fw_ns':>8}  {'scope_ns':>10}  {'Vpp':>6}")
    for w in args.widths:
        sc.flush()
        sc.w(f"TDIV {tdiv_for(w)}")
        sc.w("TRMD SINGLE"); sc.w("ARM"); time.sleep(0.2)
        cb.trigger, cb.output, cb.delay_us, cb.width_ns = "immediate", args.output, 0, w
        cb.arm(); cb.fire()
        fw = cb.status.pulse_width_ns_actual
        cb.disarm()
        time.sleep(args.settle)
        m = meas("WID")          # seconds
        vpp = meas("PKPK")        # volts
        m_ns = f"{m * 1e9:.1f}" if m and m < 1 else "n/a"
        vs = f"{vpp:.2f}" if vpp else "n/a"
        print(f"  {w:>8}  {fw:>8}  {m_ns:>10}  {vs:>6}")

    cat.close(); sc.s.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
