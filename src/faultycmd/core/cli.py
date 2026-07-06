#! /usr/bin/env python3

# Electronic Cats
# Original Creation Date: June 18, 2026
# This code is beerware; if you see me (or any other Electronic Cats
# member) at the local, and you've found our code helpful,
# please buy us a round!
# Distributed as-is; no warranty is given.

"""click-based CLI for faultycmd, with Rich-rendered output.

Top-level command groups mirror the firmware's CDC layout:

    faultycmd emfi      (F4 emfi_proto on CDC0)
    faultycmd crowbar   (F5 crowbar_proto on CDC1)
    faultycmd campaign  (F9-4 campaign_proto on CDC0/CDC1, --engine)
    faultycmd scanner   (F8-2 ``scan swd`` over CDC2 text shell)
    faultycmd i2c       (I2C scan/probe over the same CDC2 text shell)
    faultycmd uart      (Target UART passthrough: CDC2 control + CDC3 data)
    faultycmd la        (Protocol-agnostic logic analyzer over CDC2)
    faultycmd tui       (F10-5 Textual dashboard)
    faultycmd devices   (USB enumeration helper)

Each subcommand wraps the corresponding ``faultycmd.protocols.*``
client. Plain status lines go through ``faultycmd.utils.output``
(``print_success``/``print_error``/...); Rich :class:`Table` and
:class:`Live` handle tables, progress bars, and live displays.

JTAG and direct-SWD verbs (init/deinit/freq/idcode/connect/read32/
write32/reset + scan-jtag) are WIP and intentionally hidden from
this release; the underlying ``ScannerClient`` keeps them as
underscored methods so the v3.1 unblock can re-expose them
without re-writing the protocol layer.
"""

from __future__ import annotations

import os
import platform
import random as _random
import shutil
import subprocess
import sys
from pathlib import Path

# External dependencies
import click
from rich import box
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

# Internal imports
from .. import __version__
from ..protocols import (
    CampaignClient,
    CampaignError,
    CrowbarClient,
    EmfiClient,
    EngineError,
    ScannerClient,
    ScannerError,
    parse_scan_i2c_match,
)
from ..protocols.crowbar import CrowbarOutput, CrowbarTrigger
from ..protocols.emfi import EmfiTrigger
from ..protocols.i2c_decode import decode_i2c
from ..protocols.uart_decode import decode_uart
from ..protocols.la_decode import samples_to_vcd
from .usb import PortDiscoveryError, discover
from ..utils.version_check import VersionMismatchError, set_allow_mismatch
from ..utils.output import (
    console,
    STYLES,
    print_success,
    print_warning,
    print_error,
    print_info,
    print_dim,
)

COMPANY = "Electronic Cats - PWNLAB"
_FUNNY_PHRASES = [
    "Glitching bits since breakfast.",
    "Your silicon's worst Tuesday.",
    "Voltage is a suggestion, not a rule.",
    "Faults happen. We make them happen on purpose.",
    "EMFI: electromagnetic mischief, fully intended.",
    "Crowbar in hand, secure boot in shambles.",
    "Who needs a debugger when you have a glitch?",
    "Making bootloaders reconsider their life choices.",
    "One pulse closer to a root shell.",
    "Reliability engineering's natural predator.",
]

FUNNY_PHRASE = _random.choice(_FUNNY_PHRASES)


def print_header(module: str | None = None) -> None:
    """Print the ASCII art header."""
    label = f"faultycmd {module}" if module else "faultycmd"

    ascii_art = f"""      :=--             --=-       |
      -====-         -=====       |
      :===================-       |
       ===================:       |
  -   :==--===========--==-   -   |  {label}
 -===:===-   :=====-   -==-.-=--  |  v{__version__}
--    ====-   :===-   -====    -- |  {FUNNY_PHRASE}
-=:   :===================-   .=- |
 ---=-- -===============-  -=---  |
 ---       --=======--        --  |"""

    colored_ascii = f"[cyan bold]{ascii_art}[/cyan bold]"

    header_panel = Panel(
        colored_ascii,
        title=f"[cyan]{COMPANY}[/cyan]",
        border_style=STYLES["header"],
        title_align="left",
        padding=(1, 2),
    )
    console.print(header_panel)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _parse_axis(spec: str) -> tuple[int, int, int]:
    """Parse 'START:END:STEP' or just 'X' (collapses axis)."""
    if ":" not in spec:
        v = int(spec, 0)
        return v, v, 0
    parts = spec.split(":")
    if len(parts) != 3:
        raise click.BadParameter(f"axis must be START:END:STEP, got {spec!r}")
    return int(parts[0], 0), int(parts[1], 0), int(parts[2], 0)


def _engine_to_client(engine: str, port: str | None) -> CampaignClient:
    if port is not None:
        return CampaignClient(port, engine=engine)  # type: ignore[arg-type]
    return CampaignClient.discover(engine)  # type: ignore[arg-type]


def _print_status_table(title: str, rows: list[tuple[str, str]]) -> None:
    table = Table(title=title, show_header=False, box=None, pad_edge=False)
    table.add_column("field", style="cyan", no_wrap=True)
    table.add_column("value", style="white")
    for k, v in rows:
        table.add_row(k, v)
    console.print(table)


def _resolve_i2c_pins(
    cli: ScannerClient, sda: int | None, scl: int | None, *, timeout_s: float
) -> tuple[int, int]:
    """Return ``(sda, scl)``, auto-discovering via a full ``i2c scan``
    sweep when either pin is omitted.

    The I2C bit-bang core can use any of the 8 scanner-header channels
    as SDA/SCL (see docs/I2C_SCANNER_INTERNALS.md) — there's no fixed
    pin pair — so when the operator doesn't already know the wiring,
    running the same P(8,2) sweep `i2c scan` does is the only way to
    find it.
    """
    if sda is not None and scl is not None:
        return sda, scl
    if (sda is None) != (scl is None):
        raise click.UsageError("Pass both SDA and SCL, or neither to auto-discover.")
    print_info("No SDA/SCL given — running `i2c scan` to auto-discover pins...")
    lines = cli.scan_i2c(timeout_s=timeout_s, on_progress=console.print)
    match = parse_scan_i2c_match(lines)
    if match is None:
        raise click.ClickException(
            "i2c scan found no device — wire the target or pass --sda/--scl explicitly."
        )
    found_sda, found_scl, _addrs = match
    print_success(f"Auto-discovered sda=GP{found_sda} scl=GP{found_scl}")
    return found_sda, found_scl


