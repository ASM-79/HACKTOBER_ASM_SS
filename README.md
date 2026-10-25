# Ohmni: a TA on every lab bench

Intro electronics and embedded labs have 30 students and one TA. Ohmni sits at every bench: it **measures** the circuit through an Arduino Uno instrument, **sees** the breadboard through a camera, **hears** the student through a push-to-talk button, and **hosts the student's coding environment** for the board they are programming. Then it explains what is wrong like a TA would, using the code, the compiler errors, the serial output, the live voltages and the photo together. It runs on **Gemma 4**, an open-weight Apache 2.0 model, locally on a laptop through Ollama.

**Tracks:** Best Use of Gemma, Best Open-Source AI Project.

## How it works

```
breadboard ── probe wires ──> Uno instrument (A0-A3, 10 Hz JSON) ──USB──> server (labs.py checker)
     │                                                                      │
     └── powered by ── ESP32 target (student sketch) <──USB── compile/upload/serial (arduino-cli)
camera + mic (browser) ──── photo + question + sketch + errors + serial ───> Gemma 4 (Ollama)
                                                                            │
                          spoken answer (browser TTS) <─────────────────────┘
```

- **The Uno is the multimeter.** It reads 4 nodes from 0 to 5 V on A0 to A3. It never powers the student circuit.
- **The ESP32 is the student's project.** The circuit runs from its 3V3 pin, and the student writes, compiles and uploads its sketch from the Code tab.
- **The rule checker does the math** (`server/labs.py`): expected voltages, fault patterns, implied resistor ratios, blink detection, button tracking. LLMs are bad at arithmetic, so we don't ask Gemma to do it.
- **Gemma does the TA part:** it gets the photo, the readings, the checker's diagnosis, the sketch, the last compile result and the last 40 lines of serial output, then points at the problem and asks a guiding question. Example: "your code drives pin 13, but P2 on the LED row reads 0 V, and in the photo the LED wire goes to pin 12."
- **It speaks up on its own:** if a working step breaks, the red LED lights within 1.5 s and Ohmni explains what happened. After a failed compile it gives a one-sentence hint, not the fix, unless the student asks for the answer.

## Two-board wiring

Both boards plug into the laptop over USB. **The Uno GND and the ESP32 GND must both go to the breadboard - rail.** Without a shared ground no reading means anything.

**Board 1: Uno instrument** (flashed with `firmware/ohmni_probe_uno`)

| What | Uno pin | Notes |
|---|---|---|
| Probe P1 | A0 | Usually on the + rail |
| Probe P2 | A1 | The node being measured |
| Probe P3 | A2 | Second node (LED pin in Button to LED) |
| Probe P4 | A3 | Spare |
| Push-to-talk button | D2 to GND | Internal pull-up, no resistor needed |
| Green LED | D5, 220 ohm, LED, GND | Step OK |
| Red LED | D6, 220 ohm, LED, GND | Fault. Both blinking = thinking |
| Ground | GND to breadboard - rail | **Shared with the ESP32** |

**Board 2: ESP32 target** (DevKit, FQBN `esp32:esp32:esp32`, programmed from the Code tab)

| What | ESP32 pin | Notes |
|---|---|---|
| Lab power | 3V3 to + rail | Powers every lab circuit |
| Ground | GND to - rail | **Shared with the Uno** |
| LED (coding labs) | GPIO 13, 220 ohm, LED, GND | Probe the GPIO 13 row |
| Button input (Button to LED) | GPIO 4, 10k pull-down to GND | Button from + rail to the GPIO 4 row |

Park unused probe wires in the GND rail so they don't float. The Uno reads 3.3 V correctly, so no dividers are needed. The Uno firmware streams at 57600 baud, because CH340 clone Unos garble 115200 on macOS; the server tries 57600 and then 115200 automatically. The Uno measures against its own 5V pin, which is often about 4.6 V on USB power: set `VREF_MV` in the firmware to your board's real 5V (we calibrated ours to 4630 against the 3.3 V rail) or every reading is a few percent high. If you only have ESP32s, `firmware/ohmni_probe` turns an ESP32 into the instrument instead (GPIO 34, 35, 32, 33; its ADC tops out near 3.1 V). The server finds the instrument by its JSON stream, not by USB name, so either works.

## Setup

1. **Start the model download first** (about 10 GB): install Ollama 0.22 or newer, then `ollama pull gemma4:e4b`. On an 8 GB laptop use `gemma4:e2b` and run with `OLLAMA_MODEL=gemma4:e2b`.
2. **Install arduino-cli and the two cores:**
   ```
   brew install arduino-cli                      # Linux: curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh
   arduino-cli core update-index
   arduino-cli core install arduino:avr esp32:esp32
   softwareupdate --install-rosetta --agree-to-license   # Apple Silicon only: Arduino's ctags tool is an Intel binary
   ```
   If something is missing, the Code tab shows a card with the exact command to run.
3. **Flash the Uno instrument** (find the port with `arduino-cli board list`):
   ```
   arduino-cli compile --fqbn arduino:avr:uno firmware/ohmni_probe_uno
   arduino-cli upload -p /dev/cu.usbmodemXXXX --fqbn arduino:avr:uno firmware/ohmni_probe_uno
   ```
