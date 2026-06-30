# Installation

`faultycmd` is the host-side CLI/TUI for the **FaultyCat v3** fault-injection
board (EMFI / crowbar / SWD-JTAG scanner / I2C / campaign sweeps), talking to
the board over a USB CDC composite interface (VID `1209`, PID `FA17`).

>[!Note]
> `faultycmd` is pure Python (`pyserial`, `click`, `rich`, `textual`) — no
> native libraries (libusb, etc.) are required to run it from source.

## Prerequisites

- A FaultyCat v3 board, connected via USB.
- Firmware matching board id `2` (`EXPECTED_BOARD` in
  [`utils/version_check.py`](../src/faultycmd/utils/version_check.py)). Use
  `faultycmd update` after install to flash the latest compatible firmware.
- For installing **from source**: Python 3.10+ and `pip`.

---

## Windows

1. Download the installer **`faultycmd-x.x.x.exe`** from the
   [**releases section of the faultycat repository**](https://github.com/ElectronicCats/faultycat/releases).
2. Run the installer and follow the Inno Setup wizard. Leave **"Add FaultyCat
   to PATH"** checked so `faultycmd` is callable from any terminal.
3. The installer offers to run `faultycmd.exe --help` at the end to verify
   the install.

The Start Menu group **FaultyCat** gets a CLI shortcut, a link to
`README.md`, and an uninstaller.

---

## macOS

- System requirements:
  - macOS 11 (Big Sur) or newer
  - Intel or Apple Silicon (M1/M2/M3) Mac

Check your architecture first:

```bash
uname -m
```

- `arm64` → Apple Silicon installer
- `x86_64` → Intel installer

### Intel Macs (x86_64)

1. Download **`faultycmd-x.x.x-x86_64.pkg`** from the
   [**releases section**](https://github.com/ElectronicCats/faultycat/releases).
2. Open a terminal in the download location and run:

```bash
sudo installer -allowUntrusted -pkg faultycmd-x.x.x-x86_64.pkg -target /
```

### Apple Silicon Macs

1. Download **`faultycmd-x.x.x-arm64.pkg`** from the
   [**releases section**](https://github.com/ElectronicCats/faultycat/releases).
2. Open a terminal in the download location and run:

```bash
sudo installer -allowUntrusted -pkg faultycmd-x.x.x-arm64.pkg -target /
```

Both `.pkg` variants install to `/usr/local/opt/faultycmd/` and symlink the
binary to `/usr/local/bin/faultycmd`.

### Post-Installation (macOS)

```bash
faultycmd setup-env
```

This installs udev-equivalent permissions for the FaultyCat USB CDC
interfaces and adds your user to the `dialout` group where applicable. Log
out and back in for group changes to apply.

---

## Linux

### Debian/Ubuntu (.deb)

1. Download **`faultycmd-x.x.x.deb`** from the
   [**releases section**](https://github.com/ElectronicCats/faultycat/releases).
2. Install it:

```bash
sudo dpkg -i faultycmd-x.x.x.deb
sudo apt-get install -f   # resolve any missing dependencies
```

The package's `postinst` script creates the `dialout` group if missing and
reloads udev rules; the udev rule itself
(`/lib/udev/rules.d/99-faultycat.rules`) and a desktop launcher are bundled
in the package.

>[!Note]
> Verify the install with:
>
> ```bash
> faultycmd --help
> ```

### Arch Linux (.pkg.tar.zst)

1. Download **`faultycmd-x.x.x.pkg.tar.zst`** from the
   [**releases section**](https://github.com/ElectronicCats/faultycat/releases).
2. Install it:

```bash
sudo pacman -U faultycmd-x.x.x.pkg.tar.zst
```

### Install from Source (all Linux distros, plus macOS/Windows for development)

1. Clone the repository:

```bash
git clone https://github.com/ElectronicCats/faultycat-TUI.git
cd faultycat-TUI
```

2. Create and activate a virtual environment (recommended):

```bash
python3 -m venv venv
source venv/bin/activate     # Windows: venv\Scripts\activate
```

3. Install in editable mode:

```bash
make install
# equivalent to: pip install -e .
```

4. Or build a standalone PyInstaller binary instead:

```bash
make compile-install
```

This runs `pyinstaller faultycmd.spec` and copies the result to
`/usr/local/bin/faultycmd` (requires `sudo`).

#### Post-Installation (Linux)

```bash
sudo faultycmd setup-env
```

This installs `/etc/udev/rules.d/99-faultycat.rules` for non-root access to
VID `1209` / PID `FA17`, and adds the invoking user (`$SUDO_USER`) to the
`dialout` group. Log out and back in for the group change to take effect.

---

## Verifying the Installation

Regardless of platform, confirm the board and host are talking correctly:

```bash
faultycmd devices    # lists the 4 CDC interfaces FaultyCat exposes
faultycmd verify     # smoke-tests EMFI, crowbar, scanner, and target-UART
```

See [03-Usage-Guide.md](03-Usage-Guide.md) for what these commands report.

---

## Extra: Shell Completion (macOS/Linux)

```bash
faultycmd completion install            # auto-detects bash/zsh/fish
faultycmd completion install --shell zsh
```

Restart your shell afterwards. Not supported on Windows.

---

## Uninstallation

### Windows

Settings → Apps → Installed Apps → **FaultyCat** → Uninstall (or re-run the
installer and choose "Remove").

### macOS

```bash
sudo rm -rf /usr/local/opt/faultycmd
sudo rm -f /usr/local/bin/faultycmd
```

### Linux

```bash
sudo dpkg -r faultycmd        # Debian/Ubuntu
sudo pacman -R faultycmd      # Arch
```

### From source

```bash
make uninstall
```