# -----------------------------------------------------------------------------
# Top-level group
# -----------------------------------------------------------------------------


@click.group()
@click.option(
    "--ignore-version-mismatch",
    is_flag=True,
    default=False,
    help="Bypass the firmware/host version parity check on every connect. "
    "Unsafe — only use for development against a hand-built UF2.",
)
def main(ignore_version_mismatch: bool) -> None:
    """faultycmd: All in one FaultyCat tools environment."""
    if ignore_version_mismatch:
        set_allow_mismatch(True)
        print_warning("Firmware/host version parity check bypassed.")


# -----------------------------------------------------------------------------
# `devices`
# -----------------------------------------------------------------------------


@main.command()
def devices() -> None:
    """List FaultyCat interfaces detected on this machine."""
    ports = discover()
    if not ports:
        print_warning("No FaultyCat CDC found.")
        raise SystemExit(1)
    table = Table(title=f"Found {len(ports)} FaultyCat interface(s)", box=box.ROUNDED)
    table.add_column("IF", style=STYLES["device"], justify="right")
    table.add_column("role", style="green")
    table.add_column("device", style="white")
    role_by_iface = {
        0x00: "emfi",
        0x02: "crowbar",
        0x04: "scanner",
        0x06: "target-uart",
    }
    for p in ports:
        table.add_row(
            f"0x{p.interface:02X}",
            role_by_iface.get(p.interface, "?"),
            p.device,
        )
    console.print(table)

    # Best-effort firmware version probe via the emfi CDC PING. Use the
    # ignore-mismatch path so a mismatch is reported here without
    # aborting — the operator is running `info` precisely to diagnose
    # that case.
    fw_str: str
    fw_match: bool | None
    try:
        emfi_port = next((p.device for p in ports if p.interface == 0x00), None)
        if emfi_port is None:
            fw_str, fw_match = "(no emfi CDC)", None
        else:
            probe = EmfiClient(emfi_port, check_firmware_version=False)
            with probe as cli:
                payload = cli.ping()
            from ..utils.version_check import EXPECTED_BOARD, parse_ping_version

            fw_tuple = parse_ping_version(payload)
            fw_str = ".".join(str(v) for v in fw_tuple)
            fw_match = fw_tuple[0] == EXPECTED_BOARD
    except (VersionMismatchError, OSError, RuntimeError) as e:
        fw_str, fw_match = f"unreachable ({e})", None

    if fw_match is True:
        print_success(f"Firmware: v{fw_str}  (board match)")
    elif fw_match is False:
        print_error(f"Firmware: v{fw_str}  (wrong board, re-flash to recover)")
    else:
        print_warning(f"Firmware: {fw_str}")


# -----------------------------------------------------------------------------
# `verify`
# -----------------------------------------------------------------------------


@main.command()
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Show only the PASS/FAIL summary.",
)
def verify(quiet: bool) -> None:
    """Run a communication smoke test against every detected FaultyCat CDC.

    Checks EMFI (ping + status), crowbar (ping + status), the scanner
    text shell (``help``), and that the target-UART data CDC opens
    cleanly. Useful after flashing new firmware or wiring up a board
    for the first time.
    """
    from .verify import run_verification

    success, _results = run_verification(quiet=quiet)

    if success:
        print_success("Verification completed successfully!")
    else:
        print_error("Verification failed!")
        print_warning("Troubleshooting tips:")
        print_dim("1. Make sure the FaultyCat is connected via USB")
        print_dim("2. Try reconnecting the USB cable")
        if platform.system() == "Linux":
            print_dim("3. Check udev rules / dialout group: faultycmd setup-env")
        raise SystemExit(1)


# -----------------------------------------------------------------------------
# `update`
# -----------------------------------------------------------------------------


@main.command()
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Re-flash even if the connected firmware already matches this host version.",
)
def update(force: bool) -> None:
    """Download and flash the firmware build matching this host version.

    Host and firmware versions are released together — this fetches
    the .uf2 asset from the GitHub Release tagged to this host's
    version and flashes it over the RP2040's UF2 bootloader. If the
    board isn't already in boot mode, you'll be prompted to put it
    there (it enumerates as a ``RPI-RP2`` USB drive); there's no
    remote reboot verb to do that automatically.
    """
    from .fw_update import check_and_update_firmware

    if not check_and_update_firmware(force=force):
        raise SystemExit(1)


# -----------------------------------------------------------------------------
# `emfi`
# -----------------------------------------------------------------------------


@main.group()
@click.option("--port", default=None, help="Override the EMFI port.")
@click.pass_context
def emfi(ctx: click.Context, port: str | None) -> None:
    """Control the EMFI module (electromagnetic fault injection)."""
    ctx.obj = port


def _emfi_client(ctx: click.Context) -> EmfiClient:
    port = ctx.obj
    return EmfiClient(port) if port is not None else EmfiClient.discover()


@emfi.command()
@click.pass_context
def ping(ctx: click.Context) -> None:
    """Verify communication with the EMFI module."""
    with _emfi_client(ctx) as cli:
        rpl = cli.ping()
    print_success(f"PONG {rpl!r}")


