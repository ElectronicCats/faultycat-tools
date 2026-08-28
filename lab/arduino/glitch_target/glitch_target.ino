// FaultyCat glitch target — Arduino Uno (ATmega328P)
// -----------------------------------------------------------------------------
// A fault-injection target that makes both outcomes visible over serial:
//
//   * RESET  -> prints "BOOT #n" on every power-up/reset (n counted in EEPROM),
//               so a crowbar brownout that resets the MCU shows a new BOOT line.
//   * FAULT  -> on the command byte 0xAA it runs a double loop that should
//               count to exactly 10000, pulsing D7 HIGH as a TRIGGER, and
//               reports the 4-byte little-endian result. A glitch that corrupts
//               the count returns something != 10000.
//
// Wiring (see arduino/README.md):
//   Crowbar OUTPUT -> Arduino 5V / VCC rail   (voltage-glitch the power -> reset)
//   D7             -> FaultyCat trigger input  (for the ext-trigger fault path)
//   TX/RX          -> FaultyCat Target UART, or just read D0/D1 over the Uno USB
//   GND            -> common (shared via USB here)

#include <EEPROM.h>

#define TRIG_BIT (1 << 7)   // D7

void setup() {
  Serial.begin(115200);
  DDRD |= TRIG_BIT;
  PORTD &= ~TRIG_BIT;
  uint8_t boots = EEPROM.read(0) + 1;   // survives a reset; increments each boot
  EEPROM.write(0, boots);
  Serial.print(F("BOOT #"));
  Serial.println(boots);
}

void loop() {
  if (Serial.available() && (uint8_t)Serial.read() == 0xAA) {
    volatile uint32_t cnt = 0;
    PORTD |= TRIG_BIT;                         // trigger high — glitch window
    for (volatile uint16_t i = 0; i < 100; i++)
      for (volatile uint16_t j = 0; j < 100; j++)
        cnt++;                                 // 10000 when un-glitched
    PORTD &= ~TRIG_BIT;
    Serial.write((const uint8_t*)&cnt, 4);     // 10000 = 10 27 00 00
  }
}
