# PulseView setup (AppImage)

`faultycmd la pulseview` arms the firmware's SUMP/OLS mode and then
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
faultycmd la pulseview
```

With `pulseview` resolvable on `PATH`, this arms the firmware and opens
PulseView automatically (driver: `ols`, connected to the scanner CDC
port). Pass `--no-pulseview` to skip the auto-launch and open it
yourself.

`la pulseview` takes no pin arguments — unlike the on-device decoder in
`la capture --decode i2c`, the firmware never learns which channels are
SDA/SCL for a PulseView capture; it just streams the raw GP0..GP7 bank
and you tell PulseView which channels to decode (see step 7). If you
don't already know which pins the target is wired to, run
`faultycmd i2c scan` first — it prints the pair it finds (`Auto-discovered
sda=GP<n> scl=GP<n>`) — and use those channel numbers below. I2C has no
fixed pin pair on FaultyCat; the firmware's bit-bang core can use any of
the 8 scanner-header channels as SDA/SCL (see
[`I2C_SCANNER_INTERNALS.md`](../../faultycat-firmware/docs/I2C_SCANNER_INTERNALS.md)).

## 6. Initial PulseView connection setup

If you open PulseView manually (instead of letting `la pulseview`
launch and connect it for you), you need to point it at the right
driver and port yourself:

1. Identify the scanner's port with:

   ```bash
   faultycmd devices
   ```

   Look for the row with `role` = **scanner** and take its `device`
   column (e.g. `/dev/ttyACM0` on Linux, `COM5` on Windows).

2. In PulseView, click the connection icon (next to the driver
   selector, top left) to open **Connect to Device**.
3. Under **Driver**, pick **Open Bench Logic Sniffer** — this is the
   same `ols` driver `la pulseview` uses internally via
   `-d ols:conn=<port>`.
4. Under **Connection**, select **Serial Port** and enter the port
   found in step 1.
5. Click **Scan for devices**, then **OK**. PulseView should show 8
   channels (`0`–`7`) coming from the firmware.

Once connected, continue with the channel/decoder setup in step 7.

## 7. Configure channels + I2C decoder (one-time)

PulseView's `-d ols:conn=<port>` flag (used internally by
`la pulseview`) only selects the driver and connects to the device — it
cannot also preselect channels or attach a decoder from the command
line. PulseView has no CLI option that combines a live device with a
saved channel/decoder setup (`--settings` only applies when opening a
capture file, not a live device). So this configuration has to be done
once by hand in the GUI; PulseView remembers it from then on.

### Which channels are SDA/SCL?

The firmware's SUMP/OLS implementation
(`faultycat-firmware/services/sump_ols/sump_ols.c`) reports **8
channels** to satisfy the OLS metadata handshake (`NUM_PROBES_LONG =
8`), matching the raw `GPIO_IN[7:0]` byte-per-sample format also used by
the on-device logic analyzer (see `i2c_la.h`). Unlike the on-device
`la capture --decode i2c` path, `la pulseview` never tells the firmware
which pins are SDA/SCL — it just arms a raw capture of all 8 channels,
so *you* have to know which two channels are meaningful and map them to
the decoder yourself:

- If you wired SDA/SCL to `GP0`/`GP1` (the default channels
  `la capture` also assumes), channel **0** is SDA and channel **1** is
  SCL.
- If you're not sure of the wiring, run `faultycmd i2c scan` first — it
  prints the pair it finds (`Auto-discovered sda=GP<n> scl=GP<n>`) —
  and use those channel numbers for the mapping below instead of
  assuming 0/1.

### Steps

1. Run `faultycmd la pulseview` — PulseView opens with the `ols`
   driver on the scanner port, 8 channels visible.
2. In the channel list on the left, disable every channel except the
   two you identified above (e.g. **0** and **1**). Renaming them →
   `SDA`/`SCL` (right-click → rename) isn't required but makes the
   decoder mapping in the next step unambiguous.
3. Click **Add protocol decoder** (`Ctrl+D`), search for **I2C**, add
   it, and assign the `SCL`/`SDA` decoder pins to the matching
   channels.
4. Close PulseView normally — no explicit "save" step needed.

### Why it persists

PulseView auto-saves the last session (device, enabled channels,
decoder stack) to `~/.config/sigrok/PulseView.conf`. On the next
`la pulseview` invocation, PulseView matches the new device against
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

## 8. Sample rate: satisfying Nyquist, and the capture-window trade-off

The firmware's hardware ceiling is a fixed **1 MHz** sample rate
(reported via `METADATA_TOKEN_MAX_SAMPLE_RATE_HZ` in
`faultycat-firmware/services/sump_ols/sump_ols.c`) and a fixed **16384
samples** per capture (`SUMP_OLS_MAX_SAMPLES`,
`faultycat-firmware/services/sump_ols/sump_ols.h`) — half the sampler's
32 KB ring, kept lossless by capturing into the other half before
streaming. Both PulseView's "Samplerate" dropdown and its "Samples"
count map directly onto these two knobs.

### Why the raw Nyquist minimum (2x) isn't enough

The Nyquist rate (2x the signal frequency) is the bare minimum to
reconstruct a *sine wave's* frequency — it says nothing about
resolving *when an edge happened*, which is what a protocol decoder
actually needs (UART's start bit, I2C's clock edges). Sampling right
at 2x can land every sample on the same phase of the signal and miss
transitions entirely. In practice, decoding a digital protocol
reliably needs several samples per bit/clock period, not two per
period:

- **UART @ 115200 baud** — bit period ≈ 8.68 µs. Set PulseView's
  samplerate to **500 kHz** (≈ 4.34 samples/bit) — the practical floor
  for the decoder to place start/stop bit edges correctly; 230.4 kHz
  (the literal 2x Nyquist minimum) is too marginal to trust.
- **I2C fast-mode @ 400 kHz SCL** — clock period = 2.5 µs. Set
  PulseView's samplerate to **1 MHz** (2.5 samples/cycle) — this is
  also the firmware's hardware ceiling, so there's no headroom left to
  go faster if a given bus turns out to need more margin.

### The trade-off: higher sample rate = shorter capture window

Because the sample count is capped at 16384 regardless of rate, a
faster samplerate buys decode accuracy at the cost of a shorter time
window:

| Signal            | Samplerate | Capture window            | Roughly visible                  |
|--------------------|-----------:|---------------------------|-----------------------------------|
| UART @ 115200 baud | 500 kHz    | 16384 / 500 kHz ≈ 32.8 ms | ~375 bytes (8N1 framing)          |
| I2C fast-mode 400 kHz | 1 MHz   | 16384 / 1 MHz ≈ 16.4 ms   | ~6550 SCL cycles (~725 bytes)     |

If you need to observe more bytes/transactions than that, either the
bus is too fast for this analyzer's ceiling to see the full exchange,
or you need to isolate the interesting part of the capture with a
trigger (below) instead of trying to capture everything from power-up.

### Don't waste the window on silence — set a trigger

A capture window of only 16–33 ms is easy to burn entirely on an idle
bus if PulseView just starts sampling at a random moment. Configure a
trigger so the window is centered on actual activity instead of empty
line:

1. In PulseView's channel list, click the small trigger icon on the
   channel that starts the transaction you care about (e.g. the UART
   RX line, or SDA/SCL for I2C) and pick an edge — **falling edge**
   for UART (idle-high, falls at the start bit) or for an I2C start
   condition on SDA.
2. Adjust the **pre-trigger capture ratio** in the sampling toolbar so
   part of the window is captured *before* the trigger fires — this is
   what lets a UART decoder see the idle line preceding the first
   start bit instead of misframing it. The firmware already guarantees
   a minimum of `SUMP_OLS_PRETRIGGER_MIN` (32) samples of pre-trigger
   history whenever any trigger is armed (see `sump_ols.h`), but a
   larger PulseView-requested ratio wins over that floor.
3. Click **Run** — the capture now waits for the trigger condition
   before consuming its (short) sample budget, instead of streaming
   16–33 ms of whatever the bus happened to be doing when you clicked.
