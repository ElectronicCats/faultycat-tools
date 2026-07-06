# FaultyCat TUI — `faultycmd`

`faultycmd` is the host-side tool for the **FaultyCat v3** board: a
click+Rich CLI and Textual TUI for driving electromagnetic fault injection
(EMFI), voltage-glitch (crowbar) attacks, SWD/JTAG target scanning, I2C bus
probing, target-UART passthrough, and automated parameter sweeps
(campaigns) over FaultyCat's USB CDC composite interface. Built and
maintained by Electronic Cats / PWNLab.

>[!WARNING]
> FaultyCat is a fault-injection tool intended solely for authorized
> security research and testing on hardware you own or have explicit
> permission to test. Electronic Cats/PWNLab holds no responsibility for
> unauthorized use or resulting damage.

## Installation

Packaged installers (`.deb`, `.pkg.tar.zst`, macOS `.pkg`, Windows `.exe`)
are published on the [releases page](https://github.com/ElectronicCats/faultycat-TUI/releases).

To install from source:

```bash
git clone https://github.com/ElectronicCats/faultycat-TUI.git
cd faultycat-TUI
python3 -m venv venv && source venv/bin/activate
make install   # equivalent to: pip install -e .
```

See [docs/01-Installation.md](docs/01-Installation.md) for full per-OS
instructions, including PyInstaller binary builds and post-install setup
(`faultycmd setup-env`).

## Usage

```bash
faultycmd devices    # list detected FaultyCat CDC interfaces
faultycmd verify     # smoke-test EMFI, crowbar, scanner, and UART
faultycmd tui        # launch the interactive Textual dashboard
```

See [docs/03-Quick-Reference.md](docs/03-Quick-Reference.md) for the full
command table and [docs/02-Usage-Guide.md](docs/02-Usage-Guide.md) for
step-by-step workflows.

## Documentation

| Doc | What's in it |
| --- | --- |
| [00-Overview.md](docs/00-Overview.md) | Project snapshot and stack |
| [01-Installation.md](docs/01-Installation.md) | Per-OS install steps, prerequisites, uninstall |
| [02-Usage-Guide.md](docs/02-Usage-Guide.md) | Step-by-step workflows: devices/verify, EMFI, crowbar, campaign, scanner, I2C, UART, logic analyzer, TUI |
| [03-Quick-Reference.md](docs/03-Quick-Reference.md) | Full command table + exit codes |
| [04-Troubleshooting.md](docs/04-Troubleshooting.md) | Common failure modes and fixes |
| [PULSEVIEW_SETUP.md](docs/PULSEVIEW_SETUP.md) | Wiring PulseView to `la pulseview` |
| [WINDOWS_SUMP_DTR_ISSUE.md](docs/WINDOWS_SUMP_DTR_ISSUE.md) | Windows DTR root-cause analysis |

## Wiki and Getting Started

[Getting Started in our Wiki](https://github.com/ElectronicCats/FaultyCat/wiki)

[![WIKI](https://github.com/user-attachments/assets/99890265-0602-4206-a05e-5c75bb6a386d)](https://github.com/ElectronicCats/FaultyCat/wiki)

## Firmware Repository
FaultyCat Firmware lives in a separate repository for better version
control and issue tracking:

https://github.com/ElectronicCats/faultycat-firmware

`faultycmd update` fetches and flashes the firmware build matching this
host tool's version.

## Hardware Repository
All FaultyCat Hardware is tracked in its own repository:

https://github.com/ElectronicCats/FaultyCat

## Contribute
<img width="1354" alt="image" src="https://github.com/ElectronicCats/CatSniffer-Tools/assets/15166625/f3d1a1a2-caf5-496f-bc4d-8c7614c8af62">

## How to contribute <img src="https://electroniccats.com/wp-content/uploads/2018/01/fav.png" height="35"><img src="https://raw.githubusercontent.com/gist/ManulMax/2d20af60d709805c55fd784ca7cba4b9/raw/bcfeac7604f674ace63623106eb8bb8471d844a6/github.gif" height="30">
 Contributions are welcome!

Please read the document  [**Contribution Manual**](https://github.com/ElectronicCats/electroniccats-cla/blob/main/electroniccats-contribution-manual.md)  which will show you how to contribute your changes to the project.

✨ Thanks to all our [contributors](https://github.com/ElectronicCats/faultycat-TUI/graphs/contributors)! ✨

See [**_Electronic Cats CLA_**](https://github.com/ElectronicCats/electroniccats-cla/blob/main/electroniccats-cla.md) for more information.

See the  [**community code of conduct**](https://github.com/ElectronicCats/electroniccats-cla/blob/main/electroniccats-community-code-of-conduct.md) for a vision of the community we want to build and what we expect from it.
