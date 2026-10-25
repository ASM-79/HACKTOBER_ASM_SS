# Ohmni product spec (source of truth for the build)

Hackathon: DS3 Hack Day San Diego, projects due **6:30pm today**. Target tracks: **Best Use of Gemma** and **Best Open-Source AI Project**. Everything AI runs on **Gemma 4 via Ollama** (`gemma4:e4b` default, laptop is an Apple Silicon Mac with 24 GB RAM).

The repo already contains a working v1 (see README.md): ESP32 probe firmware, FastAPI server with lab checker (`server/labs.py`), and a single-page UI. **Do not break it.** Every milestone must leave `SIM=1 python server/app.py` working.

## 1. What Ohmni is

An AI lab TA for intro electronics and embedded labs. One bench = a breadboard, an **instrument ESP32** (the probe, measures voltages), a **target board** the student programs, a camera, and a laptop. Ohmni:

- **Measures** circuit nodes through the instrument ESP32 (existing).
- **Sees** the breadboard through the camera (existing).
- **Hears** the student via push-to-talk (existing).
- **NEW: hosts the student's coding environment.** Students write, compile, and upload Arduino code to the target board from inside Ohmni, see serial output, and Ohmni debugs using code + compiler errors + serial output + live voltages + photo together. This is the core differentiator: an AI TA that sees both the code and the circuit.

## 2. Hardware model

- Instrument (board 1, Ohmni station): Arduino Uno flashed with firmware/ohmni_probe_uno. Probes on A0-A3 read 0 to 5 V directly, no dividers. Same JSON protocol as the ESP32 firmware, which stays in the repo as an alternative. Remove the probe-scale and 5 V warning features; they are not needed.
- Target (board 2, student project): ESP32 (FQBN esp32:esp32:esp32). The student circuit is always powered from the target's 3V3 pin. The instrument never powers the student circuit. Update all lab instructions in labs.py to say "target board 3V3" where they refer to the power source, and update the ADC saturation comment in labs.py since the Uno reads 3.3 V correctly.
- Both boards share GND. This is required for any reading to make sense.


## 3. Coding environment (new)

- **Editor:** Monaco (load from cdn.jsdelivr.net) with C++ highlighting. Fall back to a plain textarea if the CDN fails. One sketch per lab step that needs code, with starter code that has TODOs.
- **Compile and upload:** server shells out to `arduino-cli` (`compile --fqbn <fqbn>` and `upload -p <port> --fqbn <fqbn>`). FQBNs: `esp32:esp32:esp32` and `arduino:avr:uno`. Stream compiler output to the UI. Parse errors into line markers in the editor. If `arduino-cli` or a core is missing, show the exact install command in the UI instead of crashing.
- **Serial monitor:** panel showing target board output (115200 default, selectable).
- **Ask about code:** Gemma's context includes the current sketch (trimmed to relevant length), last compile result (errors), last ~40 lines of serial output, probe readings, checker diagnosis, and the photo. Example of what it should catch: "your code writes pin 13 HIGH, but P2 on the LED row reads 0 V, and in the photo the LED is wired to pin 12."
- **Auto-help on compile errors:** after a failed compile, Ohmni gives a one-sentence hint (not the full fix) unless the student asks for the answer.
- **Lab steps can now be code steps.** Add at least two coding labs to `labs.py`:
  1. **Blink and measure:** write code to blink an LED on a pin. Checker passes when the probe on the LED node toggles between low and high (detect from `hist`; widen history as needed).
  2. **Button to LED:** read a button with a pull-down and light the LED while pressed. Checker passes when the input node and LED node track each other.
  Keep the three existing wiring labs.
- **Sandboxing:** only compile code inside a per-session temp sketch folder. Never execute student code on the laptop. Cap compile time at 120 s.

## 4. Design system (reference: General Intelligence Company style on Refero)

Light, editorial, "literary journal beside a bonfire." Spacious, calm, book-like. Replace the current dark UI.

