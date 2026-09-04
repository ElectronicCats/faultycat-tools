# FaultyCat as a single-device ChipWhisperer (for fault injection)

FaultyCat is a PicoEMP improvement that also folds in the parts of a
ChipWhisperer/Husky a **fault-injection** workflow needs — trigger, delay,
pulse, target UART, reset, capture — into **one** self-contained device. So
you can write CW-style FI notebooks against `faultycat` alone, no separate
scope + injector.

This is the CW → `faultycat` correspondence for the fault-injection surface
those notebooks use.

## API mapping

| ChipWhisperer (+ external injector) | `faultycat` (one device) |
| --- | --- |
| `import chipwhisperer as cw` | `import faultycat as fc` |
| `scope = cw.scope()` | `cat = fc.connect()` |
| `target = cw.target(scope, SimpleSerial)` | `cat.uart` — `open()` / `write()` / `read_until()` / `reset_input()` |
| `scope.glitch.ext_offset` | `cat.emfi.delay_us` · `cat.crowbar.delay_us` |
| `scope.glitch.width` | `cat.emfi.width_us` · `cat.crowbar.width_ns` |
| `scope.glitch.trigger_src = 'ext_single'` | `cat.emfi.trigger = 'ext_rising'` (also falling / pulse / immediate) |
| `scope.io.nrst` pulse / reset target | `cat.target_reset(gp, ms)` |
| `scope.arm()` (+ HV charge) | `cat.emfi.arm()` |
| `wait_for_hv()` | `cat.emfi.wait_for_charged()` |
| glitch fires on the trigger | `cat.emfi.fire()` · one-call `cat.emfi.glitch()` |
| `scope.capture()` / `get_last_trace()` | `cat.emfi.capture()` → ADC trace (EMFI only) |
| `cw.plot(trace)` | `fc.plot_trace(trace)` |
| voltage glitching (`scope.glitch.output`) | `cat.crowbar` (`output='lp'`/`'hp'`) |
| `glitch.GlitchController(groups, parameters)` | `fc.GlitchController(parameters, groups)` |
| `gc.set_range` · `gc.glitch_values()` · `gc.add()` | same names |
| `gc.results` / plotting the map | `gc.results_df()` · `gc.counts()` · `gc.plot(x=, y=)` |
| logic/protocol probing (external) | `cat.scanner` (SWD/I2C brute-force, logic analyzer) |

## Target UART

Bidirectional over the scanner header — **CH0 (GP0) = TX, CH1 (GP1) = RX**,
a hardware UART0. `cat.uart`: `open(baud=…)` / `write()` / `read_until()` /
`set_baud()` / `set_parity()` / `set_stop_bits()`. The wire baud is set
on-device; the host CDC line-coding stays fixed (do not open a FaultyCat
CDC at 1200 baud — that is the RP2040 BOOTSEL magic touch).

**Confirmed baud range** (loopback-free, verified against an FT232R peer):
clean round-trip **300 baud … 3 Mbaud** — 3 Mbaud is the FT232R's ceiling,
not FaultyCat's (the RP2040 UART reaches ~7.8 Mbaud; the TXS0108E level
shifter, ≥60 Mbps push-pull, is not the limit). Practical min ~119 baud
(PL011 divisor).

## The CW loop, on FaultyCat

```python
import faultycat as fc
cat = fc.connect()
cat.uart.open()

def classify(ret):                       # YOUR success condition (cf. gc.add)
    if ret == EXPECTED: return "normal"
    if not ret: return "crash"
    return "success"

gc = fc.GlitchController(["delay", "width"], groups=["success", "crash", "normal"])
gc.set_range("delay", range(0, 500, 20)).set_range("width", [10] * REPEATS)

cat.emfi.trigger = "ext_rising"
for p in gc.glitch_values():
    cat.emfi.delay_us, cat.emfi.width_us = p["delay"], p["width"]
    cat.target_reset(RST_GP)             # scope.io.nrst
    cat.emfi.arm(); cat.emfi.wait_for_charged()   # scope.arm() + wait_for_hv()
    cat.uart.reset_input()
    cat.emfi.fire()                      # arms trigger wait
    cat.uart.write(CMD)                  # target runs -> raises trigger -> pulse
    gc.add(classify(cat.uart.read_until(b"\n", timeout=0.1)))

gc.counts(); gc.plot(x="delay", y="width")
```
