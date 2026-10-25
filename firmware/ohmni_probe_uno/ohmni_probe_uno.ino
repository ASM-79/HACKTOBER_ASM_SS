// Ohmni instrument firmware for an Arduino Uno (board 1, the Ohmni station).
// Streams 4 probe voltages + the bench push-to-talk button as JSON lines over USB serial,
// using the same protocol as firmware/ohmni_probe (the ESP32 version):
//   {"t":12345,"v":[3301,1650,0,0],"ptt":0}
// Listens for one-char LED commands from the laptop: g = green, r = red, y = thinking (blink), 0 = off.
//
// Wiring (see README):
//   Probes P1..P4  -> A0, A1, A2, A3 (read 0 to 5 V directly, no dividers)
//   PTT button     -> D2 to GND (internal pull-up, pressed = LOW)
//   Green LED      -> D5 -> 220 ohm -> LED -> GND
//   Red LED        -> D6 -> 220 ohm -> LED -> GND
//   Uno GND        -> breadboard - rail (shared with the target board GND, required)
// The Uno never powers the student circuit. The target board's 3V3 pin does.

const int PROBE_PINS[4] = {A0, A1, A2, A3};
const int PTT_PIN = 2;
const int LED_GREEN = 5;
const int LED_RED = 6;
const int SAMPLES = 16;               // average to kill ADC noise
const unsigned long PERIOD_MS = 100;  // 10 readings per second
const long VREF_MV = 5000;            // measure your Uno's 5V pin and put the real value here for best accuracy

char ledMode = '0';
unsigned long lastSend = 0;

void setup() {
  Serial.begin(115200);
  pinMode(PTT_PIN, INPUT_PULLUP);
  pinMode(LED_GREEN, OUTPUT);
  pinMode(LED_RED, OUTPUT);
}

int readMv(int pin) {
  long sum = 0;
  for (int i = 0; i < SAMPLES; i++) sum += analogRead(pin);
  return (int)((sum / SAMPLES) * VREF_MV / 1023);
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
