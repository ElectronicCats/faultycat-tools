# Troubleshooting

## "No FaultyCat CDC found" (`PortDiscoveryError`, exit code 1)

`faultycmd` discovers the board by USB VID `1209` / PID `FA17` and never
caches a port — it re-enumerates on every command.

1. Confirm the board shows up at the OS level:
   - Linux: `lsusb | grep 1209:fa17`, or check `/dev/ttyACM*`
   - Windows: Device Manager → Ports (COM & LPT)
   - macOS: `ls /dev/cu.usbmodem*`
2. Try a different USB cable/port — some cables are power-only.
3. Run `faultycmd setup-env` (Linux/macOS) and log out/in — missing udev
   permissions make the device invisible to a non-root user without
   raising an OS-level error.
4. Run `faultycmd devices` for the most detailed view of what was found.

## "Version mismatch" (`VersionMismatchError`, exit code 3)

The firmware's reported board id doesn't match this host's
`EXPECTED_BOARD`. This means either:

- The board is running firmware built for a different FaultyCat hardware
  revision — re-flash with `faultycmd update`.
- The firmware predates version embedding (a stale dev/beta build) — the
  error message will say so explicitly.

For active firmware development against a hand-built UF2 only, bypass with:

```bash
faultycmd --ignore-version-mismatch <command> ...
```

See [02-Configuration.md](02-Configuration.md#firmwarehost-version-parity).

## `faultycmd verify` fails

Run `faultycmd verify` (not `-q`) to see which interface failed
specifically (EMFI, crowbar, scanner shell, or target-UART). Standard
checklist:

1. Make sure the FaultyCat is connected via USB.
2. Try reconnecting the USB cable.
3. Check udev rules / `dialout` group membership: `sudo faultycmd setup-env`.

## Permission denied opening the serial port (Linux)

The udev rule wasn't installed, or the user wasn't added to `dialout`, or
the group membership hasn't taken effect yet (group changes require a
fresh login session — sometimes a full reboot for desktop session
managers).

```bash
sudo faultycmd setup-env
# then log out and back in (or reboot if it still doesn't take)
```

## EMFI/crowbar `arm`/`fire` commands time out

- Check `<engine> status` first — a stale `armed` state from a previous
  session that crashed mid-sequence can require a `disarm` before
  re-arming.
- `fire --trigger-timeout-ms` defaults to 60000 (60s) — if you configured
  an external trigger (`--trigger ext_rising`, etc.) the command blocks
  until that signal arrives or the timeout elapses.

## `i2c la-sump-arm` opens PulseView but it shows no channels

This is almost always one of:

1. **`pulseview` not on `PATH`** — a raw downloaded AppImage isn't picked
   up by name. See [PULSEVIEW_SETUP.md](PULSEVIEW_SETUP.md).
2. **Windows DTR race** — the firmware reverts out of SUMP mode before
   PulseView connects, due to how `usbser.sys` handles port close. A 60s
   firmware-side grace period mitigates this, but the window can still be
   missed. Full root-cause writeup: [WINDOWS_SUMP_DTR_ISSUE.md](WINDOWS_SUMP_DTR_ISSUE.md).
3. **Channel/decoder config not yet saved for this port** — see
   [PULSEVIEW_SETUP.md §7](PULSEVIEW_SETUP.md#7-configure-channels--i2c-decoder-one-time);
   PulseView keys its saved session on device model + port path, so a
   different `/dev/ttyACMx` or `COMx` needs the one-time setup repeated.

## `i2c scan`/`probe`/`la` find nothing

I2C has no fixed SDA/SCL pin pair on FaultyCat — any of the 8
scanner-header channels can carry either signal. Confirm the target is
actually wired to the scanner header and powered, then re-run `i2c scan`
(used internally for auto-discovery whenever `SDA`/`SCL` are omitted from
`probe`/`la`/`la-sump-arm`).

## Shell completion: `'faultycmd' not found on PATH`

`completion install` shells out to the installed `faultycmd` binary to
generate the script, so it must already be importable/installed first:

```bash
pip install -e .          # from a source checkout
# or make sure the packaged install's bin dir is on PATH
```

Not supported on Windows.

## TUI: stale or conflicting serial sessions

The TUI holds CDC0 (EMFI) and CDC1 (crowbar/campaign) exclusively while
open, and CDC2 (scanner) read-only for the diagnostic tail. Running
`faultycmd scanner`/`i2c ...` from a second terminal, or opening a serial
monitor against the same ports, while the TUI is open will conflict. Quit
the TUI (`q`) first, or use the `n` (scan-swd modal) hotkey inside the TUI
instead.

If the board was just re-flashed while the TUI was open, press `r` to
close and reopen all CDCs rather than restarting the TUI.

---

## Still stuck?

- Re-run with the full (non-quiet) `faultycmd verify` output and capture it.
- Check [PULSEVIEW_SETUP.md](PULSEVIEW_SETUP.md) and
  [WINDOWS_SUMP_DTR_ISSUE.md](WINDOWS_SUMP_DTR_ISSUE.md) for the two
  documented edge cases in this repo.
- File an issue at
  [github.com/ElectronicCats/faultycat/issues](https://github.com/ElectronicCats/faultycat/issues)
  with the `faultycmd devices` and `faultycmd verify` output attached.

[TODO: confirm the public issue tracker URL — `pyproject.toml` points at
`ElectronicCats/faultycat/issues`, but firmware-side issues may belong in
a separate `faultycat-firmware` repo instead.]
