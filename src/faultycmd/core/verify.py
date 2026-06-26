#! /usr/bin/env python3

# Electronic Cats
# This code is beerware; if you see me (or any other Electronic Cats
# member) at the local, and you've found our code helpful,
# please buy us a round!
# Distributed as-is; no warranty is given.

"""Communication smoke test for the FaultyCat composite.

Mirrors CatSniffer's ``catnip verify`` shape: discover every CDC
interface the board enumerates, run a small functional check against
each role (EMFI ping+status, crowbar ping+status, scanner shell
``help``, raw open of the target-UART data CDC), and print a
PASS/FAIL summary. Used as a quick post-flash / post-wiring check —
not a substitute for the full protocol test suite.
"""

from __future__ import annotations

from rich import box
from rich.table import Table

from ..protocols import CrowbarClient, EmfiClient, ScannerClient
from ..utils.output import (
    console,
    print_error,
    print_test_fail,
    print_test_header,
    print_test_pass,
    print_test_step,
    print_test_summary,
    print_warning,
    set_quiet_mode,
)
from .usb import FaultyCatPort, discover

ROLE_BY_INTERFACE: dict[int, str] = {
    0x00: "emfi",
    0x02: "crowbar",
    0x04: "scanner",
    0x06: "target",
}


def _ports_by_role(ports: list[FaultyCatPort]) -> dict[str, str]:
    return {
        ROLE_BY_INTERFACE[p.interface]: p.device
        for p in ports
        if p.interface in ROLE_BY_INTERFACE
    }


def test_emfi(port: str) -> bool:
    """Ping and read status from the EMFI module (CDC0)."""
    print_test_header("Testing EMFI module")
    results: list[bool] = []

    print_test_step("ping", "Verifying communication")
    try:
        with EmfiClient(port) as cli:
            reply = cli.ping()
        print_test_pass(repr(reply))
        results.append(True)
    except Exception as e:
        print_test_fail(str(e))
        results.append(False)

    print_test_step("status", "Reading module status")
    try:
        with EmfiClient(port) as cli:
            st = cli.status()
        print_test_pass(f"state={getattr(st.state, 'name', st.state)}")
        results.append(True)
    except Exception as e:
        print_test_fail(str(e))
        results.append(False)

    passed = sum(results)
    print_test_summary(passed, len(results), "EMFI")
    return passed == len(results)


def test_crowbar(port: str) -> bool:
    """Ping and read status from the crowbar module (CDC1)."""
    print_test_header("Testing crowbar module")
    results: list[bool] = []

    print_test_step("ping", "Verifying communication")
    try:
        with CrowbarClient(port) as cli:
            reply = cli.ping()
        print_test_pass(repr(reply))
        results.append(True)
    except Exception as e:
        print_test_fail(str(e))
        results.append(False)

    print_test_step("status", "Reading module status")
    try:
        with CrowbarClient(port) as cli:
            st = cli.status()
        print_test_pass(f"state={getattr(st.state, 'name', st.state)}")
        results.append(True)
    except Exception as e:
        print_test_fail(str(e))
        results.append(False)

    passed = sum(results)
    print_test_summary(passed, len(results), "crowbar")
    return passed == len(results)


def test_scanner_shell(port: str) -> bool:
    """Verify the CDC2 text shell answers a basic command (CDC2).

    ``help`` replies with one ``SHELL:`` line per command (~15+
    lines), not a single line — using ``send_line`` here would grab
    only the first line and leave the rest sitting unread, which then
    bleeds into the *next* client's version-check probe on reopen and
    causes an intermittent, spurious ``VersionMismatchError``. Drain
    the whole reply with ``send_line_collect`` instead.
    """
    print_test_header("Testing scanner shell")
    results: list[bool] = []

    print_test_step("help", "Verifying shell responds")
    try:
        with ScannerClient(port) as cli:
            lines = cli.send_line_collect(
                "help", accept_prefixes=("SHELL:",), quiet_ms=300, timeout=3.0
            )
        print_test_pass(f"{len(lines)} lines, first: {lines[0] if lines else ''}")
        results.append(True)
    except Exception as e:
        print_test_fail(str(e))
        results.append(False)

    passed = sum(results)
    print_test_summary(passed, len(results), "scanner")
    return passed == len(results)


def test_target_uart(port: str) -> bool:
    """Verify the target-UART data CDC (CDC3) opens cleanly.

    This CDC carries raw passthrough bytes only — there's no control
    protocol to probe here (that lives on the scanner shell), so the
    check is just that the port exists and can be opened/closed.
    """
    print_test_header("Testing target UART data CDC")

    print_test_step("open", f"Opening {port}")
    try:
        import serial

        ser = serial.Serial(port, 115200, timeout=0.2)
        ser.close()
        print_test_pass(f"opened {port}")
        ok = True
    except Exception as e:
        print_test_fail(str(e))
        ok = False

    print_test_summary(1 if ok else 0, 1, "target UART")
    return ok


def run_verification(quiet: bool = False) -> tuple[bool, dict[str, bool]]:
    """Run the full smoke test against the connected FaultyCat board.

    Returns:
        Tuple of (overall_success, {role: passed}).
    """
    set_quiet_mode(quiet)

    if not quiet:
        console.print("[cyan]Starting FaultyCat verification...[/cyan]")

    ports = discover()
    if not ports:
        if not quiet:
            print_error("No FaultyCat CDC found!")
        return False, {}

    by_role = _ports_by_role(ports)

    if not quiet:
        table = Table(
            title=f"Found {len(ports)} FaultyCat interface(s)", box=box.ROUNDED
        )
        table.add_column("role", style="green")
        table.add_column("device", style="white")
        for role, device in by_role.items():
            table.add_row(role, device)
        console.print(table)

    checks = (
        ("emfi", test_emfi),
        ("crowbar", test_crowbar),
        ("scanner", test_scanner_shell),
        ("target", test_target_uart),
    )

    results: dict[str, bool] = {}
    for role, test_fn in checks:
        port = by_role.get(role)
        if port is None:
            if not quiet:
                print_warning(f"{role} CDC not found — skipping")
            results[role] = False
            continue
        results[role] = test_fn(port)

    all_passed = all(results.values())

    if not quiet:
        print_test_header("Verification Summary")
        summary = Table(box=box.SIMPLE)
        summary.add_column("Interface", style="cyan")
        summary.add_column("Result", justify="center")
        for role, ok in results.items():
            color = "green" if ok else "red"
            summary.add_row(role, f"[{color}]{'PASS' if ok else 'FAIL'}[/{color}]")
        console.print(summary)
    else:
        for role, ok in results.items():
            print(f"{role}: {'PASS' if ok else 'FAIL'}")

    return all_passed, results
