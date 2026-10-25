"""Lab definitions for Ohmni.

Each step has an instruction and check(v, hist) -> (status, note).
  v    = [P1, P2, P3, P4] in millivolts (latest reading)
  hist = recent readings, oldest first (cleared on every step change)
  status is "ok", "fault", or "waiting"; note is a short plain-English diagnosis.

The math lives here, not in the LLM. Gemma gets the note and explains it.
Hardware: the student circuit is always powered from the target board's 3V3 pin. The Arduino Uno
instrument reads 0 to 5 V directly on A0-A3 (10-bit ADC, about 5 mV per step), so the 3.3 V rail reads
about 3.3 V. RAIL leaves margin for a sagging regulator and the Uno's own 5 V reference tolerance.
Both boards must share GND or no reading means anything.
"""
import statistics

SUPPLY = 3300  # mV, target board 3V3 pin
HIST_LEN = 40  # readings kept per step (4 s at 10 Hz), enough to see a 1 Hz blink toggle
RAIL = 2800    # at or above this = sitting on the + rail
LOW = 80       # at or below this = basically ground


def spread(hist, i):
    vals = [h[i] for h in hist]
    return statistics.pstdev(vals) if len(vals) > 3 else 0.0


def fmt(mv):
    return f"{mv / 1000:.2f} V"


# ---------- shared step ----------

def rail_check(v, hist):
    p1 = v[0]
    if p1 >= RAIL:
        return "ok", "P1 sees the 3.3 V rail."
    if p1 <= LOW:
        return "waiting", "P1 reads 0 V. Wire the target board 3V3 pin to the + rail and put P1 on the + rail."
    return "fault", (f"P1 reads {fmt(p1)}, expected 3.3 V. Loose target board 3V3 jumper, P1 in the wrong row, "
                     "or the Uno and target board do not share GND.")


RAIL_STEP = {
    "instruction": "Wire the target board 3V3 pin to the breadboard + rail and the target board GND to the - rail. "
                   "Connect the Uno instrument GND to the same - rail so both boards share ground. "
                   "Put probe P1 on the + rail. Park unused probe wires in the GND rail.",
    "check": rail_check,
}


def lost_rail(v):
    if v[0] < RAIL:
        return "fault", f"P1 dropped to {fmt(v[0])}. The + rail lost power: check the target board 3V3 jumper first."
    return None


# ---------- lab 1: voltage divider ----------

def divider_check(r1, r2):
    expected = SUPPLY * r2 / (r1 + r2)
    tol = max(expected * 0.12, 100)

    def check(v, hist):
        bad = lost_rail(v)
        if bad:
            return bad
        p2 = v[1]
        if abs(p2 - expected) <= tol:
            return "ok", f"Midpoint {fmt(p2)}, expected {fmt(expected)}."
        if p2 >= RAIL:
            return "fault", ("Midpoint reads the full rail. The bottom resistor R2 is not reaching GND, "
                             "or P2 is sitting on the + rail.")
        if p2 <= LOW:
            return "fault", ("Midpoint reads 0 V. The top resistor R1 is not reaching the + rail, "
                             "or the midpoint row is shorted to GND.")
        ratio = p2 / max(SUPPLY - p2, 1)
        return "fault", (f"Midpoint {fmt(p2)}, expected {fmt(expected)}. That implies R2/R1 of about "
                         f"{ratio:.2f} instead of {r2 / r1:.2f}, so a resistor value is probably wrong. "
                         "Color bands: 10k = brown black orange, 1k = brown black red.")
    return check


# ---------- lab 2: LED + resistor ----------

