# Quick Reference

All commands accept the global `--ignore-version-mismatch` flag before the
subcommand (see [02-Configuration.md](02-Configuration.md#firmwarehost-version-parity)).
Every `--port` option overrides USB auto-discovery for that engine.

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
| `campaign configure --delay R --width R --power R [--settle-ms N]` | Define sweep ranges (`R` = `START:END:STEP` or fixed) |
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
| `i2c la [SDA SCL] [--interval-us N] [--samples N] [--decode/--no-decode] [--vcd FILE] [--timeout-s N]` | Capture + decode a raw SDA/SCL trace |
| `i2c la-sump-arm [SDA SCL] [--pulseview/--no-pulseview] [--scan-timeout-s N]` | Arm SUMP/OLS mode for live PulseView capture |

`SDA`/`SCL` are positional and optional on `probe`, `la`, and
`la-sump-arm` — omit both to auto-discover via `i2c scan`.

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

## Exit Codes

| Code | Meaning                                                  |
| ---- | ----------------------------------------------------------- |
| `0`  | Success                                                     |
| `1`  | No FaultyCat CDC found / verification failed                |
| `2`  | Engine/protocol error, parameter validation error, device communication error, file not found |
| `3`  | Firmware/host version (board) mismatch                      |
| `130`| Aborted by user (Ctrl-C)                                    |