4. **Run the server:**
   ```
   cd server
   pip install -r requirements.txt
   python app.py                  # real hardware: the Uno is found automatically
   SIM=1 python app.py            # no hardware: sliders fake the probes, uploads are simulated
   SIM=1 MOCK_MODEL=1 python app.py   # no hardware and no Ollama
   ```
   Optional: `INSTRUMENT_PORT=...` to skip auto-detect, `TARGET_PORT=...` to preselect the ESP32, `OLLAMA_URL=http://<ip>:11434` to share one model across laptops (run `OLLAMA_HOST=0.0.0.0 ollama serve` on the strong laptop).
5. **Open http://localhost:8000 in Chrome.** Voice recognition needs Chrome. Allow camera and mic access and pick the camera that points at the breadboard. A phone works well as a webcam (Continuity Camera on Mac, or DroidCam).
6. **Pick the target port** in the Code tab. If only one other USB serial device is plugged in besides the Uno, Ohmni picks it for you. The port the Uno is on is greyed out.

## The Code tab

- Monaco editor with C++ highlighting (loaded from jsDelivr; falls back to a plain text box offline). Each lab step that needs code has starter code with TODOs, and your edits are saved in the browser.
- **Compile** runs `arduino-cli compile --fqbn <board>` in a per-session temp folder, streams the output, and marks errors on the editor lines. Click a problem to jump to it. Compiles are capped at 120 s. Student code is only compiled and uploaded to the target board, never run on the laptop.
- **Upload to target** compiles, releases the serial monitor, runs `arduino-cli upload -p <port>`, and reopens the monitor. In `SIM=1` nothing touches hardware.
- **Serial monitor** shows the target board output at 115200 baud by default (selectable).
- **Ask Ohmni** questions here or in the Guide tab; both send the current sketch along.

## Labs included

| Lab | Steps | Mistakes it catches |
|---|---|---|
| Voltage divider | Rail, 10k/10k divider, swap R2 for 1k and predict | R2 not reaching GND, R1 open, wrong resistor value (computes the implied ratio) |
| LED + resistor | Rail, light a red LED with 220 ohm, flip it | Backwards LED, LED shorted, unusual LED; reports forward voltage and current |
| Button + pull-down | Rail, button released, button pressed | Floating input (noise), button rotated 90 degrees, bad contact |
| Blink and Measure (code) | Rail, wire LED on GPIO 13 and write the blink sketch | Pin never driven (wrong pin, no pinMode, no upload), stuck HIGH, half-voltage (weak pull or blinking too fast) |
| Button to LED (code) | Rail, button on GPIO 4 with pull-down, LED on GPIO 13, write the sketch | Floating input, LED never lights, LED always on, inverted logic, LED only sometimes follows |

Every lab is powered from the target board 3V3. Adding a lab is a new entry in `LABS` in `labs.py`; give a step a `starter` sketch to make it a coding step.

## Tests and checks

```
pip install -r server/requirements-dev.txt && python -m playwright install chromium
python -m pytest server/tests -q          # unit, API, compile (fake arduino-cli), prompt, Playwright smoke test
python scripts/compile_starters.py        # every starter sketch for esp32:esp32:esp32 + the Uno firmware, real arduino-cli
python scripts/check_gemma.py             # one real question to Gemma through Ollama, prints the reply and latency
```

Tests never open real serial ports, never upload, and never call a real model. The smoke test saves screenshots to `screenshots/`.

## Demo script (2.5 minutes)

1. **The hook (15 s):** "One TA, thirty students. Ohmni puts a TA on every bench. It reads the circuit, sees the board, and now reads your code too, all on open Gemma 4 running on this laptop."
2. **Wiring (30 s):** Voltage divider, step 2. Green LED. Pull R2 out of the GND rail. Red LED, and within 2 seconds Ohmni says, unprompted, that the midpoint jumped to the rail and the bottom resistor isn't reaching GND, and points at it in the photo.
3. **Code (45 s):** switch to Blink and Measure and open the Code tab. Type `pinMod(LED_PIN, OUTPUT);` and press Compile. The error appears on line 6 in the editor and Ohmni gives a one-sentence hint without giving away the fix. Fix it, press Upload to target. P2 starts toggling on the meter and the step turns green.
4. **Code meets circuit (45 s):** change `LED_PIN` to 12 and upload. The LED stops, P2 stays at 0 V. Hold the bench button and ask "why did my LED stop?" Ohmni connects the two: the sketch drives pin 12, but the probe on the GPIO 13 row reads 0 V and the photo shows the LED wired to 13.
5. **Close (15 s):** "About $10 of hardware per bench, open-weight model, no cloud. Works in any intro electronics or embedded lab."

**Record a backup video of the demo by 6:00** in case the venue wifi, camera or lighting fails on stage.

## Stretch goals

- **Native Gemma audio:** Gemma 4 E2B and E4B accept audio directly, so push-to-talk could skip Chrome's speech recognition (which goes through Google's servers). Until then, say "the model runs locally" rather than "fully offline."
- **Resistor color bands:** ask Gemma to read the bands from the photo and compare them to the expected value.
- **More labs:** RC charging curve (plot P2 over time), PWM brightness measured as average voltage, Ohm's law with a shunt resistor.
