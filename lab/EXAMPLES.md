# Testing faultycat-lab & authoring your own examples

Three ways to exercise the package — pick by what you have on hand — plus
how to build a ChipWhisperer-style set of example notebooks.

## 0. Setup

```bash
cd faultycat-lab
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[notebook,dev]"
```

> **faultycmd version matters.** The transport comes from `faultycmd`.
> A *minimal* faultycmd only exposes EMFI / crowbar / campaign / SWD; the
> **full** feature set (I2C, target-UART, logic-analyzer) needs the
> faultycmd from the [`faultycat-TUI`](https://github.com/ElectronicCats/faultycat-TUI)
> repo. Install it alongside to unlock everything:
>
> ```bash
> pip install -e ../faultycat-TUI      # richer faultycmd (1.x)
> ```
>
> The package feature-gates cleanly either way: calls that need a missing
> capability raise a plain "upgrade faultycmd" error, never a crash.

## 1. Unit tests (fastest, no board)

```bash
pytest -q
```

Drives the facade with recording stubs and the in-memory simulator — no
serial ports touched. This is what CI runs.

## 2. The simulator (whole notebooks, no board)

`fc.connect(simulator=True)` swaps in an in-memory FaultyCat that speaks
the real wire protocols, so an entire session runs without hardware:

```python
import faultycat as fc
cat = fc.connect(simulator=True)
cat.emfi.glitch()
camp = cat.campaign("emfi").configure(delay=(0,180,20), width=(1,25,3))
camp.run(); fc.glitch_map(camp.results_df())   # synthetic success cluster
```

Every notebook in `notebooks/` takes a `SIM` flag in its first cell — set
`SIM = True` to run it on the simulator. The numbers are synthetic (a
planted glitch-success region), so it's for **developing and testing
examples**, not real results. Logic-analyzer capture isn't simulated (it
needs the streaming binary reply); everything else is.

To execute notebooks headlessly (rot-proofing for CI):

```bash
pip install nbmake
pytest --nbmake notebooks/    # each defaults to SIM=False; flip for boardless CI
```

## 3. A real board (staged, safety-gated)

With a FaultyCat plugged in, use the staged smoke test. It escalates in
risk and **never fires high voltage** unless you pass `--fire` and confirm.

```bash
python scripts/hw_smoketest.py          # stages 0-2: detect, connect, status  (SAFE)
python scripts/hw_smoketest.py --scan   # + SWD/I2C scan (touches target pins, no HV)
python scripts/hw_smoketest.py --fire   # + ONE EMFI shot (⚡ HIGH VOLTAGE, prompts first)
```

⚠️ **Safety.** `--fire` arms and discharges the EMFI HV capacitor. Only
use it with the plastic shield installed and with a known target (or
nothing) under the coil. See the FaultyCat hardware README.

In a notebook, the same board is just `fc.connect()` (no `simulator=`):

```python
cat = fc.connect()          # auto-discovers by VID:PID
cat.emfi.trigger = "ext_rising"; cat.emfi.delay_us = 100; cat.emfi.width_us = 10
cat.emfi.glitch()           # real pulse — shield on!
```

## 4. Authoring your own examples (the ChipWhisperer way)

ChipWhisperer ships tutorials as **versioned, numbered notebooks** in a
dedicated repo (`chipwhisperer-jupyter`). Mirror that here:

- **Numbered, task-named files** under `notebooks/`, sequential, e.g.
  `00-getting-started`, `01-glitching`, `02-fault-injection`,
  `03-jtagulator-swd`, `04-<your-target>-secure-boot-bypass`. The number
  is simple reading order.
- **Open with the simulator, close with hardware.** Write and debug the
  notebook against `fc.connect(simulator=True)`; the only change to run it
  on a board is dropping `simulator=True`. Keep that first cell a
  one-liner so the switch is obvious.
- **A cell tells its own story.** Configure → act → observe → plot, in
  that order. The facade's `_repr_html_` means evaluating `cat.emfi` or
  `cat.emfi.status` renders a table — use that instead of `print()`.
- **Define "success" for *your* target.** The default glitch-map success
  is `verify_status != 0`; override it per target:
  ```python
  df["success"] = df["target_state"] == 2      # or your own check
  fc.glitch_map(df)
  ```
- **Keep notebooks executable in CI** with `nbmake` against the simulator,
  so an API change that breaks an example fails the build instead of
  rotting silently.
- **Parameterize, don't hard-code.** Put target-specific pins/baud/axes in
  the first code cell so a reader adapts one place.

### Skeleton for a new example

```python
import faultycat as fc

# --- target config (edit me) ---
SIM = True                    # False to run on a real board
DELAY = (0, 200, 5)
WIDTH = (1, 40, 1)

cat = fc.connect(simulator=SIM)
camp = cat.campaign("emfi").configure(delay=DELAY, width=WIDTH)
camp.run()
df = camp.results_df()
df["success"] = df["verify_status"] != 0     # your target's success test
fc.glitch_map(df)
cat.close()
```
