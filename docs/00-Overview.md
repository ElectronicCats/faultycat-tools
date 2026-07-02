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

[TODO: list the headline changes from this release for the support team —
e.g. the i2c_scanner branch work: I2C auto-discovery of SDA/SCL, the
protocol-agnostic logic analyzer being unified under its own top-level
`la` command (`la capture` / `la pulseview`, no longer nested under `i2c`
or `uart`), the Windows SUMP/DTR grace-period fix, plus the internal
refactors (status-line mixin, dict-form mixin, getattr cleanups). Recent
commit titles on this branch (`i2c_scanner`) suggest this list but should
be confirmed/expanded by whoever is closest to the release.]

## Where to Go Next

| Doc | What's in it |
| --- | --- |
| [01-Installation.md](01-Installation.md) | Per-OS install steps (packaged installers + from source), prerequisites, uninstall |
| [02-Configuration.md](02-Configuration.md) | Environment variables, persisted state, version-parity check, port discovery, permissions |
| [03-Usage-Guide.md](03-Usage-Guide.md) | Step-by-step workflows: devices/verify, EMFI, crowbar, campaign, scanner, I2C, UART, logic analyzer, TUI |
| [04-Quick-Reference.md](04-Quick-Reference.md) | Full command table + exit codes |
| [05-Troubleshooting.md](05-Troubleshooting.md) | Common failure modes and fixes |
| [PULSEVIEW_SETUP.md](PULSEVIEW_SETUP.md) | Existing deep-dive: wiring PulseView to `la pulseview` |
| [WINDOWS_SUMP_DTR_ISSUE.md](WINDOWS_SUMP_DTR_ISSUE.md) | Existing deep-dive: Windows DTR root-cause analysis (in Spanish) |

>[!Note]
> The repository's top-level `README.md` currently describes the older,
> general `CatSniffer-Tools` repo (catnip/cc2538-bsl/pycatsniffer_bv3) and
> does not describe `faultycmd` or this project. [TODO: flag this to the
> team — the README likely needs to be replaced with FaultyCat-specific
> content; this doc set does not modify it.]