def led_check(r_ohm):
    def check(v, hist):
        bad = lost_rail(v)
        if bad:
            return bad
        p2 = v[1]
        if 1500 <= p2 <= 2600:
            ma = (SUPPLY - p2) / r_ohm
            return "ok", f"LED forward voltage {fmt(p2)}, current about {ma:.1f} mA."
        if p2 >= RAIL:
            return "fault", ("No voltage drop across the resistor, so no current flows. The LED is probably "
                             "backwards (short leg goes to GND), not seated, or its GND leg is not in the GND rail.")
        if p2 <= LOW:
            return "fault", ("Junction reads 0 V. The LED is shorted (both legs in the same row?) "
                             "or the junction row is wired to GND.")
        return "fault", (f"Junction reads {fmt(p2)}, unusual for a red LED. Is it a blue or white LED, "
                         "or a much larger resistor than 220 ohm?")
    return check


def led_reversed_check(v, hist):
    bad = lost_rail(v)
    if bad:
        return bad
    p2 = v[1]
    if p2 >= RAIL:
        return "ok", "LED is reversed and no current flows, which is exactly what this step should show."
    return "waiting", f"Junction still reads {fmt(p2)}, so the LED is still conducting. Flip it around."


# ---------- lab 3: button with pull-down ----------

def button_released(v, hist):
    bad = lost_rail(v)
    if bad:
        return bad
    p2, s = v[1], spread(hist, 1)
    if s > 150 or (LOW < p2 < RAIL - 300 and s > 50):
        return "fault", (f"P2 is jumping around ({s:.0f} mV of noise), so the input is floating. "
                         "The 10k pull-down is missing or not reaching GND.")
    if p2 <= 200:
        return "ok", "Reads 0 V while released, so the pull-down works."
    if p2 >= RAIL:
        return "fault", ("Reads 3.3 V without pressing. The button is probably rotated 90 degrees: legs on the "
                         "same side are always connected. Rotate it so it straddles the center gap.")
    return "fault", f"Reads {fmt(p2)} while released, expected 0 V. Check the pull-down resistor."


def button_pressed(v, hist):
    bad = lost_rail(v)
    if bad:
        return bad
    p2 = v[1]
    if p2 >= RAIL:
        return "ok", "Reads 3.3 V while pressed. The button works."
    if p2 <= 200:
        return "waiting", ("Still 0 V. Press and hold. If you are already holding it, the button legs are on "
                           "the wrong rows: rotate it 90 degrees.")
    return "fault", f"Reads {fmt(p2)} while pressed, expected 3.3 V. Bad contact, or the pull-down is far below 10k."


# ---------- coding labs: the target board runs the student's sketch ----------

HIGH_MV = 2000     # a GPIO driven HIGH by the 3.3 V target reads about 3.3 V
LOW_MV = 800       # driven LOW reads about 0 V
MIN_SAMPLES = 20   # 2 s of history before judging a running sketch


def level(mv):
    return "high" if mv >= HIGH_MV else "low" if mv <= LOW_MV else "mid"


def transitions(vals):
    """Count clean HIGH/LOW flips, skipping in-between samples."""
    last, n = None, 0
    for x in vals:
        lv = level(x)
        if lv == "mid":
            continue
        if last is not None and lv != last:
            n += 1
        last = lv
    return n


def blink_check(v, hist):
    bad = lost_rail(v)
    if bad:
        return bad
    p2 = [h[1] for h in hist]
    if len(p2) < MIN_SAMPLES:
        return "waiting", "Watching the LED pin for a couple of seconds."
    flips = transitions(p2)
    if flips >= 2:
        return "ok", (f"P2 toggles between {fmt(min(p2))} and {fmt(max(p2))}: {flips} flips in "
                      f"{len(p2) / 10:.0f} s. The LED is blinking.")
    levels = {level(x) for x in p2}
    if levels == {"low"}:
        return "fault", ("P2 on the LED pin stays at 0 V, so the sketch is not driving this pin. Check that the pin "
                         "number in the code matches the GPIO the wire goes to, that pinMode makes it an OUTPUT, "
                         "and that the upload finished.")
    if levels == {"high"}:
        return "fault", (f"P2 stays HIGH at {fmt(max(p2))} and never goes LOW. Is there a digitalWrite(..., LOW) "
                         "with a delay after it? Or the P2 row is on the 3V3 rail instead of the GPIO.")
    if flips == 1:
        return "waiting", "Saw one change on P2. Waiting for it to toggle again."
    return "fault", (f"P2 sits around {fmt(statistics.median(p2))}, neither HIGH nor LOW. The pin may only be weakly "
                     "pulled (pinMode OUTPUT missing), it toggles faster than the meter can see (use delays of "
                     "200 ms or more), or P2 is on the LED side of the resistor instead of the GPIO row.")