@emfi.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show the current state of the EMFI module."""
    with _emfi_client(ctx) as cli:
        st = cli.status()
    _print_status_table(
        "EMFI status",
        [
            ("state", getattr(st.state, "name", str(st.state))),
            ("err", getattr(st.err, "name", str(st.err))),
            ("last_fire_at_ms", str(st.last_fire_at_ms)),
            ("capture_fill", str(st.capture_fill)),
            ("pulse_width_us_actual", str(st.pulse_width_us_actual)),
            ("delay_us_actual", str(st.delay_us_actual)),
        ],
    )


@emfi.command()
@click.option(
    "--trigger",
    type=click.Choice([t.name.lower() for t in EmfiTrigger]),
    default="immediate",
    show_default=True,
)
@click.option("--delay-us", type=int, default=0, show_default=True)
@click.option(
    "--width-us",
    type=int,
    default=5,
    show_default=True,
    help="Pulse width in µs (1..50).",
)
@click.option(
    "--charge-timeout-ms",
    type=int,
    default=0,
    show_default=True,
    help="Max armed time in ms (0 = 60 s default).",
)
@click.pass_context
def configure(
    ctx: click.Context,
    trigger: str,
    delay_us: int,
    width_us: int,
    charge_timeout_ms: int,
) -> None:
    """Configure the parameters for the next EMFI fire."""
    trig = EmfiTrigger[trigger.upper()]
    with _emfi_client(ctx) as cli:
        cli.configure(trig, delay_us, width_us, charge_timeout_ms)
    print_success(
        f"Configured trigger={trig.name} delay={delay_us}us width={width_us}us"
    )


@emfi.command()
@click.pass_context
def arm(ctx: click.Context) -> None:
    """Arm the module (charge the high-voltage capacitor)."""
    with _emfi_client(ctx) as cli:
        cli.arm()
    print_success("Armed")


@emfi.command()
@click.option(
    "--trigger-timeout-ms",
    type=int,
    default=60000,
    show_default=True,
    help="Max time to wait for the trigger in ms.",
)
@click.pass_context
def fire(ctx: click.Context, trigger_timeout_ms: int) -> None:
    """Wait for the trigger and fire the EMFI pulse."""
    with _emfi_client(ctx) as cli:
        cli.fire(trigger_timeout_ms)
    print_success("Fire dispatched")


@emfi.command()
@click.pass_context
def disarm(ctx: click.Context) -> None:
    """Disarm the EMFI module."""
    with _emfi_client(ctx) as cli:
        cli.disarm()
    print_success("Disarmed")


@emfi.command()
@click.option(
    "--offset",
    type=int,
    default=0,
    show_default=True,
    help="Start position within the buffer.",
)
@click.option(
    "--length",
    type=int,
    default=512,
    show_default=True,
    help="Bytes to read (max 512 per request).",
)
@click.option(
    "--out",
    "out_file",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Output file (if omitted, prints as hex).",
)
@click.pass_context
def capture(
    ctx: click.Context,
    offset: int,
    length: int,
    out_file: str | None,
) -> None:
    """Read the analog capture from the last fire."""
    with _emfi_client(ctx) as cli:
        data = cli.capture(offset=offset, length=length)
    if out_file:
        with open(out_file, "wb") as fh:
            fh.write(data)
        print_success(f"Wrote {len(data)} bytes → {out_file}")
    else:
        print_info(f"{len(data)} bytes: {data.hex()}")


# -----------------------------------------------------------------------------
# `crowbar`
# -----------------------------------------------------------------------------


@main.group()
@click.option("--port", default=None, help="Override the crowbar port.")
@click.pass_context
def crowbar(ctx: click.Context, port: str | None) -> None:
    """Control the crowbar (voltage glitch on the target)."""
    ctx.obj = port


def _crowbar_client(ctx: click.Context) -> CrowbarClient:
    port = ctx.obj
    return CrowbarClient(port) if port is not None else CrowbarClient.discover()


@crowbar.command("ping")
@click.pass_context
def crowbar_ping(ctx: click.Context) -> None:
    """Verify communication with the crowbar."""
    with _crowbar_client(ctx) as cli:
        rpl = cli.ping()
    print_success(f"PONG {rpl!r}")


@crowbar.command("status")
@click.pass_context
def crowbar_status(ctx: click.Context) -> None:
    """Show the current state of the crowbar."""
    with _crowbar_client(ctx) as cli:
        st = cli.status()
    _print_status_table(
        "Crowbar status",
        [
            ("state", getattr(st.state, "name", str(st.state))),
            ("err", getattr(st.err, "name", str(st.err))),
            ("last_fire_at_ms", str(st.last_fire_at_ms)),
            ("pulse_width_ns_actual", str(st.pulse_width_ns_actual)),
            ("delay_us_actual", str(st.delay_us_actual)),
            ("output", getattr(st.output, "name", str(st.output))),
        ],
    )


@crowbar.command("configure")
@click.option(
    "--trigger",
    type=click.Choice([t.name.lower() for t in CrowbarTrigger]),
    default="immediate",
    show_default=True,
)
@click.option(
    "--output",
    "output_str",
    type=click.Choice(["lp", "hp"]),
    default="hp",
    show_default=True,
    help="lp = low power, hp = high power (real glitch).",
)
@click.option(
    "--delay-us",
    type=int,
    default=0,
    show_default=True,
    help="Delay after the trigger in µs.",
)
@click.option(
    "--width-ns",
    type=int,
    default=200,
    show_default=True,
    help="Pulse width in ns (8..50000).",
)
@click.pass_context
def crowbar_configure(
    ctx: click.Context,
    trigger: str,
    output_str: str,
    delay_us: int,
    width_ns: int,
) -> None:
    """Configure the parameters for the next glitch."""
    trig = CrowbarTrigger[trigger.upper()]
    out = CrowbarOutput[output_str.upper()]
    with _crowbar_client(ctx) as cli:
        cli.configure(trig, out, delay_us, width_ns)
    print_success(
        f"Configured trigger={trig.name} output={out.name} "
        f"Delay={delay_us}us width={width_ns}ns"
    )


@crowbar.command("arm")
@click.pass_context
def crowbar_arm(ctx: click.Context) -> None:
    """Arm the crowbar."""
    with _crowbar_client(ctx) as cli:
        cli.arm()
    print_success("Armed")


@crowbar.command("fire")
@click.option(
    "--trigger-timeout-ms",
    type=int,
    default=60000,
    show_default=True,
    help="Max time to wait for the trigger in ms.",
)
@click.pass_context
def crowbar_fire(ctx: click.Context, trigger_timeout_ms: int) -> None:
    """Wait for the trigger and fire the glitch."""
    with _crowbar_client(ctx) as cli:
        cli.fire(trigger_timeout_ms)
    print_success("Fire dispatched")


@crowbar.command("disarm")
@click.pass_context
def crowbar_disarm(ctx: click.Context) -> None:
    """Disarm the crowbar."""
    with _crowbar_client(ctx) as cli:
        cli.disarm()
    print_success("Disarmed")


# -----------------------------------------------------------------------------
# `campaign`
# -----------------------------------------------------------------------------


@main.group()
@click.option(
    "--engine",
    type=click.Choice(["emfi", "crowbar"]),
    default="crowbar",
    show_default=True,
    help="Engine to run the sweep on.",
)
@click.option("--port", default=None, help="Override the port.")
@click.pass_context
def campaign(ctx: click.Context, engine: str, port: str | None) -> None:
    """Run automated parameter sweeps."""
    ctx.obj = (engine, port)


def _campaign_client(ctx: click.Context) -> CampaignClient:
    engine, port = ctx.obj
    return _engine_to_client(engine, port)


@campaign.command("status")
@click.pass_context
def campaign_status(ctx: click.Context) -> None:
    """Show the state of the running sweep."""
    with _campaign_client(ctx) as cli:
        st = cli.status()
    _print_status_table(
        f"Campaign status ({ctx.obj[0]})",
        [
            ("state", getattr(st.state, "name", str(st.state))),
            ("err", getattr(st.err, "name", str(st.err))),
            ("step_n", f"{st.step_n}/{st.total_steps}"),
            ("results_pushed", str(st.results_pushed)),
            ("results_dropped", str(st.results_dropped)),
        ],
    )


@campaign.command("configure")
@click.option(
    "--delay",
    required=True,
    help="Delay range in µs (START:END:STEP, or a fixed value).",
)
@click.option(
    "--width", required=True, help="Pulse width range (µs on EMFI, ns on crowbar)."
)
@click.option(
    "--power",
    required=True,
    help="Power range. On crowbar: 1=low, 2=high. Ignored on EMFI.",
)
@click.option(
    "--settle-ms",
    type=int,
    default=0,
    show_default=True,
    help="Settle time between shots in ms.",
)
@click.pass_context
def campaign_configure(
    ctx: click.Context,
    delay: str,
    width: str,
    power: str,
    settle_ms: int,
) -> None:
    """Define the sweep ranges."""
    with _campaign_client(ctx) as cli:
        cli.configure(
            _parse_axis(delay),
            _parse_axis(width),
            _parse_axis(power),
            settle_ms=settle_ms,
        )
    print_success("Configured")


@campaign.command("start")
@click.pass_context
def campaign_start(ctx: click.Context) -> None:
    """Start the sweep."""
    with _campaign_client(ctx) as cli:
        cli.start()
    print_success("Started")


@campaign.command("stop")
@click.pass_context
def campaign_stop(ctx: click.Context) -> None:
    """Stop the running sweep."""
    with _campaign_client(ctx) as cli:
        cli.stop()
    print_success("Stopped")


@campaign.command("drain")
@click.option(
    "--max",
    "max_count",
    type=int,
    default=18,
    show_default=True,
    help="Records per request (max 18).",
)
@click.pass_context
def campaign_drain(ctx: click.Context, max_count: int) -> None:
    """Download the accumulated sweep results."""
    rows: list[tuple] = []
    with _campaign_client(ctx) as cli:
        for r in cli.drain_all():
            rows.append(
                (
                    r.step_n,
                    r.delay,
                    r.width,
                    r.power,
                    f"0x{r.fire_status:02X}",
                    f"0x{r.verify_status:02X}",
                    f"0x{r.target_state:08X}",
                    r.ts_us,
                )
            )
    if not rows:
        print_warning("Ring empty")
        return
    table = Table(title=f"Campaign results ({len(rows)})")
    for col, just in [
        ("step", "right"),
        ("delay", "right"),
        ("width", "right"),
        ("power", "right"),
        ("fire", "right"),
        ("verify", "right"),
        ("target", "right"),
        ("ts_us", "right"),
    ]:
        table.add_column(col, justify=just)
    for row in rows:
        table.add_row(*[str(c) for c in row])
    console.print(table)


@campaign.command("watch")
@click.option(
    "--every-ms",
    type=int,
    default=200,
    show_default=True,
    help="Update interval in ms.",
)
@click.pass_context
def campaign_watch(ctx: click.Context, every_ms: int) -> None:
    """Follow the sweep live until it completes."""
    engine = ctx.obj[0]
    seen_results: list = []

    def _render() -> Table:
        t = Table(
            title=f"Campaign live ({engine}) — {len(seen_results)} results so far"
        )
        for col in ("step", "delay", "width", "power", "fire", "verify", "target"):
            t.add_column(col, justify="right")
        for r in seen_results[-15:]:
            t.add_row(
                str(r.step_n),
                str(r.delay),
                str(r.width),
                str(r.power),
                f"0x{r.fire_status:02X}",
                f"0x{r.verify_status:02X}",
                f"0x{r.target_state:08X}",
            )
        return t

    last_status = None
    with (
        _campaign_client(ctx) as cli,
        Live(_render(), refresh_per_second=8, console=console) as live,
    ):
        for st, batch in cli.watch(every_ms=every_ms):
            last_status = st
            seen_results.extend(batch)
            live.update(_render())
    if last_status is not None:
        st = last_status
        print_success(
            f"done state={getattr(st.state, 'name', st.state)} "
            f"step={st.step_n}/{st.total_steps} pushed={st.results_pushed} dropped={st.results_dropped}"
        )


# -----------------------------------------------------------------------------
# `scanner`
# -----------------------------------------------------------------------------


@main.group()
@click.option("--port", default=None, help="Override the scanner port.")
@click.pass_context
def scanner(ctx: click.Context, port: str | None) -> None:
    """Scan the target's SWD pinout."""
    ctx.obj = port


