// Ohmni probe firmware for a classic ESP32 DevKit (ESP32-WROOM), Arduino core 2.x or 3.x.
// Streams 4 probe voltages + the bench push-to-talk button as JSON lines over USB serial.
// Listens for one-char LED commands from the laptop: g = green, r = red, y = thinking (blink), 0 = off.
//
// Wiring (see README):
//   Probes P1..P4  -> GPIO 34, 35, 32, 33  (ADC1 pins, input only is fine)
//   PTT button     -> GPIO 27 to GND (internal pull-up, pressed = LOW)
//   Green LED      -> GPIO 25 -> 220 ohm -> LED -> GND
//   Red LED        -> GPIO 26 -> 220 ohm -> LED -> GND
//   Lab circuit is powered from the ESP32 3V3 and GND pins.
// If you have an ESP32-S3 or C3, change PROBE_PINS to ADC1 pins on that chip (S3: 1-10, C3: 0-4).

const int PROBE_PINS[4] = {34, 35, 32, 33};
const int PTT_PIN = 27;
const int LED_GREEN = 25;
const int LED_RED = 26;
const int SAMPLES = 16;               // average to kill ADC noise
const unsigned long PERIOD_MS = 100;  // 10 readings per second

char ledMode = '0';
unsigned long lastSend = 0;

void setup() {
  Serial.begin(115200);
  pinMode(PTT_PIN, INPUT_PULLUP);
  pinMode(LED_GREEN, OUTPUT);
  pinMode(LED_RED, OUTPUT);
  analogReadResolution(12);
  // Default attenuation is 11 dB: usable range roughly 0.1 V to 3.1 V.
}

int readMv(int pin) {
  long sum = 0;
  for (int i = 0; i < SAMPLES; i++) sum += analogReadMilliVolts(pin);
  return (int)(sum / SAMPLES);
}

void updateLeds() {
  bool blink = (millis() / 250) % 2;
  switch (ledMode) {
    case 'g': digitalWrite(LED_GREEN, HIGH); digitalWrite(LED_RED, LOW); break;
    case 'r': digitalWrite(LED_GREEN, LOW);  digitalWrite(LED_RED, HIGH); break;
    case 'y': digitalWrite(LED_GREEN, blink); digitalWrite(LED_RED, blink); break;
    default:  digitalWrite(LED_GREEN, LOW);  digitalWrite(LED_RED, LOW);
  }
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 'g' || c == 'r' || c == 'y' || c == '0') ledMode = c;
  }
  updateLeds();

  unsigned long now = millis();
  if (now - lastSend >= PERIOD_MS) {
    lastSend = now;
    Serial.print("{\"t\":");
    Serial.print(now);
    Serial.print(",\"v\":[");
    for (int i = 0; i < 4; i++) {
      if (i) Serial.print(',');
      Serial.print(readMv(PROBE_PINS[i]));
    }
    Serial.print("],\"ptt\":");
    Serial.print(digitalRead(PTT_PIN) == LOW ? 1 : 0);
    Serial.println("}");
  }
}