def button_led_check(v, hist):
    bad = lost_rail(v)
    if bad:
        return bad
    if len(hist) < MIN_SAMPLES:
        return "waiting", "Press and release the button a few times while Ohmni watches P2 and P3."
    btn = [level(h[1]) for h in hist]
    led = [level(h[2]) for h in hist]
    if spread(hist, 1) > 150 and "mid" in btn:
        return "fault", (f"The button input P2 is jumping around ({spread(hist, 1):.0f} mV of noise), so it is "
                         "floating. The 10k pull-down from the GPIO 4 row to GND is missing or loose.")
    pressed = [l for b, l in zip(btn, led) if b == "high"]
    released = [l for b, l in zip(btn, led) if b == "low"]
    if not pressed:
        if released and all(l == "high" for l in released):
            return "fault", ("The LED pin P3 is HIGH even though the button is not pressed. The sketch turns the LED "
                             "on without reading the button, or the logic is inverted.")
        return "waiting", "P2 has stayed LOW. Press and hold the button for a second, then let go."
    p_low, p_high = all(l == "low" for l in pressed), all(l == "high" for l in pressed)
    r_low = bool(released) and all(l == "low" for l in released)
    r_high = bool(released) and all(l == "high" for l in released)
    if p_low and not r_high:
        return "fault", ("The button press reaches P2 (3.3 V), but the LED pin P3 stays at 0 V. Check that the sketch "
                         "reads BUTTON_PIN with digitalRead, writes LED_PIN, makes LED_PIN an OUTPUT, and that "
                         "LED_PIN matches the GPIO wired to the LED.")
    if p_high and r_high:
        return "fault", ("P3 stays HIGH whether or not the button is pressed. The sketch never turns the LED off, "
                         "or the P3 row is on the 3V3 rail.")
    if p_low and r_high:
        return "fault", ("The LED is on when the button is released and off when it is pressed: the logic is "
                         "inverted. Look at the if condition in loop().")
    mism = sum(1 for i in range(len(btn)) if btn[i] != "mid" and led[i] != "mid"
               and led[i] != btn[i] and (i == 0 or led[i] != btn[i - 1]))
    if mism > 0.2 * len(btn):
        return "fault", (f"The LED only sometimes follows the button ({mism} of {len(btn)} readings disagree). "
                         "A long delay in loop(), or a loose LED wire.")
    if not r_low:
        return "waiting", "The LED lights while pressed. Now let go of the button."
    return "ok", "P3 follows the button on P2: the LED lights while pressed and turns off when released."


BLINK_STARTER = """// Blink and measure: blink an LED on the target board and watch probe P2 see it.
const int LED_PIN = 13;  // the GPIO wired to the 220 ohm resistor and LED

void setup() {
  Serial.begin(115200);
  // TODO: set LED_PIN as an OUTPUT with pinMode
}

void loop() {
  // TODO: turn the LED on (HIGH), wait 500 ms, turn it off (LOW), wait 500 ms
  Serial.println("tick");
  delay(1000);
}
"""

