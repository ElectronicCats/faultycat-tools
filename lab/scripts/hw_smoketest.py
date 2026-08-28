#!/usr/bin/env python3
"""Staged, safety-gated smoke test for a real FaultyCat v3 board.

Run this with the board plugged in to check the whole stack end to end.
Stages escalate in risk; nothing fires high voltage unless you explicitly
pass --fire AND confirm at the prompt.

    python scripts/hw_smoketest.py            # stages 0-2: detect, connect, read status (SAFE)
    python scripts/hw_smoketest.py --scan     # + SWD/I2C scan (touches target pins, no HV)
    python scripts/hw_smoketest.py --fire      # + ONE guarded EMFI shot (HIGH VOLTAGE)

SAFETY: --fire arms and discharges the EMFI HV capacitor. Only use it with
the plastic shield installed and with a target you intend to glitch (or
nothing) under the coil. See the FaultyCat hardware README.
"""

from __future__ import annotations

import argparse
import sys


def hr(title: str) -> None:
    print(f"\n\033[1;35m== {title} ==\033[0m")


def ok(msg: str) -> None:
    print(f"  \033[32m✓\033[0m {msg}")


def warn(msg: str) -> None:
    print(f"  \033[33m!\033[0m {msg}")


def fail(msg: str) -> None:
    print(f"  \033[31m✗\033[0m {msg}")


def stage_detect() -> bool:
    hr("Stage 0 · detect  (harmless)")
    from serial.tools import list_ports

    hits = [p for p in list_ports.comports() if p.vid == 0x1209 and p.pid == 0xFA17]
    if not hits:
        fail("No FaultyCat found (VID 0x1209 / PID 0xFA17).")
        warn("Plug in the board, check the USB cable is data-capable, and re-run.")
        return False
    ok(f"FaultyCat detected — {len(hits)} CDC interface(s):")
    for p in hits:
        print(f"      {p.device}   {p.hwid}")
    return True


def stage_connect(args):
    hr("Stage 1 · connect + version  (read-only)")
    import faultycat as fc

    try:
        cat = fc.connect(
            scanner=not args.no_scanner,
            uart=False,  # never enable the bridge in a smoke test
            allow_version_mismatch=args.ignore_version,
            require=True,
        )
    except Exception as exc:  # noqa: BLE001
        fail(f"connect() failed: {exc}")
        if "mismatch" in str(exc).lower():
            warn("Host/firmware version differ. Re-flash the matching UF2, or pass --ignore-version.")
        return None
    present = [n for n in ("emfi", "crowbar", "scanner") if getattr(cat, n) is not None]
    ok(f"Connected. Engines up: {', '.join(present) or 'none'}")
    return cat


def stage_status(cat) -> None:
    hr("Stage 2 · read engine status  (read-only)")
    for name in ("emfi", "crowbar"):
        eng = getattr(cat, name, None)
        if eng is None:
            warn(f"{name}: not present")
            continue
        try:
            st = eng.status
            rows = ", ".join(f"{k}={v}" for k, v in st.as_rows())
            ok(f"{name}: {rows}")
        except Exception as exc:  # noqa: BLE001
            fail(f"{name} status: {exc}")


def stage_scan(cat) -> None:
    hr("Stage 3 · scanner  (touches target pins, no HV)")
    sc = getattr(cat, "scanner", None)
    if sc is None:
        warn("scanner not present; skipping")
        return
    try:
        res = sc.swd()
        if res.matched:
            ok(f"SWD found: SWCLK=GP{res.swclk_gp} SWDIO=GP{res.swdio_gp}")
        else:
            warn("SWD: no match (no target wired, or different pins)")
    except NotImplementedError as exc:
        warn(f"SWD: {exc}")
    except Exception as exc:  # noqa: BLE001
        fail(f"SWD scan: {exc}")
    try:
        res = sc.i2c()
        ok(f"I2C: {res.addresses_hex or 'no devices'}") if res.matched else warn("I2C: no match")
    except NotImplementedError as exc:
        warn(f"I2C: {exc}")
    except Exception as exc:  # noqa: BLE001
        fail(f"I2C scan: {exc}")


def stage_fire(cat, args) -> None:
    hr("Stage 4 · EMFI single shot  \033[1;31m⚡ HIGH VOLTAGE\033[0m")
    print("  This ARMS and DISCHARGES the EMFI HV capacitor.")
    print("  Confirm ALL of the following before continuing:")
    print("    - the plastic shield is installed")
    print("    - you know what (if anything) is under the coil")
    print("    - nobody is touching the exposed circuitry")
    if not args.yes:
        try:
            resp = input("  Type 'FIRE' to proceed (anything else aborts): ").strip()
        except EOFError:
            resp = ""
        if resp != "FIRE":
            warn("Aborted — no pulse fired.")
            return
    emfi = getattr(cat, "emfi", None)
    if emfi is None:
        fail("emfi engine not present")
        return
    try:
        emfi.trigger = "immediate"
        emfi.delay_us = args.delay_us
        emfi.width_us = args.width_us
        ok(f"configured: trigger=immediate delay={args.delay_us}us width={args.width_us}us")
        emfi.glitch(trigger_timeout_ms=5000)
        ok(f"fired — state now {emfi.status.state.name}")
        try:
            trace = emfi.capture(length=128)
            ok(f"captured {len(trace)} ADC samples (peak {max(trace)})")
        except Exception as exc:  # noqa: BLE001
            warn(f"capture skipped: {exc}")
    except Exception as exc:  # noqa: BLE001
        fail(f"fire failed: {exc}")
    finally:
        try:
            emfi.disarm()
            ok("disarmed")
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Staged FaultyCat hardware smoke test.")
    ap.add_argument("--scan", action="store_true", help="also run SWD/I2C scan (stage 3)")
    ap.add_argument("--fire", action="store_true", help="also fire ONE EMFI shot (stage 4, HIGH VOLTAGE)")
    ap.add_argument("--yes", action="store_true", help="skip the interactive fire confirmation (use with care)")
    ap.add_argument("--no-scanner", action="store_true", help="don't open the scanner CDC")
    ap.add_argument("--ignore-version", action="store_true", help="bypass host/firmware version check")
    ap.add_argument("--delay-us", type=int, default=0, help="EMFI delay for stage 4 (default 0)")
    ap.add_argument("--width-us", type=int, default=5, help="EMFI pulse width for stage 4 (default 5)")
    args = ap.parse_args()

    if not stage_detect():
        return 1
    cat = stage_connect(args)
    if cat is None:
        return 1
    try:
        stage_status(cat)
        if args.scan:
            stage_scan(cat)
        if args.fire:
            stage_fire(cat, args)
        hr("Done")
        ok("Smoke test complete.")
        if not args.fire:
            print("  (No high voltage was fired. Add --fire when you're ready and shielded.)")
    finally:
        cat.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