def _get_scanner_client(ctx: click.Context) -> ScannerClient:
    port = ctx.obj
    return ScannerClient(port) if port is not None else ScannerClient.discover()


@scanner.command("scan-swd")
@click.option(
    "--targetsel",
    default=None,
    help="Optional hex value (discovery sweeps the whole bus by default).",
)
@click.option(
    "--timeout-s",
    type=float,
    default=30.0,
    show_default=True,
    help="Max scan time in seconds.",
)
@click.pass_context
def scanner_scan_swd(
    ctx: click.Context,
    targetsel: str | None,
    timeout_s: float,
) -> None:
    """Detect the target's SWD pins."""
    with _get_scanner_client(ctx) as cli:
        cli.scan_swd(
            targetsel_hex=targetsel,
            timeout_s=timeout_s,
            on_progress=console.print,
        )


# -----------------------------------------------------------------------------
# `i2c`
# -----------------------------------------------------------------------------


@main.group()
@click.option("--port", default=None, help="Override the scanner port.")
@click.pass_context
def i2c(ctx: click.Context, port: str | None) -> None:
    """Scan and probe the target's I2C bus (rides the scanner CDC)."""
    ctx.obj = port


@i2c.command("scan")
@click.option(
    "--timeout-s",
    type=float,
    default=30.0,
    show_default=True,
    help="Max scan time in seconds.",
)
@click.pass_context
def i2c_scan(ctx: click.Context, timeout_s: float) -> None:
    """Detect the target's I2C SDA/SCL pins and ACKed addresses."""
    with _get_scanner_client(ctx) as cli:
        cli.scan_i2c(timeout_s=timeout_s, on_progress=console.print)


