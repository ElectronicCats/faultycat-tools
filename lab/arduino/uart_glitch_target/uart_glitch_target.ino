// FaultyCat UART glitch target — Arduino Uno (ATmega328P)
// -----------------------------------------------------------------------------
// Closes the ChipWhisperer attack->observe loop *over FaultyCat's own UART*:
// FaultyCat sends a command, the Uno counts (a wide, glitchable window), and
// replies with the count THROUGH FaultyCat's target UART. The notebook judges
// the reported count — anything but the expected value means the glitch landed.
//
//   FaultyCat UART is on GP0(TX)/GP1(RX) via a 3.3 V level shifter, so we talk
//   to it on a SoftwareSerial port (D2=RX, D3=TX). The 5 V hardware UART (USB)
//   is kept for flashing + a standalone bench path (send 0xAA over USB too).
//
// Protocol @ 9600 on the link (and 115200 on USB, for standalone testing):
//   RX 0xAA  -> raise D7 (optional ext-trigger), count to 10000 slowly,
//               then reply "<count>\n"  (10000 when un-glitched).
//   on boot  -> reply "BOOT #<n>\n"     (EEPROM counter -> a reset shows up).
//
// Wiring (see arduino/README.md):
//   Crowbar OUTPUT     -> Arduino 5V rail      (voltage-glitch the power)
//   FaultyCat GP0 (TX) -> Arduino D2  (RX)     3.3 V, reads HIGH on the Uno
//   Arduino  D3  (TX)  -> 1k -> FaultyCat GP1  (protect the 3.3 V shifter)
//   D7                 -> FaultyCat GP8 trigger (OPTIONAL, precise timing)
//   GND                -> common (shared via USB; add a wire for margin)

#include <EEPROM.h>
#include <SoftwareSerial.h>

#define TRIG_BIT (1 << 7)   // D7
SoftwareSerial link(2, 3);  // RX=D2, TX=D3  (to FaultyCat GP0/GP1)

uint32_t run_count() {
  volatile uint32_t cnt = 0;
  PORTD |= TRIG_BIT;                     // trigger high — glitch window open
  for (volatile uint16_t i = 0; i < 10000; i++) {
    cnt++;                              // == 10000 if never glitched
    delayMicroseconds(3);              // widen the window (~35 ms total)
  }
  PORTD &= ~TRIG_BIT;
  return cnt;
}

void setup() {
  DDRD |= TRIG_BIT;
  PORTD &= ~TRIG_BIT;
  link.begin(9600);
  Serial.begin(115200);                 // USB: debug + standalone 0xAA path
  uint8_t boots = EEPROM.read(0) + 1;   // survives a brownout reset
  EEPROM.write(0, boots);
  link.print(F("BOOT #"));   link.println(boots);
  Serial.print(F("BOOT #")); Serial.println(boots);
}

void loop() {
  if (link.available() && (uint8_t)link.read() == 0xAA) {
    uint32_t c = run_count();
    link.println(c);                    // report over FaultyCat's UART
    Serial.print(F("[link] ")); Serial.println(c);
  }
  if (Serial.available() && (uint8_t)Serial.read() == 0xAA) {
    Serial.println(run_count());        // standalone bench path over USB
  }
}
