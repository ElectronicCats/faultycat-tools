# PulseView setup (AppImage)

`faultycmd i2c la-sump-arm` arms the firmware's SUMP/OLS mode and then
auto-launches PulseView via `shutil.which("pulseview")`. That only works
if a `pulseview` binary is reachable on `PATH` — a raw AppImage downloaded
from the site below is **not** picked up by name, since its filename
includes a version/build hash (e.g.
`pulseview-NIGHTLY-x86_64-debug_<hash>.AppImage`).

## 1. Download the AppImage

Get the Linux AppImage from the official sigrok downloads page:

<https://sigrok.org/wiki/Downloads#Linux_distribution_packages>

## 2. Make it executable

```bash
chmod +x ~/Applications/pulseview-*.AppImage
```

(Place it wherever you keep AppImages — `~/Applications/` is just a
convention used here.)

## 3. Expose it as `pulseview` on PATH

`faultycmd` looks for the literal name `pulseview`, so symlink it from a
directory already on your `PATH` (`~/.local/bin` is on `PATH` by default
on most distros):

```bash
mkdir -p ~/.local/bin
ln -sf ~/Applications/pulseview-*.AppImage ~/.local/bin/pulseview
```

## 4. Verify

```bash
which pulseview
pulseview --version
```

`which pulseview` must resolve to the symlink above. Some startup
warnings from the bundled AppImage runtime (missing Qt translations,
`_ctypes` import errors for unrelated protocol decoders) are benign and
can be ignored.

## 5. Use it

```bash
faultycmd i2c la-sump-arm <sda> <scl>
```

With `pulseview` resolvable on `PATH`, this arms the firmware and opens
PulseView automatically (driver: `ols`, connected to the scanner CDC
port). Pass `--no-pulseview` to skip the auto-launch and open it
yourself.

`<sda>`/`<scl>` can be omitted (`faultycmd i2c la-sump-arm`). I2C has
no fixed pin pair — the firmware's bit-bang core can use any of the 8
scanner-header channels as SDA/SCL (see
[`I2C_SCANNER_INTERNALS.md`](../../faultycat-firmware/docs/I2C_SCANNER_INTERNALS.md)) —
so when they're left out, the CLI runs a full `i2c scan` sweep first
to find them before arming SUMP mode.

## 6. Configure channels + I2C decoder (one-time)

PulseView's `-d ols:conn=<port>` flag (used internally by
`la-sump-arm`) only selects the driver and connects to the device — it
cannot also preselect channels or attach a decoder from the command
line. PulseView has no CLI option that combines a live device with a
saved channel/decoder setup (`--settings` only applies when opening a
capture file, not a live device). So this configuration has to be done
once by hand in the GUI; PulseView remembers it from then on.

### Why only GP0/GP1 matter

The firmware's SUMP/OLS implementation
(`faultycat-firmware/services/sump_ols/sump_ols.c`) reports **8
channels** to satisfy the OLS metadata handshake (`NUM_PROBES_LONG =
8`), matching the raw `GPIO_IN[7:0]` byte-per-sample format used by
`i2c_la` (see `i2c_la.h`). Only two of those eight bits are ever
meaningful for an I2C capture:

- bit/channel **0** = `GP0` = whichever pin you pass as `<sda>`
- bit/channel **1** = `GP1` = whichever pin you pass as `<scl>`

`faultycmd i2c la-sump-arm 0 1` arms `sda=GP0`, `scl=GP1` — so for
that exact invocation, channel 0 is SDA and channel 1 is SCL. If you
arm with different pin numbers, map the channels accordingly (channel
N corresponds to GPIO N for whichever role you assigned it).

If you omit `<sda>`/`<scl>` and let the CLI auto-discover them via
`i2c scan`, it prints the pair it found (`Auto-discovered sda=GP<n>
scl=GP<n>`) before arming — use those numbers for the channel mapping
in step 2/3 below instead of assuming GP0/GP1.

### Steps

1. Run `faultycmd i2c la-sump-arm 0 1` — PulseView opens with the
   `ols` driver on the scanner port, 8 channels visible.
2. In the channel list on the left, disable every channel except
   **0** and **1**. Renaming `0` → `SDA` and `1` → `SCL` (right-click
   → rename) isn't required but makes the decoder mapping in the next
   step unambiguous.
3. Click **Add protocol decoder** (`Ctrl+D`), search for **I2C**, add
   it, and assign:
   - `SCL` (decoder pin) → channel **1**
   - `SDA` (decoder pin) → channel **0**
4. Close PulseView normally — no explicit "save" step needed.

### Why it persists

PulseView auto-saves the last session (device, enabled channels,
decoder stack) to `~/.config/sigrok/PulseView.conf`. On the next
`la-sump-arm` invocation, PulseView matches the new device against
that saved entry by **model** (`FaultyCat I2C LA`, reported via the
SUMP metadata device-name token) and **connection_id** (the serial
port path) — if both match, it restores the exact channel/decoder
setup automatically, with no manual steps.

### Caveat: port path must match

The match is keyed on the device path (e.g. `/dev/ttyACM0`), not just
"the FaultyCat board". If the board enumerates on a different path
next time (e.g. `/dev/ttyACM1` after a reconnect, or a different port
on Windows/macOS), PulseView won't find a saved session for that path
and opens a fresh, unconfigured one — repeat steps 2–3 once for that
port.