@i2c.command("probe")
@click.argument("sda", type=int, required=False, default=None)
@click.argument("scl", type=int, required=False, default=None)
@click.option(
    "--timeout-s",
    type=float,
    default=5.0,
    show_default=True,
    help="Max probe time in seconds.",
)
@click.option(
    "--scan-timeout-s",
    type=float,
    default=30.0,
    show_default=True,
    help="Max time for the auto-discovery `i2c scan` (only used if SDA/SCL are omitted).",
)
@click.pass_context
def i2c_probe(
    ctx: click.Context,
    sda: int | None,
    scl: int | None,
    timeout_s: float,
    scan_timeout_s: float,
) -> None:
    """Rescan I2C addresses on known SDA/SCL pins (skip the full sweep).

    SDA/SCL can be omitted — I2C has no fixed pin pair (any of the 8
    scanner-header channels can carry SDA/SCL), so when they're
    omitted this runs a full `i2c scan` first to find them.
    """
    with _get_scanner_client(ctx) as cli:
        sda, scl = _resolve_i2c_pins(cli, sda, scl, timeout_s=scan_timeout_s)
        for line in cli.i2c_probe(sda, scl, timeout_s=timeout_s):
            console.print(line)


# -----------------------------------------------------------------------------
# `uart` (Target UART passthrough)
#
# Control verbs (enter/baud/parity/stopbits/status/exit) ride the CDC2
# text shell via ScannerClient — see uart_passthrough in main.c. The
# actual byte traffic flows on a separate CDC (the "target" role in
# core.usb); `uart console` bridges that data CDC to this terminal.
# -----------------------------------------------------------------------------


@main.group()
@click.option("--port", default=None, help="Override the scanner control port (CDC2).")
@click.pass_context
def uart(ctx: click.Context, port: str | None) -> None:
    """Control and bridge the Target UART passthrough."""
    ctx.obj = port


@uart.command("enter")
@click.option("--baud", type=int, default=115200, show_default=True)
@click.option(
    "--parity", type=click.Choice(["n", "e", "o"]), default="n", show_default=True
)
@click.option(
    "--stopbits",
    "stop_bits",
    type=click.Choice(["1", "2"]),
    default="1",
    show_default=True,
)
@click.pass_context
def uart_enter(ctx: click.Context, baud: int, parity: str, stop_bits: str) -> None:
    """Enable the bridge (CH0=TX/CH1=RX on the scanner header)."""
    with _get_scanner_client(ctx) as cli:
        reply = cli.uart_enter(baud=baud, parity=parity, stop_bits=int(stop_bits))
    print_success(reply)


@uart.command("exit")
@click.pass_context
def uart_exit(ctx: click.Context) -> None:
    """Disable the bridge."""
    with _get_scanner_client(ctx) as cli:
        reply = cli.uart_exit()
    print_success(reply)


@uart.command("status")
@click.pass_context
def uart_status(ctx: click.Context) -> None:
    """Show the current bridge configuration."""
    with _get_scanner_client(ctx) as cli:
        reply = cli.uart_status()
    print_info(reply)


@uart.command("baud")
@click.argument("value", type=int)
@click.pass_context
def uart_baud(ctx: click.Context, value: int) -> None:
    """Reconfigure the baud rate of a live bridge."""
    with _get_scanner_client(ctx) as cli:
        reply = cli.uart_set_baud(value)
    print_success(reply)


@uart.command("parity")
@click.argument("value", type=click.Choice(["n", "e", "o"]))
@click.pass_context
def uart_parity(ctx: click.Context, value: str) -> None:
    """Reconfigure the parity of a live bridge."""
    with _get_scanner_client(ctx) as cli:
        reply = cli.uart_set_parity(value)
    print_success(reply)


@uart.command("stopbits")
@click.argument("value", type=click.Choice(["1", "2"]))
@click.pass_context
def uart_stopbits(ctx: click.Context, value: str) -> None:
    """Reconfigure the stop bits of a live bridge."""
    with _get_scanner_client(ctx) as cli:
        reply = cli.uart_set_stopbits(int(value))
    print_success(reply)


@uart.command("console")
@click.option(
    "--target-port",
    default=None,
    help="Override the Target UART data port (auto-discovered by default).",
)
@click.option("--baud", type=int, default=115200, show_default=True)
@click.option(
    "--parity", type=click.Choice(["n", "e", "o"]), default="n", show_default=True
)
@click.option(
    "--stopbits",
    "stop_bits",
    type=click.Choice(["1", "2"]),
    default="1",
    show_default=True,
)
@click.pass_context
def uart_console(
    ctx: click.Context,
    target_port: str | None,
    baud: int,
    parity: str,
    stop_bits: str,
) -> None:
    """Enable the bridge and open a live raw byte console on it.

    Enables the bridge over the scanner control shell (skipped if a
    bridge is already running — the firmware reports `ERR busy` on a
    second `uart enter` since the pins are already owned by this same
    passthrough), then pumps bytes bidirectionally between this
    terminal and the Target UART data CDC. Press Ctrl-X to exit; the
    bridge is only disabled again on the way out if this command was
    the one that enabled it.
    """
    import contextlib
    import select
    import threading

    import serial

    from .usb import cdc_for

    @contextlib.contextmanager
    def _raw_terminal():
        if platform.system() == "Windows" or not sys.stdin.isatty():
            yield
            return
        import termios
        import tty

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setraw(fd)
        try:
            yield
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    control = _get_scanner_client(ctx)
    with control:
        # A second `uart enter` while one is already running gets
        # `ERR busy` from the firmware (the pins are already owned by
        # this same passthrough) — so reuse a running bridge instead
        # of re-entering, and only tear it down on exit if we're the
        # one who started it.
        already_enabled = control.uart_status().startswith("UART: enabled")
        if already_enabled:
            print_info(f"Bridge already running: {control.uart_status()}")
        else:
            print_success(
                control.uart_enter(baud=baud, parity=parity, stop_bits=int(stop_bits))
            )

        data_port = target_port or cdc_for("target")
        ser = serial.Serial(data_port, baud, timeout=0.1)
        print_info(f"Bridging {data_port} — Ctrl-X to exit")

        stop = threading.Event()
        disconnect_error: list[Exception] = []

        def _pump_serial_to_stdout() -> None:
            try:
                while not stop.is_set():
                    chunk = ser.read(64)
                    if chunk:
                        sys.stdout.buffer.write(chunk)
                        sys.stdout.buffer.flush()
            except serial.SerialException as exc:
                disconnect_error.append(exc)
                stop.set()

        reader = threading.Thread(target=_pump_serial_to_stdout, daemon=True)
        reader.start()
        try:
            with _raw_terminal():
                while not stop.is_set():
                    if select.select([sys.stdin], [], [], 0.1)[0]:
                        data = sys.stdin.buffer.read(1)
                        if not data or data == b"\x18":  # Ctrl-X
                            break
                        ser.write(data)
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            reader.join(timeout=1.0)
            ser.close()
            if not already_enabled:
                print_success(control.uart_exit())
            if disconnect_error:
                raise click.ClickException(
                    f"Target UART disconnected: {disconnect_error[0]}"
                )


