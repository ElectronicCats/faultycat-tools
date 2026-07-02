# Usage Guide

This guide walks through the most common `faultycmd` workflows: checking
the board, the two fault-injection engines (EMFI / crowbar), automated
sweeps (campaign), the SWD/JTAG scanner, the I2C bus tools, the target UART
bridge, the protocol-agnostic logic analyzer, and the interactive TUI
dashboard.

Every command prints the FaultyCat banner first, then Rich-formatted
output (tables for status, colored success/warning/error lines).

## 1. Discover the Board

```bash
faultycmd devices
```

Lists the 4 CDC interfaces the board exposes (`emfi`, `crowbar`, `scanner`,
`target-uart`) with their OS device paths, and probes the firmware version
via a PING on the `emfi` interface.

```bash
faultycmd verify
```

Runs a communication smoke test against every detected interface (EMFI
ping+status, crowbar ping+status, scanner shell `help`, target-UART CDC
open). Use this right after flashing new firmware or wiring up a board for
the first time. Add `-q`/`--quiet` for just the PASS/FAIL summary.

## 2. Update Firmware

```bash
faultycmd update            # flash if firmware doesn't match this host version
faultycmd update --force    # re-flash even if already matching
```

Downloads the `.uf2` asset from the latest
`ElectronicCats/faultycat-firmware` GitHub release and flashes it over the
RP2040's UF2 bootloader. If the board isn't already in boot mode, you'll be
prompted to put it there manually (it enumerates as an `RPI-RP2` USB
drive) — there's no remote-reboot verb to do that automatically.

## 3. EMFI (Electromagnetic Fault Injection)

```bash
faultycmd emfi ping                                    # check the link
faultycmd emfi configure --trigger immediate \
    --delay-us 0 --width-us 5                           # set pulse params
faultycmd emfi arm                                      # charge the HV cap
faultycmd emfi fire --trigger-timeout-ms 60000           # wait + fire
faultycmd emfi status                                    # read back state
faultycmd emfi disarm                                     # safe the module
faultycmd emfi capture --length 512 --out capture.bin     # read analog capture
```

`--trigger` accepts the values defined by `EmfiTrigger`
(`immediate` and the external-trigger variants); see `--help` on each
subcommand for the live list.

## 4. Crowbar (Voltage Glitch)

Same arm/fire/disarm shape as EMFI, plus an output-power select:

```bash
faultycmd crowbar ping
faultycmd crowbar configure --trigger immediate --output hp \
    --delay-us 0 --width-ns 200
faultycmd crowbar arm
faultycmd crowbar fire
faultycmd crowbar status
faultycmd crowbar disarm
```

`--output lp` is low power (safe to test the link); `--output hp` is the
real glitch.

## 5. Campaign (Automated Parameter Sweeps)

Runs the EMFI or crowbar engine across a delay/width/power grid
automatically, instead of one shot at a time.

```bash
faultycmd campaign --engine crowbar configure \
    --delay 1000:5000:100 \
    --width 100:500:50 \
    --power 1:2:1 \
    --settle-ms 10
faultycmd campaign --engine crowbar start
faultycmd campaign --engine crowbar watch          # live table until done
faultycmd campaign --engine crowbar drain --max 18 # pull buffered results
faultycmd campaign --engine crowbar stop           # abort early
```

Each axis is `START:END:STEP` (or a single fixed value, e.g. `--power 2`).
`watch` follows the sweep live in a Rich table; `drain` pulls whatever
results have accumulated in the firmware's ring buffer (max 18 per
request) without waiting for completion.

## 6. Scanner (SWD Pin Discovery)

```bash
faultycmd scanner scan-swd --timeout-s 30
```

Sweeps the target header to detect SWCLK/SWDIO pins. Pass `--targetsel` to
narrow the sweep if you already know the TARGETSEL value.

>[!Note]
> Direct JTAG/SWD read/write verbs are work-in-progress and intentionally
> hidden in this release.

## 7. I2C Tools

