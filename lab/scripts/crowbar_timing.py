#!/usr/bin/env python3
"""Characterize the crowbar pulse TIMING as the firmware sees it.

Two things this measures WITHOUT a scope, from CrowbarStatus'
`pulse_width_ns_actual` / `delay_us_actual` (what the PIO actually
programmed for the last fire):

  * width quantization: requested width_ns vs actual (PIO ticks ~8 ns)
  * delay quantization:  requested delay_us vs actual

This is the *intended/quantized* timing, not the electrical pulse (rise/
fall, MOSFET response, jitter) — that needs a scope on the crowbar output
(see `--scope`).

SAFETY: this FIRES the crowbar (immediate trigger). Only run it with the
crowbar output clear (nothing connected, or just a scope probe) and the
shield on. Requires --yes to fire.

    python scripts/crowbar_timing.py --yes                 # width+delay readback
    python scripts/crowbar_timing.py --scope --trigger ext --count 200   # fire loop for a scope
"""

from __future__ import annotations

import argparse
import time

import faultycat as fc


def readback(cat, output):
    cb = cat.crowbar
    print(f"\n# width readback (output={output}, immediate, delay=0)")
    print(f"  {'requested_ns':>12}  {'actual_ns':>10}  {'delta':>6}")
    for w in [8, 16, 50, 100, 200, 500, 1000, 5000, 20000, 50000]:
        cb.trigger, cb.output, cb.delay_us, cb.width_ns = "immediate", output, 0, w
        cb.arm(); cb.fire()
        st = cb.status
        print(f"  {w:>12}  {st.pulse_width_ns_actual:>10}  {st.pulse_width_ns_actual - w:>6}")
        cb.disarm()

    print(f"\n# delay readback (output={output}, immediate, width=100ns)")
    print(f"  {'requested_us':>12}  {'actual_us':>10}  {'delta':>6}")
    for d in [0, 1, 5, 10, 50, 100, 500, 1000, 10000]:
        cb.trigger, cb.output, cb.delay_us, cb.width_ns = "immediate", output, d, 100
        cb.arm(); cb.fire()
        st = cb.status
        print(f"  {d:>12}  {st.delay_us_actual:>10}  {st.delay_us_actual - d:>6}")
        cb.disarm()


def scope_loop(cat, output, trigger, delay_us, width_ns, count, interval_s):
    """Fire repeatedly so a scope can measure the electrical pulse, its
    delay-from-trigger, and jitter (use the scope in persistence/average).
    ext trigger: the pulse only fires when a real edge arrives on the
    trigger input, so wire your trigger source (or scope trigger-out)."""
    cb = cat.crowbar
    from faultycmd.protocols import EngineError

    print(f"\n# scope loop: {count}x  trigger={trigger} output={output} "
          f"delay={delay_us}us width={width_ns}ns interval={interval_s}s")
    print("  probe: crowbar output (and the trigger input for delay). Ctrl-C to stop.")
    fired = 0
    for i in range(count):
        cb.trigger, cb.output, cb.delay_us, cb.width_ns = trigger, output, delay_us, width_ns
        try:
            cb.arm()
            cb.fire(trigger_timeout_ms=2000)
            fired += 1
        except EngineError as e:
            print(f"  [{i}] {e}")
        finally:
            cb.disarm()
        time.sleep(interval_s)
    print(f"  done: {fired}/{count} fired")


def main() -> int:
    ap = argparse.ArgumentParser(description="Crowbar pulse timing characterization.")
    ap.add_argument("--output", choices=["lp", "hp"], default="lp")
    ap.add_argument("--yes", action="store_true", help="confirm it's safe to fire the crowbar")
    ap.add_argument("--scope", action="store_true", help="fire-loop mode for a scope (instead of readback)")
    ap.add_argument("--trigger", choices=["immediate", "ext_rising", "ext_falling"], default="immediate")
    ap.add_argument("--delay-us", type=int, default=0)
    ap.add_argument("--width-ns", type=int, default=100)
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--interval", type=float, default=0.05)
    args = ap.parse_args()

    if not args.yes:
        print("This FIRES the crowbar. Ensure the output is clear (nothing / scope probe)")
        print("and the shield is on, then re-run with --yes.")
        return 1

    cat = fc.connect()
    try:
        if args.scope:
            scope_loop(cat, args.output, args.trigger, args.delay_us, args.width_ns,
                       args.count, args.interval)
        else:
            readback(cat, args.output)
    finally:
        cat.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