**Colors (CSS vars on `:root`):** parchment `#fefffc` (page canvas), paper `#ffffff` (cards), linen `#f9faf7` (inputs, wash), ink `#171717` (primary text), graphite `#2c2c2c` (headings), charcoal `#444141` (secondary text, button labels), ash `#646464` (helper text), fog `#b4b8b4` (disabled), mist `#dee2de` (hairline borders), twilight `#282834` (outlines, icon strokes), dusk `#1f1f29` (filled button), signal blue `#41a1cf` (outlined CTA border and links only), cerulean `#0081c0` (one atmospheric surface). Never `#000` for text. Never fill buttons with blue.
**Functional status exception:** ok `#3f8f5a`, fault `#c2483d`, used only as a small dot plus text label on status pills and the meter edge. No other new colors.

**Type:** display serif **Fraunces** (stand-in for ppmondwest) weight 400, sizes 27/40/48/54, line height 1.1, tracking -0.02em. UI **Inter** (stand-in for af) 13/15/16/18, weights 400 body and 500 labels, tracking -0.01em, line height 1.5 body. Load both from Google Fonts. Meter numbers: Inter with `font-variant-numeric: tabular-nums`, large. Code editor: a monospace font.

**Shape and depth:** 4 px spacing grid (4..80). Cards: paper, 1 px mist border, 12 px radius, 16 px padding, shadow `0 1px 1px rgba(0,0,0,.08), 0 4px 5px rgba(0,0,0,.08)`. Large surfaces 24 px radius. Nav is a frosted pill (50 px radius, backdrop blur, `0 2px 6px rgba(0,0,0,.15)`). Buttons 8 px radius, no shadows: primary = transparent with 1 px signal-blue border and text; secondary = twilight border; filled dusk button used sparingly (the push-to-talk button). Inputs: 0 radius, linen fill, bottom border only. Max width 1200 px. Minimal icons: simple line arrows only.

**Layout:** frosted nav pill at top center (logo "Ohmni" in Fraunces, lab picker, camera picker, connection status). Below, a 6+6 split: left column = bench (camera card, meter cards); right column = tabs **Guide** (step, status, hint, push-to-talk, conversation) and **Code** (editor, compile/upload buttons, output, serial monitor). One cerulean atmospheric card is allowed: the empty-state welcome before a lab starts. Must work at 1280x800 (demo laptop) and down to 900 px wide.

## 5. Quality bar and tests

- `pytest` suite in `server/tests/`: lab checks (every fault path in every lab), API endpoints with a mocked Ollama, port identification logic, compile endpoint with a mocked `arduino-cli`, prompt builder includes code + errors + serial + readings.
- Starter sketches for every coding lab must compile with real `arduino-cli` for both FQBNs if the cores are installed. If a core isn't installed, say so and skip, don't fake it.
- Playwright (Python) smoke test: start the server with `SIM=1` and a mock model, load the page, switch labs, type in the editor, click compile, ask a typed question, and save screenshots to `screenshots/` at 1280x800.
- A real-model check script `scripts/check_gemma.py` that sends one photo-free question to Ollama and prints the latency. Run it if `ollama list` shows a gemma4 model.
- Update README.md (setup for arduino-cli and cores, two-board wiring, demo script that includes the coding feature).

## 6. Rules

- Work on a git branch `goal/ohmni-v2`. Commit after each milestone with a clear message.
- Milestone order: (1) restyle existing UI to the design system, (2) two-board serial handling, (3) code environment, (4) coding labs, (5) tests and screenshots, (6) README. If time runs short, a polished (1)+(2)+(3) beats a half-done everything.
- Never upload firmware to a board or open hardware serial ports during tests. Hardware is wired and tested by humans.
- No new paid APIs. No cloud dependencies besides CDN fonts and Monaco.
- No em dashes in any user-facing text, docs, or comments.