# -----------------------------------------------------------------------------
# `la` (protocol-agnostic logic analyzer)
#
# Captures the full GP0..GP7 bank verbatim over the CDC2 text shell — the
# firmware never interprets the channels (see
# faultycat-firmware/docs/LOGIC_ANALYZER.md). A "protocol" is nothing more
# than a wiring convention plus the decoder you pick: on-device with
# `--decode i2c|uart`, or host-side in PulseView/sigrok via `la pulseview`.
# Any digital signal wired onto GP0..GP7 is fair game — not just UART/I2C.
# -----------------------------------------------------------------------------


@main.group()
@click.option("--port", default=None, help="Override the scanner control port (CDC2).")
@click.pass_context
def la(ctx: click.Context, port: str | None) -> None:
    """Protocol-agnostic logic analyzer (captures GP0..GP7)."""
    ctx.obj = port


@la.command("capture")
@click.option(
    "--interval-us",
    "interval_us",
    type=int,
    default=1,
    show_default=True,
    help="Sample interval in microseconds (firmware minimum/fastest: 1us — "
    "sample_interval_us=0 is treated as 1 by the firmware).",
)
@click.option(
    "--samples",
    "n",
    type=int,
    default=16384,
    show_default=True,
    help="Samples to capture. This shell command streams continuously from "
    "the firmware's ring buffer with no hard sample-count ceiling (unlike "
    "`la pulseview`'s bounded SUMP_OLS_MAX_SAMPLES=16384 capture) — going "
    "well past the default is fine as long as the host keeps draining USB "
    "fast enough to avoid an OVERFLOW.",
)
@click.option(
    "--binary/--hex",
    "binary",
    default=False,
    show_default=True,
    help="Stream raw sample bytes instead of a hexdump — halves USB bytes-on-"
    "wire, useful at fast --interval-us.",
)
@click.option(
    "--decode",
    type=click.Choice(["none", "i2c", "uart"]),
    default="none",
    show_default=True,
    help="Optionally decode the capture on-device: i2c (SDA/SCL) or uart (RX).",
)
@click.option(
    "--sda",
    type=int,
    default=0,
    show_default=True,
    help="I2C SDA channel (--decode i2c).",
)
@click.option(
    "--scl",
    type=int,
    default=1,
    show_default=True,
    help="I2C SCL channel (--decode i2c).",
)
@click.option(
    "--rx",
    type=int,
    default=0,
    show_default=True,
    help="UART RX channel (--decode uart).",
)
@click.option(
    "--baud",
    type=int,
    default=115200,
    show_default=True,
    help="UART baud rate (--decode uart).",
)
@click.option(
    "--vcd",
    "vcd_path",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Export the raw GP0..GP7 capture to a VCD file (GTKWave/PulseView/sigrok).",
)
@click.option(
    "--timeout-s",
    type=float,
    default=10.0,
    show_default=True,
    help="Max capture time in seconds.",
)
@click.option(
    "--trigger/--no-trigger",
    "trigger",
    default=None,
    help="Block the capture window's start until the trigger channel goes "
    "low, then back it up with pre-trigger history so a decoder has idle "
    "line to sync on (firmware `trig=<ch>`, see "
    "LA_CAPTURE_TRIGGER_IMPLEMENTATION_PLAN.md). Defaults to on for "
    "--decode uart (the motivating case) and off otherwise, so a plain "
    "GPIO/raw capture keeps today's immediate-start behavior.",
)
@click.option(
    "--trigger-ch",
    type=int,
    default=None,
    help="Trigger channel. Defaults to --rx under --decode uart; required "
    "for --trigger with --decode none/i2c.",
)
@click.option(
    "--trigger-timeout-s",
    type=float,
    default=None,
    help="Max time to wait for the trigger before giving up (default: same "
    "as --timeout-s).",
)
@click.pass_context
def la_capture(
    ctx: click.Context,
    interval_us: int,
    n: int,
    binary: bool,
    decode: str,
    sda: int,
    scl: int,
    rx: int,
    baud: int,
    vcd_path: str | None,
    timeout_s: float,
    trigger: bool | None,
    trigger_ch: int | None,
    trigger_timeout_s: float | None,
) -> None:
    """Capture a raw GP0..GP7 logic trace with the firmware logic analyzer.

    Records all 8 scanner-header channels simultaneously at --interval-us µs
    per sample. Export with --vcd for PulseView/GTKWave, or decode a wired
    bus on-device with --decode i2c|uart (channels selected via
    --sda/--scl or --rx).
    """
    if decode == "uart":
        spb = (1_000_000 / baud) / interval_us
        if spb < 4:
            print_warning(
                f"Oversampling ratio is {spb:.1f}× (< 4×) — decoding may be "
                "unreliable. Reduce --interval-us or lower --baud."
            )

    if trigger is None:
        trigger = decode == "uart"

    resolved_trigger_ch: int | None = None
    if trigger:
        # --rx is the motivating case's natural default (--decode uart);
        # --trigger-ch overrides it for --decode none/i2c/custom wiring.
        resolved_trigger_ch = trigger_ch if trigger_ch is not None else rx

    trigger_timeout_ms: int | None = None
    la_call_timeout_s = timeout_s
    if resolved_trigger_ch is not None:
        effective_trigger_timeout_s = (
            trigger_timeout_s if trigger_timeout_s is not None else timeout_s
        )
        trigger_timeout_ms = int(effective_trigger_timeout_s * 1000)
        # The host read deadline covers the firmware's trigger wait too —
        # the "LA:" summary line (and the sample stream after it) doesn't
        # arrive until the trigger fires, so a --trigger-timeout-s longer
        # than --timeout-s must not make the host give up first.
        la_call_timeout_s = max(timeout_s, effective_trigger_timeout_s)

    with _get_scanner_client(ctx) as cli:
        cap = cli.la(
            interval_us,
            n,
            timeout_s=la_call_timeout_s,
            binary=binary,
            trigger_ch=resolved_trigger_ch,
            trigger_timeout_ms=trigger_timeout_ms,
        )
    print_success(f"{len(cap.samples)} samples @ {cap.interval_us}µs (ch=GP0..GP7)")
    if cap.overflow:
        print_warning(
            "firmware reported OVERFLOW — the capture ring (32768 samples) "
            "lapped the USB drain during streaming, so some samples were "
            "silently skipped ahead in time; the trace (and any --decode "
            "output) may be discontinuous/wrong from that point on. Raise "
            "--interval-us, pass --binary to halve bytes-on-wire, or lower "
            "--samples."
        )
    if decode == "uart":
        print_info(f"Decoding UART @ {baud} baud (rx=GP{rx})")

    if vcd_path is not None:
        Path(vcd_path).write_text(samples_to_vcd(cap.samples, cap.interval_us))
        print_success(f"VCD written -> {vcd_path}")

    if decode == "i2c":
        events = decode_i2c(cap.samples, cap.interval_us)
        table = Table(title=f"I2C events ({len(events)})", box=box.ROUNDED)
        table.add_column("t_us", justify="right")
        table.add_column("kind", style=STYLES["device"])
        table.add_column("value", justify="right")
        for event in events:
            value = "" if event.value is None else f"0x{event.value:02x}"
            table.add_row(f"{event.t_us:.1f}", event.kind, value)
        console.print(table)
    elif decode == "uart":
        frames = decode_uart(cap.samples, cap.interval_us, rx_bit=rx, baud=baud)
        if not frames:
            print_info("No UART frames decoded.")
            return
        table = Table(
            box=box.SIMPLE, show_header=True, header_style=STYLES["highlight"]
        )
        table.add_column("t_us", justify="right")
        table.add_column("hex", justify="center")
        table.add_column("ASCII", justify="center")
        table.add_column("framing_error")
        for fr in frames:
            ascii_ch = chr(fr.byte) if 0x20 <= fr.byte < 0x7F else "·"
            fe_str = "[red]FE[/red]" if fr.framing_error else ""
            table.add_row(f"{fr.t_us:.1f}", f"{fr.byte:02X}", ascii_ch, fe_str)
        console.print(table)