BUTTON_STARTER = """// Button to LED: light the LED while the button is pressed.
const int BUTTON_PIN = 4;  // button input, held LOW by the 10k pull-down
const int LED_PIN = 13;    // LED through a 220 ohm resistor

void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_PIN, INPUT);
  // TODO: set LED_PIN as an OUTPUT
}

void loop() {
  int pressed = LOW;  // TODO: read BUTTON_PIN with digitalRead
  // TODO: write the LED HIGH while pressed, LOW otherwise
  Serial.print("button: ");
  Serial.println(pressed);
  delay(20);
}
"""


LABS = {
    "divider": {
        "title": "Voltage Divider",
        "goal": "Build a two-resistor voltage divider and predict its output.",
        "probes": {"P1": "+ rail", "P2": "midpoint"},
        "steps": [
            RAIL_STEP,
            {"instruction": "Place R1 (10k) from the + rail (target board 3V3) to an empty row. Place R2 (10k) from that row to GND. "
                            "Move probe P2 to the shared row (the midpoint).",
             "check": divider_check(10_000, 10_000)},
            {"instruction": "Swap R2 for a 1k resistor. Before you look at the meter, say your prediction out loud.",
             "check": divider_check(10_000, 1_000)},
        ],
    },
    "led": {
        "title": "LED + Resistor",
        "goal": "Light an LED safely and understand forward voltage and current limiting.",
        "probes": {"P1": "+ rail", "P2": "resistor/LED junction"},
        "steps": [
            RAIL_STEP,
            {"instruction": "Place a 220 ohm resistor from the + rail (target board 3V3) to an empty row. Put a red LED from that row "
                            "to GND: long leg in the resistor's row, short leg (flat side) in GND. Move P2 to the "
                            "resistor/LED row.",
             "check": led_check(220)},
            {"instruction": "Pull the LED out, flip it around, and put it back. Watch what happens, then explain why.",
             "check": led_reversed_check},
        ],
    },
    "button": {
        "title": "Button + Pull-down",
        "goal": "Read a pushbutton reliably and see why a pull-down resistor matters.",
        "probes": {"P1": "+ rail", "P2": "input node"},
        "steps": [
            RAIL_STEP,
            {"instruction": "Place a pushbutton across the center gap. Wire one leg to the + rail (target board 3V3). Connect the "
                            "opposite leg's row to GND through a 10k resistor (the pull-down). Put P2 on that row. "
                            "Don't press yet.",
             "check": button_released},
            {"instruction": "Now press and hold the button.",
             "check": button_pressed},
        ],
    },
    "blink": {
        "title": "Blink and Measure",
        "goal": "Write a first sketch that blinks an LED from the target board, and watch the meter see it.",
        "probes": {"P1": "+ rail", "P2": "LED pin (GPIO 13 row)"},
        "steps": [
            RAIL_STEP,
            {"instruction": "Wire GPIO 13 of the target board to an empty row and put probe P2 on that row. From that "
                            "row, run a 220 ohm resistor to a second row, then a red LED from the second row to GND "
                            "(long leg toward the resistor). In the Code tab, finish the TODOs so the LED blinks once "
                            "a second, then Compile and Upload.",
             "check": blink_check, "starter": BLINK_STARTER},
        ],
    },
    "button_led": {
        "title": "Button to LED",
        "goal": "Read a button with a pull-down resistor and light an LED only while it is pressed.",
        "probes": {"P1": "+ rail", "P2": "button input (GPIO 4)", "P3": "LED pin (GPIO 13)"},
        "steps": [
            RAIL_STEP,
            {"instruction": "Put a pushbutton across the center gap. Wire one leg to the + rail (target board 3V3). "
                            "Wire the opposite leg's row to GPIO 4, and to GND through a 10k pull-down. Put P2 on "
                            "the GPIO 4 row. Keep the LED on GPIO 13 with its 220 ohm resistor and put P3 on the "
                            "GPIO 13 row. Finish the TODOs in the Code tab, upload, then press and release the "
                            "button a few times.",
             "check": button_led_check, "starter": BUTTON_STARTER},
        ],
    },
}
