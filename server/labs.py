"""Lab definitions for Ohmni.

Each step has an instruction and check(v, hist) -> (status, note).
  v    = [P1, P2, P3, P4] in millivolts (latest reading)
  hist = recent readings, oldest first (cleared on every step change)
  status is "ok", "fault", or "waiting"; note is a short plain-English diagnosis.

The math lives here, not in the LLM. Gemma gets the note and explains it.
The ESP32 ADC saturates around 3.1 V, so the 3.3 V rail reads about 3.1 V.
"""
import statistics

SUPPLY = 3300  # mV, ESP32 3V3 pin
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
        return "waiting", "P1 reads 0 V. Wire ESP32 3V3 to the + rail and put P1 on the + rail."
    return "fault", f"P1 reads {fmt(p1)}, expected 3.3 V. Loose 3V3 jumper, or P1 is in the wrong row."


RAIL_STEP = {
    "instruction": "Wire the ESP32 3V3 pin to the breadboard + rail and GND to the - rail. "
                   "Put probe P1 on the + rail. Park unused probe wires in the GND rail.",
    "check": rail_check,
}


def lost_rail(v):
    if v[0] < RAIL:
        return "fault", f"P1 dropped to {fmt(v[0])}. The + rail lost power: check the 3V3 jumper first."
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


LABS = {
    "divider": {
        "title": "Voltage Divider",
        "goal": "Build a two-resistor voltage divider and predict its output.",
        "probes": {"P1": "+ rail", "P2": "midpoint"},
        "steps": [
            RAIL_STEP,
            {"instruction": "Place R1 (10k) from the + rail to an empty row. Place R2 (10k) from that row to GND. "
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
            {"instruction": "Place a 220 ohm resistor from the + rail to an empty row. Put a red LED from that row "
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
            {"instruction": "Place a pushbutton across the center gap. Wire one leg to the + rail. Connect the "
                            "opposite leg's row to GND through a 10k resistor (the pull-down). Put P2 on that row. "
                            "Don't press yet.",
             "check": button_released},
            {"instruction": "Now press and hold the button.",
             "check": button_pressed},
        ],
    },
}
