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
