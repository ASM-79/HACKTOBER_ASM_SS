# Ohmni: a TA on every lab bench

Intro electronics labs have 30 students and one TA. Ohmni sits at every bench: it **measures** the circuit through an ESP32, **sees** the breadboard through a camera, **hears** the student through a push-to-talk button on the bench, and explains what's wrong like a TA would. It runs on **Gemma 4**, an open-weight Apache 2.0 model, locally on a laptop.

**Tracks:** Best Use of Gemma, Best Open-Source AI Project. Confirm with an organizer that one project can enter both.

## How it works

```
breadboard ── probe wires ──> ESP32 (ADC, 10 Hz) ──USB──> server (labs.py checker)
bench button / LEDs <────────┘                               │
camera + mic (browser) ──── photo + question ───────────────> Gemma 4 (Ollama)
                                                             │
                          spoken answer (browser TTS) <──────┘
```

- **The ESP32 is the multimeter.** It powers the lab circuit from its own 3V3 pin and reads 4 nodes with its ADC.
- **The rule checker does the math** (`server/labs.py`): expected voltages, fault patterns, implied resistor ratios. LLMs are bad at arithmetic, so we don't ask Gemma to do it.
- **Gemma does the TA part:** it gets the photo, the readings, the checker's diagnosis, and the question, then says where on the board the problem is and asks a guiding question.
- **It speaks up on its own:** if a working step breaks, the red LED lights up within 1.5 s and Ohmni explains what happened without being asked.

## Wiring (classic ESP32 DevKit)

| What | ESP32 pin | Notes |
|---|---|---|
| Probe P1 | GPIO 34 | Usually on the + rail |
| Probe P2 | GPIO 35 | The node being measured |
| Probe P3 | GPIO 32 | Spare |
| Probe P4 | GPIO 33 | Spare |
| Push-to-talk button | GPIO 27 to GND | Internal pull-up, no resistor needed |
| Green LED | GPIO 25, 220 ohm, LED, GND | Step OK |
| Red LED | GPIO 26, 220 ohm, LED, GND | Fault. Both blinking = thinking |
| Lab power | 3V3 to + rail, GND to - rail | Keeps every node inside the ADC range |

Park unused probe wires in the GND rail so they don't float. The ADC tops out around 3.1 V, so the 3.3 V rail shows as about 3.1 V. That's expected. On an ESP32-S3 or C3, change `PROBE_PINS` to ADC1 pins.

## Setup

1. **Start the model download first** (about 10 GB): install Ollama 0.22 or newer, then `ollama pull gemma4:e4b`. On an 8 GB laptop use `gemma4:e2b` and run with `OLLAMA_MODEL=gemma4:e2b`.
2. **Flash the firmware:** open `firmware/ohmni_probe/ohmni_probe.ino` in the Arduino IDE, select your ESP32 board and port, upload. The Serial Monitor at 115200 should show JSON lines. Close the Serial Monitor afterward so the server can use the port.
3. **Run the server:**
   ```
   cd server
   pip install -r requirements.txt
   python app.py              # real hardware (port auto-detected, or set SERIAL_PORT)
   SIM=1 python app.py        # no hardware: sliders fake the probe
   ```
4. **Open http://localhost:8000 in Chrome.** Voice recognition needs Chrome. Allow camera and mic access, then pick the camera that points at the breadboard. A phone works well as a webcam (Continuity Camera on Mac, or DroidCam).

Teammates without hardware can build the UI and tune prompts with `SIM=1`. To share one model across laptops, run Ollama on the strongest laptop with `OLLAMA_HOST=0.0.0.0 ollama serve`, and point the others at it with `OLLAMA_URL=http://<that-ip>:11434`.

## Labs included

| Lab | Steps | Mistakes it catches |
|---|---|---|
| Voltage divider | Rail, 10k/10k divider, swap R2 for 1k and predict | R2 not reaching GND, R1 open, wrong resistor value (computes the implied ratio) |
| LED + resistor | Rail, light a red LED with 220 ohm, flip it | Backwards LED, LED shorted, unusual LED; reports forward voltage and current |
| Button + pull-down | Rail, button released, button pressed | Floating input (noise), button rotated 90 degrees, bad contact |

Adding a lab is just a new entry in `LABS` in `labs.py`.

## Demo script (2 minutes)

1. **The hook (15 s):** "One TA, thirty students. Ohmni puts a TA on every bench, and it runs fully on open Gemma 4 on this laptop."
2. **Normal flow (30 s):** do the divider step. Green LED. Hold the bench button and ask "is this right?" Ohmni confirms and tells you what's next.
3. **The break (30 s):** pull R2 out of the GND rail. Red LED. Within 2 seconds Ohmni says, unprompted, that the midpoint jumped to the rail and the bottom resistor isn't reaching GND, and points at it in the photo.
4. **Teaching (30 s):** step 3, say "I think it'll be about one volt." Ohmni explains why it will be lower, then the meter shows 0.30 V.
5. **Close (15 s):** "$5 of hardware per bench, open-weight model, no cloud. Works in any lab."

**Record a backup video of the demo by 6:00** in case the venue wifi, camera or lighting fails on stage.

## Plan for today (projects due 6:30)

| Time | Goal |
|---|---|
| Now to 2:30 | Model downloading. Flash firmware, see JSON in Serial Monitor. Run `SIM=1` and open the UI. Wire the divider lab. |
| 2:30 to 4:00 | End to end on real hardware: readings on screen, LEDs react, Gemma answers with the photo. |
| 4:00 to 5:15 | Tune the system prompt on real mistakes. Get the LED lab working. Polish the UI. |
| 5:15 to 6:00 | Rehearse the demo 3 times. Record the backup video. |
| 6:00 to 6:25 | Write the submission and submit. Don't leave this to 6:29. |

Suggested split for 4 people: **hardware and firmware**, **server and prompts**, **UI and voice**, **pitch, submission and backup video**.

## Stretch goals (only if the core demo is solid)

- **Native Gemma audio:** Gemma 4 E2B and E4B accept audio directly, so push-to-talk could skip Chrome's speech recognition (which goes through Google's servers). Ollama doesn't take audio input yet as far as we know, so this would need Hugging Face transformers or mlx-vlm. This would make the "fully offline" claim completely true. Until then, say "the model runs locally" rather than "fully offline."
- **Resistor color bands:** ask Gemma to read the bands from the photo and compare them to the expected value.
- **More labs:** RC charging curve (plot P2 over time), Ohm's law with a shunt resistor.
