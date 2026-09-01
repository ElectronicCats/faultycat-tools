# Arduino Uno as a FaultyCat glitch target

`glitch_target/glitch_target.ino` turns an Arduino Uno into a fault-injection
target (like the CC26x2R1 in SimpleLink-FI notebook 5): it counts to 10000 in
a double loop, raising **D7** as a trigger, and reports the 4-byte result over
serial. FaultyCat glitches during the loop and reads the result back to
classify **normal / fault / crash**. Drive it from
`arduino/attack_arduino.ipynb`.

Flash the sketch with the Arduino IDE (or `arduino-cli`), then wire it up.

## Wiring

⚠️ **The Uno is 5 V, FaultyCat is 3.3 V.** Every signal between them needs
level handling. Common **GND** everywhere (Arduino, FaultyCat, scope, supply).

| Signal | Arduino | FaultyCat | Level note |
| --- | --- | --- | --- |
| **Trigger** | D7 (out, 5 V) | trigger input **GP8** | 5 V → 3.3 V: tie GP8's `TRIGGER_VREF` to the Arduino 5 V, or a divider (e.g. 1.8 k / 3.3 k) |
| **Target UART RX** | TX / D1 (5 V) | **CH1** (RX) | via the TXS0108E — set the scanner-header target-side VCC to **5 V** |
| **Target UART TX** | RX / D0 | **CH0** (TX) | 3.3 V into the Uno RX reads HIGH (Vih≈3.0 V), OK |
| **Glitch (crowbar)** | 5V / VCC rail | crowbar output (**LP** or **HP**) | shorts VCC — see below |
| **GND** | GND | GND | shared |

### Glitch injection (crowbar — recommended for the Uno)

Connect the crowbar output to the Uno's **VCC rail** (or a point close to the
ATmega328P power pin). Firing briefly shorts VCC → a voltage glitch. For a
sharper glitch you may need to **remove/bypass the board's decoupling caps**
near the MCU (they smooth out the dip).

EMFI (coil over the chip) also works but is harder on a big through-hole 5 V
part; crowbar VCC glitching is the more practical route here.

### Scope (verify the attack timing)

- **CH1 → D7** (the trigger): the scope triggers here; you see the glitch
  window (the whole loop).
- **CH2 → Arduino VCC / crowbar output**: see the glitch dip and *when* it
  lands inside the loop (the `delay_us` you swept).

## Honest expectation

The ATmega328P at 5 V is robust — clean, repeatable faults are **hard** and may
need decap removal, a tuned `delay`/`width` sweep, and patience (or you mostly
get resets = "crash"). Even so, this setup validates FaultyCat's **full attack
pipeline** end to end: trigger → glitch → observe the target's serial → classify.