```bash
faultycmd i2c scan                          # sweep all 8 header pins for SDA/SCL + ACKed addrs
faultycmd i2c probe 0 1                      # rescan addresses on known pins (skip full sweep)
faultycmd i2c probe                          # SDA/SCL omitted -> auto-discovers via `scan` first
```

I2C has no fixed pin pair — any of the 8 scanner-header channels can carry
SDA/SCL — so `probe` accepts omitting the pin arguments to auto-discover
them via a full `i2c scan` first.

>[!Note]
> Raw signal capture (with optional on-device I2C/UART decode or VCD/
> PulseView export) has moved to its own top-level `la` command — see
> section 9 below. It's no longer nested under `i2c` or `uart`.

## 8. Target UART Passthrough

Bridges the target's UART through the scanner header (CH0=TX/CH1=RX) to
this terminal.

```bash
faultycmd uart enter --baud 115200 --parity n --stopbits 1
faultycmd uart status
faultycmd uart baud 9600          # reconfigure a live bridge
faultycmd uart exit
```

Or do it in one step with a live console (`Ctrl-X` to exit):

```bash
faultycmd uart console --baud 115200
```

## 9. Logic Analyzer (Protocol-Agnostic)

Captures the full GP0..GP7 scanner-header bank verbatim — the firmware
never interprets the channels. A "protocol" is nothing more than a wiring
convention plus the decoder you pick: on-device with `--decode i2c|uart`,
or host-side in PulseView/sigrok via `la pulseview`. Any digital signal
wired onto GP0..GP7 is fair game, not just I2C/UART.

```bash
faultycmd la capture --samples 4096 --vcd out.vcd            # raw capture, export to VCD
faultycmd la capture --decode i2c --sda 0 --scl 1             # on-device I2C decode
faultycmd la capture --decode uart --rx 0 --baud 115200        # on-device UART decode
faultycmd la pulseview                                         # arm SUMP/OLS mode, auto-launch PulseView
faultycmd la pulseview --no-pulseview                          # arm only, open PulseView yourself
```

`la capture` options of note: `--interval-us` (sample interval, default
2µs), `--binary/--hex` (stream raw bytes instead of a hexdump — halves
USB traffic at fast intervals), `--decode none|i2c|uart` (default
`none`), `--sda`/`--scl` or `--rx`/`--baud` for the chosen decoder, and
`--timeout-s`. Unlike `i2c probe`, `la capture` does **not**
auto-discover SDA/SCL — pass them explicitly (they default to 0/1) or
run `i2c scan` first if you don't already know the wiring.

`la pulseview` takes no pin arguments at all: it arms the firmware's raw
SUMP/OLS capture of all 8 channels and hands off to PulseView, where you
pick which channels are SDA/SCL (or whatever else) and attach the
decoder yourself. For that live-capture workflow, see
[PULSEVIEW_SETUP.md](PULSEVIEW_SETUP.md).

## 10. Interactive TUI

```bash
faultycmd tui
```

Opens a 4-panel dashboard (EMFI status, Crowbar status, Campaign live view,
diagnostic snapshot tail). Hotkeys:

| Key | Action                                    |
| --- | ------------------------------------------ |
| `q` | quit                                        |
| `r` | close all CDCs and reconnect (after reflash) |
| `c` | clear the campaign live log                  |
| `s` | stop the running sweep                       |
| `e` | open the EMFI control modal                  |
| `b` | open the crowBar control modal               |
| `p` | open the camPaign control modal              |
| `n` | open the scan-swd modal                      |

Control modals prefill from the last successfully-applied parameters (see
[02-Configuration.md](02-Configuration.md#persisted-state)).

>[!Note]
> While the TUI is open it holds CDC0 (EMFI) and CDC1 (crowbar/campaign)
> exclusively, and CDC2 (scanner) read-only for the diag tail. Don't run
> `faultycmd scanner`/`i2c`/`uart`/`la` commands or open a serial terminal
> against the same ports from a second window at the same time — they all
> ride the same CDC2 text shell.
