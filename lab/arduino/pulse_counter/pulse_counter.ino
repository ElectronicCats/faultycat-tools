// FaultyCat pulse counter — Arduino Uno
// -----------------------------------------------------------------------------
// Verifies that a glitch burst really produces the pulse count it claims.
// Timer1 counts external edges on its T1 input = pin D5, in hardware (no ISR,
// no missed edges). Use it to confirm repeat == N pulses.
//
// Probe point: the crowbar GLITCH OUTPUT (the shared LP/HP MOSFET drain). D5's
// internal pull-up idles the line HIGH (~5 V); each glitch shorts it to GND, a
// sharp FALLING edge (MOSFET-driven) — so we count falling edges. One glitch =
// one falling edge; repeat=N must read N.
//
// Serial protocol (115200):  'z' -> zero the counter (replies "0");  'r' -> count.
//
// Wiring: FaultyCat crowbar OUTPUT -> D5, common GND (shared via USB here).
//   If counts read 0, the line isn't swinging — add an external pull-up
//   (~1 kOhm from the output to 5 V) so the short makes a clean dip.

void setup() {
  Serial.begin(115200);
  pinMode(5, INPUT_PULLUP);          // T1 input, idle HIGH; glitch pulls it LOW
  TCCR1A = 0;
  TCCR1B = (1 << CS12) | (1 << CS11); // external clock on T1, FALLING edge
  TCNT1 = 0;
}

void loop() {
  if (Serial.available()) {
    char c = Serial.read();
    if (c == 'z') {
      TCNT1 = 0;
      Serial.println(0);
    } else if (c == 'r') {
      Serial.println((unsigned)TCNT1);
    }
  }
}
