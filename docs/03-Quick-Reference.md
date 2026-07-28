# Quick Reference

All commands accept the global `--ignore-version-mismatch` flag before the
subcommand (see ["Version mismatch"](04-Troubleshooting.md#version-mismatch-versionmismatcherror-exit-code-3)
in the troubleshooting guide). Every `--port` option overrides USB
auto-discovery for that engine.

## Top-Level Commands

| Command                  | Description                                                |
| ------------------------- | ------------------------------------------------------------ |
| `faultycmd devices`       | List detected FaultyCat CDC interfaces + probe firmware version |
| `faultycmd verify [-q]`   | Communication smoke test across all interfaces              |
| `faultycmd update [-f]`   | Download + flash the firmware build matching this host       |
| `faultycmd tui`           | Launch the interactive Textual dashboard                     |
| `faultycmd setup-env`     | Install udev rule + add user to `dialout` (run with `sudo`)  |
| `faultycmd completion install [--shell bash\|zsh\|fish]` | Install shell tab completion |

## `emfi` — Electromagnetic Fault Injection (CDC0)

| Command                                   | Description                         |
| ------------------------------------------ | -------------------------------------- |
| `emfi ping`                                | Verify communication                |
| `emfi status`                              | Show current state                  |
| `emfi configure --trigger T --delay-us N --width-us N --charge-timeout-ms N` | Set pulse parameters |
| `emfi arm`                                 | Charge the HV capacitor             |
| `emfi fire [--trigger-timeout-ms N]`       | Wait for trigger and fire           |
| `emfi disarm`                              | Disarm                              |
| `emfi capture [--offset N] [--length N] [--out FILE]` | Read the last fire's analog capture |

## `crowbar` — Voltage Glitch (CDC1)

| Command                                   | Description                         |
| ------------------------------------------ | -------------------------------------- |
| `crowbar ping`                             | Verify communication                |
| `crowbar status`                           | Show current state                  |
| `crowbar configure --trigger T --output lp\|hp --delay-us N --width-ns N` | Set glitch parameters |
| `crowbar arm`                              | Arm                                  |
| `crowbar fire [--trigger-timeout-ms N]`    | Wait for trigger and fire            |
| `crowbar disarm`                           | Disarm                               |

## `campaign --engine emfi|crowbar` — Automated Sweeps

| Command                                   | Description                         |
| ------------------------------------------ | -------------------------------------- |
| `campaign status`                          | Show sweep state                     |
| `campaign configure --delay R --width R --power R [--trigger T] [--settle-ms N]` | Define sweep ranges (`R` = `START:END:STEP` or fixed; `T` = `immediate`/`ext_rising`/`ext_falling`/`ext_pulse_pos`/`ext_pulse_neg`, applied to the engine before the sweep) |
| `campaign start`                           | Start the sweep                      |
| `campaign watch [--every-ms N]`            | Follow the sweep live                |
| `campaign drain [--max N]`                 | Download buffered results (max 18/request) |
| `campaign stop`                            | Abort the running sweep              |

## `scanner` — SWD Pin Discovery (CDC2)

| Command                                                  | Description              |
| ----------------------------------------------------------- | --------------------------- |
| `scanner scan-swd [--targetsel HEX] [--timeout-s N]`      | Detect target SWCLK/SWDIO |

## `i2c` — I2C Bus Tools (CDC2)

| Command                                                              | Description                              |
| ------------------------------------------------------------------------ | ------------------------------------------- |
| `i2c scan [--timeout-s N]`                                              | Sweep all pins for SDA/SCL + ACKed addresses |
| `i2c probe [SDA SCL] [--timeout-s N] [--scan-timeout-s N]`              | Rescan addresses on known (or auto-discovered) pins |

`SDA`/`SCL` are positional and optional on `probe` — omit both to
auto-discover via `i2c scan`.

## `uart` — Target UART Passthrough

| Command                                                       | Description                       |
| ------------------------------------------------------------------ | ------------------------------------ |
| `uart enter [--baud N] [--parity n\|e\|o] [--stopbits 1\|2]`       | Enable the bridge                  |
| `uart exit`                                                        | Disable the bridge                  |
| `uart status`                                                      | Show bridge configuration           |
| `uart baud VALUE`                                                  | Reconfigure baud on a live bridge   |
| `uart parity n\|e\|o`                                              | Reconfigure parity on a live bridge |
| `uart stopbits 1\|2`                                               | Reconfigure stop bits on a live bridge |
| `uart console [--target-port DEV] [--baud N] [--parity ...] [--stopbits ...]` | Enable + open a live byte console (`Ctrl-X` to exit) |

## `la` — Protocol-Agnostic Logic Analyzer (CDC2)

| Command                                                              | Description                              |
| ------------------------------------------------------------------------ | ------------------------------------------- |
| `la capture [--interval-us N] [--samples N] [--binary/--hex] [--decode none\|i2c\|uart] [--sda N] [--scl N] [--rx N] [--baud N] [--vcd FILE] [--timeout-s N] [--trigger/--no-trigger] [--trigger-ch N] [--trigger-timeout-s N]` | Capture GP0..GP7, optionally decode on-device or export to VCD |
| `la pulseview [--pulseview/--no-pulseview]`                            | Arm SUMP/OLS mode for a live PulseView/sigrok capture |

`la capture` does not auto-discover I2C pins — `--sda`/`--scl` default
to 0/1; run `i2c scan` first if you don't know the wiring. `la
pulseview` takes no pin arguments at all (it captures all 8 channels
raw); pick SDA/SCL/etc. inside PulseView.

`--trigger` defaults to on for `--decode uart` and off otherwise; it
delays the capture window until `--trigger-ch` (or `--rx`) goes low,
bounded by `--trigger-timeout-s` (default: same as `--timeout-s`).

## Exit Codes

| Code | Meaning                                                  |
| ---- | ----------------------------------------------------------- |
| `0`  | Success                                                     |
| `1`  | No FaultyCat CDC found / verification failed                |
| `2`  | Engine/protocol error, parameter validation error, device communication error, file not found |
| `3`  | Firmware/host version (board) mismatch                      |
| `130`| Aborted by user (Ctrl-C)                                    |
