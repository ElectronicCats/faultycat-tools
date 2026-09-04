# faultycat-lab — `import faultycat as fc`

A **notebook-first Python control layer** for the **FaultyCat v3** board,
in the spirit of ChipWhisperer's `import chipwhisperer as cw`. It gives
you an object-oriented, Jupyter-friendly API for electromagnetic fault
injection (EMFI), voltage-glitch (crowbar) attacks, and parameter-sweep
campaigns — with attribute-style configuration, rich `_repr_html_`
tables, `tqdm` progress bars, and one-call plotting of traces and glitch
maps.

> [!IMPORTANT]
> This package is a thin **ergonomics layer**. The wire protocol,
> CRC framing, and cross-platform USB discovery all live in
> [`faultycmd`](https://github.com/ElectronicCats/faultycat-TUI) (the
> host CLI/TUI), which this package **reuses**. There is exactly one
> source of truth for the FaultyCat protocol — this package never
> re-implements it.

## Install

The notebook layer ships inside the
[`faultycat-TUI`](https://github.com/ElectronicCats/faultycat-TUI) repo as the
`faultycat` package. A per-project `.venv` is the recommended setup — isolated,
reproducible, and **auto-detected by VS Code**.

```bash
git clone https://github.com/ElectronicCats/faultycat-TUI && cd faultycat-TUI
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[notebook]"     # faultycmd transport + faultycat + numpy/pandas/matplotlib/tqdm
pip install ipykernel            # Jupyter kernel for editors / Jupyter Lab
```

One editable install gives you both the `faultycmd` transport and the
`import faultycat as fc` notebook layer. Add the `[interactive]` extra
(`.[notebook,interactive]`) for the optional Plotly glitch map.

**In VS Code:**
1. Open the `faultycat-TUI` folder (`File → Open Folder`).
2. `Ctrl+Shift+P` → **Python: Select Interpreter** → pick `./.venv/bin/python`.
3. Open any notebook under `lab/notebooks/` and set its **kernel** to that
   `.venv` interpreter. Cells then run against the board and plots render
   inline.

The notebooks assume it is installed and contain only real usage — no install
steps. See [EXAMPLES.md](EXAMPLES.md) for the full setup and testing paths.

## Quick start

```python
import faultycat as fc

cat = fc.connect()                 # discover + open all CDC interfaces

# --- single EMFI shot ---
cat.emfi.trigger = "ext_rising"
cat.emfi.delay_us = 100
cat.emfi.width_us = 10
cat.emfi.glitch()                  # apply -> arm -> fire
fc.plot_trace(cat.emfi.capture())  # ADC trace

# --- sweep + classify -> glitch map (ChipWhisperer-style) ---
gc = fc.GlitchController(["delay", "width"])
gc.set_range("delay", range(0, 200, 10)).set_range("width", [10])
for p in gc.glitch_values():
    cat.emfi.delay_us, cat.emfi.width_us = p["delay"], p["width"]
    cat.emfi.glitch()
    resp = cat.uart.read_until(b"\n", timeout=0.1)
    gc.add("success" if b"root" in resp else "normal")   # YOUR condition
gc.plot(x="delay", y="width")      # coloured by the group you assigned

# --- scanner: find SWD/I2C pins, capture logic ---
cat.scanner.swd()                  # -> SwdScanResult(swclk_gp, swdio_gp)
cat.scanner.i2c()                  # -> I2cScanResult(sda, scl, addresses)
cap = cat.scanner.logic(interval_us=1, max_samples=2048, trigger_ch=0)
fc.plot_logic(cap)

# --- target UART: the attack -> observe loop ---
cat.uart.open(baud=115200)
cat.uart.reset_input()
cat.emfi.glitch()
print(cat.uart.read_until(b"\n", timeout=1.0))

cat.close()
```

Every engine object renders as a table in Jupyter — just evaluate
`cat`, `cat.emfi`, or `cat.emfi.status` in a cell.

**Coming from ChipWhisperer?** FaultyCat folds the FI parts of a CW/Husky +
injector into one device. See [../docs/CW-MAPPING.md](../docs/CW-MAPPING.md) for
the CW → `faultycat` API correspondence.

## Try it without a board

`fc.connect(simulator=True)` drives an in-memory FaultyCat that speaks the
real wire protocols — a whole session (glitch, campaign, glitch-map) runs
with no hardware:

```python
cat = fc.connect(simulator=True)
camp = cat.campaign("emfi").configure(delay=(0,180,20), width=(1,25,3))
df = camp.results_df()             # raw firmware fields (fire/verify_status)
fc.glitch_map(df, df.verify_status != 0)   # you pick what a hit means
```

With a real board plugged in, use the staged, safety-gated smoke test
(`python scripts/hw_smoketest.py` — no HV unless you pass `--fire`). See
**[EXAMPLES.md](EXAMPLES.md)** for testing paths and how to author your
own ChipWhisperer-style example notebooks.

## Status

Alpha (`0.1.0`). Implemented and unit-tested (recording stubs, no board
required):

- EMFI / crowbar single-shot + campaign sweep facade
- `GlitchController` (ChipWhisperer-style): sweep, classify, map + `target_reset`
- Typed `ScannerEngine`: `swd()`, `i2c()`, `i2c_probe()`, `logic()`
  (feature-gated by faultycmd capability level)
- `UartTarget`: target-UART passthrough for the attack -> observe loop
- Plotting: `plot_trace`, `glitch_map` (+plotly), `plot_logic`
- Graceful hardware-less discovery (`connect(require=False)`)

Shipped as the `faultycat` package inside the `faultycat-TUI` repo, reusing
`faultycmd` for the transport.
