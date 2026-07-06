# FaultyCat TUI — Documentation Overview

**`faultycmd`** is the host-side tool for the **FaultyCat v3** board: a
click+Rich CLI and Textual TUI for driving electromagnetic fault injection
(EMFI), voltage-glitch (crowbar) attacks, SWD/JTAG target scanning, I2C bus
probing, a protocol-agnostic logic analyzer, and automated parameter sweeps
(campaigns) over FaultyCat's USB CDC composite interface. It's built and
maintained by Electronic Cats / PWNLab.

>[!WARNING]
> FaultyCat is a fault-injection tool intended solely for authorized
> security research and testing on hardware you own or have explicit
> permission to test. Electronic Cats/PWNLab holds no responsibility for
> unauthorized use or resulting damage.

This is a base set of internal docs for the support team to draw from when
building the public wiki — it favors accuracy and coverage over polish.

## Project Snapshot

- **Language/stack**: Python 3.10+, [`click`](https://click.palletsprojects.com/)
  for the CLI, [`rich`](https://rich.readthedocs.io/) for terminal output,
  [`textual`](https://textual.textualize.io/) for the TUI, `pyserial` for
  the USB CDC transport.
- **Package name**: `faultycmd` (binary on `PATH` is also `faultycmd`).
- **Distribution**: PyInstaller-built standalone binaries packaged per
  platform — `.deb` (Debian/Ubuntu), `.pkg.tar.zst` (Arch), `.pkg`
  (macOS Intel/ARM), `.exe` Inno Setup installer (Windows) — built via
  GitHub Actions on every push/release. Source install via `pip install -e .`
  works everywhere.
- **Hardware target**: FaultyCat v3, USB VID `1209` / PID `FA17`, exposing
  4 CDC interfaces (EMFI, crowbar, scanner shell, target-UART passthrough).
- **Firmware repo**: `ElectronicCats/faultycat-firmware` (separate repo;
  `faultycmd update` fetches its latest `.uf2` release).

## Recent Changes (this update)

Headline changes on this branch (`i2c_scanner`) since it diverged from `main`:

- **I2C bus tools**: `i2c scan`/`i2c probe` with auto-discovery of SDA/SCL
  across all 8 scanner-header channels.
- **Logic analyzer unified under `la`**: `la capture` (raw GP0..GP7 capture,
  optional on-device I2C/UART decode, VCD export) and `la pulseview` (SUMP/
  OLS mode for PulseView/sigrok) are now their own top-level command group,
  no longer nested under `i2c`/`uart`.
- **`la capture` trigger support**: `--trigger`/`--trigger-ch`/
  `--trigger-timeout-s` delay the capture window until a channel goes low,
  for reliable UART/I2C decode sync.
- **Firmware update (`faultycmd update`)**: downloads and flashes the
  `.uf2` release matching this host's version; now tries a remote
  1200-baud boot-mode trigger before falling back to manual boot-mode
  instructions.
- **Windows SUMP/DTR reliability fix**: SUMP mode exit uses an explicit
  protocol byte instead of relying on DTR, plus a grace period — see
  [WINDOWS_SUMP_DTR_ISSUE.md](WINDOWS_SUMP_DTR_ISSUE.md).
- **`faultycmd verify`**: new communication smoke-test command across all
  interfaces.
- **Cross-platform packaging**: `.deb`, `.pkg.tar.zst` (Arch), macOS
  `.pkg`, Windows installer, plus shell completion install.
- **Internal refactors**: shared sweep-axis parser reused by CLI and TUI,
  a shared triplet-parsing utility, and TUI control-modal mixins
  (status-line, dict-form) to cut duplication.

## Where to Go Next

| Doc | What's in it |
| --- | --- |
| [01-Installation.md](01-Installation.md) | Per-OS install steps (packaged installers + from source), prerequisites, uninstall |
| [02-Usage-Guide.md](02-Usage-Guide.md) | Step-by-step workflows: devices/verify, EMFI, crowbar, campaign, scanner, I2C, UART, logic analyzer, TUI |
| [03-Quick-Reference.md](03-Quick-Reference.md) | Full command table + exit codes |
| [04-Troubleshooting.md](04-Troubleshooting.md) | Common failure modes and fixes |
| [PULSEVIEW_SETUP.md](PULSEVIEW_SETUP.md) | Existing deep-dive: wiring PulseView to `la pulseview` |
| [WINDOWS_SUMP_DTR_ISSUE.md](WINDOWS_SUMP_DTR_ISSUE.md) | Existing deep-dive: Windows DTR root-cause analysis (in Spanish) |