@la.command("pulseview")
@click.option(
    "--pulseview/--no-pulseview",
    "open_pulseview",
    default=True,
    help="Launch PulseView on this port, wait for it to close, then "
    "release the port back to the text shell (default: on).",
)
@click.pass_context
def la_pulseview(ctx: click.Context, open_pulseview: bool) -> None:
    """Arm the firmware's SUMP/OLS mode for a live PulseView capture.

    Sends `la sump enter` and disconnects immediately — the firmware then
    speaks the classic SUMP serial protocol on this same port until it's
    explicitly released, which sigrok's stock "Openbench Logic Sniffer"
    (ols) driver expects with no further setup. The full GP0..GP7 bank is
    captured; pick the decoder you need (I2C/UART/SPI/…) inside PulseView.

    By default this also launches PulseView on this same port right away,
    blocks until you close it, and then forces the firmware back to the
    text shell (see ``ScannerClient.force_exit_sump`` — an explicit
    protocol byte, not a DTR drop, so it's instant and doesn't depend on
    how the host OS handles the port close). Pass --no-pulseview to skip
    launching/waiting and drive PulseView yourself; in that case nothing
    releases the port automatically, so re-run this command (with
    --no-pulseview) once you're done to force the release.
    """
    with _get_scanner_client(ctx) as cli:
        reply = cli.la_sump_arm()
        port = cli.port
    print_success(reply)

    if not open_pulseview:
        print_warning(
            "Open PulseView/sigrok-cli on this port NOW (driver: Openbench "
            "Logic Sniffer / ols). Nothing will release the port "
            "automatically — once you're done, run `faultycmd la "
            "pulseview --no-pulseview` again to force it back to the "
            "text shell."
        )
        return

    from .pipes import get_pulseview_path

    pulseview_path = get_pulseview_path()
    proc = None
    if pulseview_path is None:
        print_warning(
            "PulseView not found — open it manually NOW (driver: "
            "Openbench Logic Sniffer / ols, port: "
            f"{port}) before anything else touches this port. Once done, "
            "this command will still release the port for you below."
        )
    else:
        proc = subprocess.Popen(
            [str(pulseview_path), "-d", f"ols:conn={port}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        print_success(f"PulseView launched on {port} (driver: ols)")

    print_info("Waiting for PulseView to close so this port can be released...")
    if proc is not None:
        proc.wait()
    else:
        click.pause("Press any key once you've closed PulseView...")

    print_info(f"Releasing {port} from SUMP mode...")
    with ScannerClient(port, check_firmware_version=False) as release_cli:
        release_cli.force_exit_sump()
    print_success(f"{port} released back to the text shell.")


# -----------------------------------------------------------------------------
# `tui`
# -----------------------------------------------------------------------------


@main.command()
def tui() -> None:
    """Launch the interactive dashboard."""
    try:
        from ..tui.app import run as _run_tui
    except ImportError as e:
        raise click.ClickException(f"TUI is not available: {e}.") from e
    _run_tui()


# -----------------------------------------------------------------------------
# `completion`
# -----------------------------------------------------------------------------


@main.group(context_settings={"help_option_names": ["-h", "--help"]})
def completion() -> None:
    """Install shell tab completion for faultycmd."""


@completion.command("install")
@click.option(
    "--shell",
    type=click.Choice(["bash", "zsh", "fish"]),
    default=None,
    help="Shell to install completion for (auto-detected if omitted)",
)
def completion_install(shell: str | None) -> None:
    """Install tab completion for your shell.

    Run this once, then restart your shell (or source your rc file).

    \b
        faultycmd completion install          # auto-detect shell
        faultycmd completion install --shell zsh
    """
    if platform.system() == "Windows":
        print_error("Shell completion is not supported on Windows.")
        raise SystemExit(1)

    faultycmd_bin = shutil.which("faultycmd")
    if not faultycmd_bin:
        print_error("'faultycmd' not found on PATH.")
        print_dim("Install it first, e.g.: pip install -e .")
        raise SystemExit(1)

    if shell is None:
        shell_env = os.environ.get("SHELL", "")
        if "zsh" in shell_env:
            shell = "zsh"
        elif "fish" in shell_env:
            shell = "fish"
        elif "bash" in shell_env:
            shell = "bash"
        else:
            print_error("Could not detect shell. Use --shell bash|zsh|fish.")
            raise SystemExit(1)
        print_info(f"Detected shell: {shell}")

    env_var = "_FAULTYCMD_COMPLETE"

    if shell == "bash":
        target = (
            Path.home()
            / ".local"
            / "share"
            / "bash-completion"
            / "completions"
            / "faultycmd"
        )
        source_flag = "bash_source"
        rc_note = None
    elif shell == "zsh":
        target = Path.home() / ".zfunc" / "_faultycmd"
        source_flag = "zsh_source"
        rc_note = "fpath=(~/.zfunc $fpath)\nautoload -Uz compinit && compinit"
    else:  # fish
        target = Path.home() / ".config" / "fish" / "completions" / "faultycmd.fish"
        source_flag = "fish_source"
        rc_note = None

    try:
        result = subprocess.run(
            [faultycmd_bin],
            env={**os.environ, env_var: source_flag},
            capture_output=True,
            text=True,
        )
        script = result.stdout
    except Exception as e:
        print_error(f"Failed to generate completion script: {e}")
        raise SystemExit(1)

    if not script.strip():
        print_error("Empty completion script generated.")
        raise SystemExit(1)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(script)
    print_success(f"Completion script written to: {target}")

    if rc_note:
        zshrc = Path.home() / ".zshrc"
        existing = zshrc.read_text() if zshrc.exists() else ""
        if "~/.zfunc" not in existing and ".zfunc" not in existing:
            with zshrc.open("a") as f:
                f.write(f"\n# faultycmd tab completion\n{rc_note}\n")
            print_success(f"Added fpath entry to {zshrc}")
        else:
            print_dim("~/.zfunc already in fpath — skipping .zshrc edit")

    if shell == "bash":
        print_info("Restart your shell or run:")
        print_dim(f"source {target}")
    elif shell == "zsh":
        print_info("Restart your shell or run:")
        print_dim("source ~/.zshrc && compinit -u")
    else:
        print_info("Completion is active immediately in new fish sessions.")


# -----------------------------------------------------------------------------
# `setup-env`
# -----------------------------------------------------------------------------


@click.command("setup-env")
def setup_env() -> None:
    """Setup environment: install udev rules and add user to the dialout group.

    Linux-only. Requires root privileges (sudo). Installs the udev rule
    needed for non-root access to the FaultyCat CDC interfaces
    (VID 1209, PID fa17) and adds the current user to the 'dialout' group.
    """
    if os.geteuid() != 0:
        print_error("Root privileges required. Please run with sudo:")
        print_dim(f"sudo {sys.argv[0]} setup-env")
        raise SystemExit(1)

    # 1. Install udev rule
    rules_content = (
        "# Permission to FaultyCat (VID 1209, PID fa17)\n"
        'SUBSYSTEM=="usb", ATTRS{idVendor}=="1209", ATTRS{idProduct}=="fa17", '
        'MODE="0660", GROUP="dialout", TAG+="uaccess"\n'
    )
    rules_path = Path("/etc/udev/rules.d/99-faultycat.rules")
    try:
        rules_path.write_text(rules_content)
        print_success(f"Udev rule installed to {rules_path}")
    except Exception as e:
        print_error(f"Failed to install udev rule: {e}")

    # 2. Add user to the dialout group
    real_user = os.environ.get("SUDO_USER")
    if not real_user:
        import getpass

        real_user = getpass.getuser()

    try:
        subprocess.run(["usermod", "-aG", "dialout", real_user], check=True)
        print_success(f"User '{real_user}' added to group 'dialout'")
    except subprocess.CalledProcessError:
        print_warning(
            f"Could not add user '{real_user}' to group 'dialout' (does it exist?)"
        )
    except Exception as e:
        print_error(f"Error adding user to group dialout: {e}")

    # 3. Reload udev rules
    try:
        subprocess.run(["udevadm", "control", "--reload-rules"], check=True)
        subprocess.run(["udevadm", "trigger"], check=True)
        print_success("Udev rules reloaded")
    except Exception as e:
        print_warning(f"Could not reload udev rules automatically: {e}")

    print_success("Environment setup complete!")
    print_info("Please log out and log back in for group changes to take effect.")


if platform.system() == "Linux":
    main.add_command(setup_env)


# -----------------------------------------------------------------------------
# Error handling
# -----------------------------------------------------------------------------


def _wrap_main() -> None:
    """Top-level wrapper that converts our protocol exceptions into
    user-friendly click messages."""
    # Skip the ASCII header when click is asked to emit a shell
    # completion script (_FAULTYCMD_COMPLETE) — `completion install`
    # captures stdout and writes it verbatim to the completion file, so
    # any extra output before the script corrupts it (e.g. the zsh
    # `#compdef` line must be the first line).
    if not os.environ.get("_FAULTYCMD_COMPLETE"):
        module = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
        print_header(module)
    try:
        main()
    except VersionMismatchError as e:
        print_error(f"Version mismatch: {e}")
        raise SystemExit(3) from e
    except (EngineError, CampaignError, ScannerError) as e:
        print_error(str(e))
        raise SystemExit(2) from e
    except PortDiscoveryError:
        print_warning("No FaultyCat CDC found.")
        raise SystemExit(1) from None
    except FileNotFoundError as e:
        print_error(f"not found: {e}")
        raise SystemExit(2) from e
    except TimeoutError as e:
        # Covers both the "no shell reply" and the partial-transfer
        # timeouts (e.g. ScannerClient.la) — their messages already
        # carry actionable hints, so just surface them as-is.
        print_error(str(e))
        raise SystemExit(2) from e
    except ValueError as e:
        # Parameter/range validation from the protocol layer (e.g.
        # EmfiClient.capture bounds, usb.cdc_for unknown role).
        print_error(str(e))
        raise SystemExit(2) from e
    except OSError as e:
        # Covers serial.SerialException (it subclasses OSError) for
        # things like the device being unplugged mid-command or the
        # port already being held open by another process.
        print_error(f"device communication error: {e}")
        raise SystemExit(2) from e
    except KeyboardInterrupt:
        print_dim("Aborted by user.")
        raise SystemExit(130) from None


if __name__ == "__main__":
    _wrap_main()
