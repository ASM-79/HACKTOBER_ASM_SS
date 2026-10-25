"""Ohmni server: reads the Uno instrument, runs lab checks, watches the target board, and asks Gemma 4 (via Ollama).

Two boards share one breadboard GND:
  instrument = Arduino Uno running firmware/ohmni_probe_uno (streams probe JSON). Found automatically by its stream.
  target     = the student's ESP32 (esp32:esp32:esp32). The user picks its port in the Code tab.

Run:   python app.py            (real hardware)
       SIM=1 python app.py      (no hardware: sliders in the UI fake the probe, the target is simulated)
Env:   INSTRUMENT_PORT=/dev/cu.usbmodem1101  (skip auto-detect; SERIAL_PORT is accepted too)
       INSTRUMENT_BAUD=57600                 (default: try 57600 for the Uno, then 115200 for the ESP32 probe)
       TARGET_PORT=/dev/cu.usbserial-0001    (preselect the target board port)
       OLLAMA_MODEL=gemma4:e4b               (or gemma4:e2b on weaker laptops)
       OLLAMA_URL=http://localhost:11434     (point at a teammate's laptop to share one model)
       MOCK_MODEL=1                          (canned replies, no Ollama needed; used by the smoke test)
"""
import asyncio
import json
import os
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

import requests
import serial
import serial.tools.list_ports
import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import arduino
from labs import HIST_LEN, LABS

INSTRUMENT_PORT = os.getenv("INSTRUMENT_PORT") or os.getenv("SERIAL_PORT")
# Uno firmware streams at 57600, the ESP32 instrument firmware at 115200. Try both unless told.
INSTRUMENT_BAUDS = [int(os.getenv("INSTRUMENT_BAUD"))] if os.getenv("INSTRUMENT_BAUD") else [57600, 115200]
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e4b")
SIM = os.getenv("SIM") == "1"
MOCK_MODEL = os.getenv("MOCK_MODEL") == "1"
PORT = int(os.getenv("PORT", "8000"))

FQBNS = {"esp32:esp32:esp32": "ESP32 Dev Module", "arduino:avr:uno": "Arduino Uno"}
BAUDS = [9600, 19200, 38400, 57600, 115200, 230400]
SIM_INSTRUMENT = "/dev/sim-uno-instrument"
SIM_TARGET = "/dev/sim-esp32-target"
SNIFF_S = 3.0  # the Uno can reset when its port opens, so give it time to start streaming

SYSTEM = """You are Ohmni, a friendly electronics lab TA standing at a student's bench.
Each turn you get: a photo of their breadboard, live voltages from probe wires an Arduino Uno instrument is measuring,
and a rule checker's verdict with a short diagnosis.
Rules:
- Trust the probe readings and the checker for numbers. Never invent a measurement.
- Use the photo to point at WHERE: which component, which row, which leg, which color wire.
- Your reply is spoken aloud: at most 3 short sentences, plain words, no markdown, no lists.
- Teach, don't just fix. Give a hint or a guiding question first. Give the full answer if the student asks directly or is stuck.
- If the student makes a prediction, tell them whether it matches the physics and why, in one sentence.
- If the step is OK, say so briefly and tell them what to do next.
The student also programs a target board (usually an ESP32) from inside Ohmni. When you get their sketch,
compiler errors, or serial output, connect the code to the circuit: for example, "your code drives pin 13,
but P2 on the LED row reads 0 V, and in the photo the LED wire goes to pin 12."
- Refer to code by line number. Quote at most one short line of code.
- After a failed compile, give a one-sentence hint that points at the line and the idea, not the fixed code,
  unless the student asks for the answer."""

app = FastAPI()
lock = threading.Lock()
hist: deque = deque(maxlen=HIST_LEN)
chat: deque = deque(maxlen=8)  # last 4 exchanges, text only
state = {
    "v": [0, 0, 0, 0], "ptt": 0, "lab": next(iter(LABS)), "step": 0,
    "status": "waiting", "hint": "", "connected": SIM, "sim": SIM,
    "alert_id": 0, "thinking": False, "model": MODEL,
    "instrument_port": SIM_INSTRUMENT if SIM else None,
}
target = {"port": os.getenv("TARGET_PORT") or (SIM_TARGET if SIM else None), "fqbn": "esp32:esp32:esp32", "baud": 115200}
target_ver = 0  # bumped whenever the target config changes so the monitor reopens the port
sim_input = {"v": [3300, 1650, 0, 0], "ptt": 0, "wave": []}  # wave: probe indexes that square-wave at 1 Hz
last_compile: Optional[dict] = None  # {"ok", "errors", "fqbn", "kind"} from the most recent compile or upload
last_code = ""
sim_sketch = ""
_ser: Optional[serial.Serial] = None
_last_led: Optional[str] = None
_fault_since: Optional[float] = None
_alerted = False
_was_ok = False

serial_lock = threading.Lock()
serial_lines: deque = deque(maxlen=400)  # (seq, text) from the target board
serial_seq = 0
monitor_paused = threading.Event()   # set while an upload owns the target port
monitor_released = threading.Event()  # set when the monitor has closed the target port
monitor_released.set()


# ---------------- port identification ----------------

PORT_KEYS = ("usbserial", "usbmodem", "ttyusb", "ttyacm", "cp210", "ch340", "ch910", "silicon labs", "wch", "uart",
             "arduino", "slab", "wchusb")
NOT_SERIAL = ("bluetooth", "billboard", "debug-console")  # USB-C hub billboard devices show up as usbmodem ports


def parse_probe_line(line: str) -> Optional[dict]:
    """Return {"v": [4 ints], "ptt": 0|1} if this is an Ohmni instrument JSON line, else None."""
    line = (line or "").strip()
    if not line.startswith("{"):
        return None
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        return None
    v = d.get("v") if isinstance(d, dict) else None
    if not isinstance(v, list) or len(v) < 4:
        return None
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v[:4]):
        return None
    return {"v": [int(x) for x in v[:4]], "ptt": 1 if d.get("ptt") else 0}


def looks_like_instrument(lines, need: int = 3) -> bool:
    """The instrument is identified by its stream: several valid probe JSON lines, not by USB names."""
    return sum(1 for ln in lines if parse_probe_line(ln)) >= need


def candidate_ports(ports, exclude=()) -> list:
    """ports: [(device, description)]. Keep USB serial adapters, drop Bluetooth and excluded devices."""
    out = []
    for dev, desc in ports:
        d = f"{dev} {desc}".lower()
        if dev in exclude or any(k in d for k in NOT_SERIAL):
            continue
        if any(k in d for k in PORT_KEYS):
            out.append(dev)
    return out


def auto_target(candidates, instrument: Optional[str], current: Optional[str]) -> Optional[str]:
    """If no target is chosen and exactly one other USB serial port is present, that is the target."""
    if current:
        return current
    others = [c for c in candidates if c != instrument]
    return others[0] if len(others) == 1 else None


def system_ports() -> list:
    if SIM:
        return [(SIM_INSTRUMENT, "Simulated Uno instrument"), (SIM_TARGET, "Simulated ESP32 target")]
    return [(p.device, p.description or "") for p in serial.tools.list_ports.comports()]


def open_port(dev: str, baud: int, timeout: float = 0.3) -> serial.Serial:
    """Open without toggling DTR/RTS so an ESP32 is not reset just by looking at it."""
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = dev, baud, timeout
    s.dtr = False
    s.rts = False
    s.open()
    return s


# ---------------- instrument ----------------

def set_led(c: str):
    global _last_led
    if c == _last_led:
        return
    _last_led = c
    if _ser:
        try:
            _ser.write(c.encode())
        except Exception:
            pass


def evaluate():
    """Run the current step's check. Caller holds the lock."""
    global _fault_since, _alerted, _was_ok
    step = LABS[state["lab"]]["steps"][state["step"]]
    status, hint = step["check"](state["v"], list(hist))
    state["status"], state["hint"] = status, hint
    now = time.time()
    if status == "ok":
        _was_ok = True
    if status == "fault":
        if _fault_since is None:
            _fault_since = now
        # Something that was working just broke: speak up fast. A wiring mistake: give them a moment.
        hold = 1.5 if _was_ok else 5.0
        if not _alerted and now - _fault_since >= hold:
            _alerted = True
            state["alert_id"] += 1
    else:
        _fault_since, _alerted = None, False
    set_led("y" if state["thinking"] else {"ok": "g", "fault": "r"}.get(status, "0"))


def ingest(v, ptt):
    with lock:
        state["v"] = [int(x) for x in v[:4]]
        state["ptt"] = int(ptt)
        hist.append(state["v"])
        evaluate()


def run_instrument(s: serial.Serial, port: str):
    """Read probe JSON until the port fails or the user claims it as the target."""
    global _ser, _last_led
    _ser, _last_led = s, None
    with lock:
        state["connected"], state["instrument_port"] = True, port
    print(f"[ohmni] instrument found on {port}")
    try:
        while target["port"] != port:
            d = parse_probe_line(s.readline().decode(errors="ignore"))
            if d:
                ingest(d["v"], d["ptt"])
    except Exception as e:
        print(f"[ohmni] instrument error on {port}: {e}. Searching again.")
    finally:
        _ser = None
        try:
            s.close()
        except Exception:
            pass
        with lock:
            state["connected"], state["instrument_port"] = False, None


def sniff_instrument(port: str) -> Optional[serial.Serial]:
    """Open a port and keep it only if it streams Ohmni probe JSON within SNIFF_S seconds at a known baud."""
    for baud in INSTRUMENT_BAUDS:
        try:
            s = open_port(port, baud)
        except Exception:
            return None
        lines, t0 = [], time.time()
        try:
            while time.time() - t0 < SNIFF_S and not looks_like_instrument(lines):
                lines.append(s.readline().decode(errors="ignore"))
        except Exception:
            pass
        if looks_like_instrument(lines):
            print(f"[ohmni] instrument streams at {baud} baud on {port}")
            return s
        s.close()
    return None


def instrument_loop():
    while True:
        cands = [INSTRUMENT_PORT] if INSTRUMENT_PORT else candidate_ports(system_ports(), exclude={target["port"]})
        for port in cands:
            s = sniff_instrument(port)
            if not s:
                continue
            new_target = auto_target(candidate_ports(system_ports()), port, target["port"])
            if new_target and new_target != target["port"]:
                set_target(port=new_target)
                print(f"[ohmni] target board assumed on {new_target}")
            run_instrument(s, port)
            break
        time.sleep(1)


def sim_loop():
    import random
    while True:
        high = int(time.time() * 2) % 2 == 0
        v = [(3300 if high else 0) if i in sim_input["wave"] else x for i, x in enumerate(sim_input["v"])]
        ingest([max(0, x + random.randint(-15, 15)) for x in v], sim_input["ptt"])
        time.sleep(0.1)


# ---------------- target board serial monitor ----------------

def push_serial(text: str):
    global serial_seq
    with serial_lock:
        serial_seq += 1
        serial_lines.append((serial_seq, text.rstrip("\r\n")[:300]))


def recent_serial(n: int = 40) -> list:
    with serial_lock:
        return [t for _, t in list(serial_lines)[-n:]]


def set_target(port=None, fqbn=None, baud=None):
    global target_ver
    if port is not None:
        target["port"] = port or None
    if fqbn is not None:
        target["fqbn"] = fqbn
    if baud is not None:
        target["baud"] = int(baud)
    target_ver += 1


def pause_monitor(timeout: float = 3.0) -> bool:
    """Release the target port (for uploads). Returns True once the monitor has let go."""
    monitor_paused.set()
    return monitor_released.wait(timeout)


def resume_monitor():
    monitor_paused.clear()


def target_loop():
    while True:
        port, baud, ver = target["port"], target["baud"], target_ver
        if not port or monitor_paused.is_set() or port == state["instrument_port"]:
            time.sleep(0.3)
            continue
        try:
            s = open_port(port, baud, timeout=0.2)
        except Exception:
            time.sleep(1)
            continue
        monitor_released.clear()
        push_serial(f"[ohmni] serial monitor on {port} at {baud} baud")
        buf = b""
        try:
            while target_ver == ver and not monitor_paused.is_set():
                buf += s.read(256)
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    push_serial(line.decode(errors="replace"))
        except Exception as e:
            push_serial(f"[ohmni] serial monitor lost {port}: {e}")
            time.sleep(1)
        finally:
            s.close()
            monitor_released.set()


SERIAL_LITERAL = re.compile(r'Serial\.print(?:ln)?\(\s*"((?:[^"\\]|\\.)*)"')


def sim_target_loop():
    """SIM stand-in for the target board: echoes the uploaded sketch's Serial.print text. Nothing is executed."""
    push_serial("[sim] ESP32 target idle. Upload a sketch from the Code tab.")
    shown, i = None, 0
    while True:
        time.sleep(1)
        if not sim_sketch:
            continue
        if sim_sketch is not shown:
            shown, i = sim_sketch, 0
            push_serial("[sim] Sketch uploaded (simulated). Echoing its Serial.print text, nothing runs on this laptop.")
        lits = SERIAL_LITERAL.findall(sim_sketch)
        if lits:
            push_serial(lits[i % len(lits)])
            i += 1


# ---------------- API ----------------

class LabReq(BaseModel):
    lab: str


class StepReq(BaseModel):
    delta: int = 1


class AskReq(BaseModel):
    text: str = ""
    auto: bool = False
    mode: str = "ask"            # ask | auto | compile_hint
    image: Optional[str] = None  # base64 JPEG, no data: prefix
    code: Optional[str] = None   # the sketch currently in the editor


class SimReq(BaseModel):
    v: list
    ptt: int = 0
    wave: list = []


class BuildReq(BaseModel):
    session: str
    code: str
    fqbn: Optional[str] = None


class TargetReq(BaseModel):
    port: Optional[str] = None
    fqbn: Optional[str] = None
    baud: Optional[int] = None


def reset_step_state():
    global _fault_since, _alerted, _was_ok
    hist.clear()
    _fault_since, _alerted, _was_ok = None, False, False


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/labs")
def labs():
    return {k: {"title": l["title"], "goal": l["goal"], "probes": l["probes"],
                "steps": [{"instruction": s["instruction"], "starter": s.get("starter")} for s in l["steps"]]}
            for k, l in LABS.items()}


@app.post("/api/lab")
def set_lab(r: LabReq):
    if r.lab not in LABS:
        return {"error": "unknown lab"}
    with lock:
        state["lab"], state["step"] = r.lab, 0
        reset_step_state()
        chat.clear()
        evaluate()
    return {"ok": True}


@app.post("/api/step")
def move_step(r: StepReq):
    with lock:
        n = len(LABS[state["lab"]]["steps"])
        state["step"] = max(0, min(n - 1, state["step"] + r.delta))
        reset_step_state()
        evaluate()
    return {"ok": True}


@app.post("/api/sim")
def sim(r: SimReq):
    sim_input["v"] = [int(x) for x in r.v[:4]]
    sim_input["ptt"] = r.ptt
    sim_input["wave"] = [int(i) for i in r.wave if 0 <= int(i) < 4]
    return {"ok": True}


@app.get("/api/ports")
def ports():
    inst = state["instrument_port"]
    rows = []
    for dev, desc in system_ports():
        role = "instrument" if dev == inst else "target" if dev == target["port"] else None
        rows.append({"device": dev, "description": desc, "role": role})
    return {"ports": rows, "instrument": inst, "target": dict(target), "fqbns": FQBNS, "bauds": BAUDS}


@app.post("/api/target")
def choose_target(r: TargetReq):
    if r.fqbn is not None and r.fqbn not in FQBNS:
        return {"error": f"Unknown board {r.fqbn}. Use one of: {', '.join(FQBNS)}"}
    if r.baud is not None and r.baud not in BAUDS:
        return {"error": f"Unsupported baud {r.baud}"}
    if r.port and r.port == state["instrument_port"]:
        return {"error": "That port is the Ohmni instrument (the Uno streaming probe readings). Pick the target board."}
    set_target(port=r.port, fqbn=r.fqbn, baud=r.baud)
    return {"ok": True, "target": dict(target)}


SKETCH_MAX_LINES = 160
PIN_CALL = re.compile(r"\b(pinMode|digitalWrite|digitalRead|analogRead|analogWrite)\s*\(\s*(\w+)")
PIN_CONST = re.compile(r"(?:#define\s+(\w+)\s+(\d+))|(?:\b(?:int|byte|uint8_t|short|long)\s+(\w+)\s*=\s*(\d+))")


def number_sketch(code: str) -> str:
    """Sketch with line numbers so Gemma can match compiler errors, trimmed to a sane length."""
    lines = code.splitlines()
    shown = [f"{n:>3}| {ln}" for n, ln in enumerate(lines[:SKETCH_MAX_LINES], 1)]
    if len(lines) > SKETCH_MAX_LINES:
        shown.append(f"... ({len(lines) - SKETCH_MAX_LINES} more lines trimmed)")
    return "\n".join(shown)


def sketch_pins(code: str) -> str:
    """Which pins the sketch touches, with constants resolved: 'pinMode(LED_PIN=13), digitalWrite(LED_PIN=13)'."""
    consts = {}
    for m in PIN_CONST.finditer(code):
        name, val = (m[1], m[2]) if m[1] else (m[3], m[4])
        consts[name] = val
    seen = []
    for fn, arg in PIN_CALL.findall(code):
        item = f"{fn}({arg}={consts[arg]})" if arg in consts else f"{fn}({arg})"
        if item not in seen:
            seen.append(item)
    return ", ".join(seen)


def describe_compile(res: Optional[dict]) -> str:
    if not res:
        return "Last compile: not compiled yet."
    if res.get("ok"):
        return f"Last compile: succeeded for {res.get('fqbn')}" + (" and uploaded." if res.get("kind") == "upload" else ".")
    errs = [e for e in res.get("errors", []) if e["severity"] == "error"][:6]
    if not errs:
        return f"Last compile: failed. {res.get('message') or res.get('install') or ''}".strip()
    return "Last compile: FAILED with errors:\n" + "\n".join(f"  line {e['line']}: {e['message']}" for e in errs)


def build_messages(*, lab: dict, step_i: int, v: list, status: str, hint: str, question: str = "",
                   mode: str = "ask", code: str = "", compile_result: Optional[dict] = None,
                   serial_tail=(), image: Optional[str] = None, history=()) -> list:
    """Everything Gemma sees for one turn: step, probe readings, checker, sketch, compile result, serial, photo."""
    step = lab["steps"][step_i]
    readings = ", ".join(f"{k} ({name}) = {v[int(k[1]) - 1] / 1000:.2f} V" for k, name in lab["probes"].items())
    parts = [
        f"Lab: {lab['title']}. Goal: {lab['goal']}",
        f"Current step {step_i + 1} of {len(lab['steps'])}: {step['instruction']}",
        f"Live probe readings (Uno instrument): {readings}",
        f"Checker status: {status}. Checker diagnosis: {hint or 'none'}",
    ]
    if code.strip():
        pins = sketch_pins(code)
        parts.append("Student sketch for the target board (sketch.ino):\n" + number_sketch(code))
        if pins:
            parts.append(f"Pins the sketch uses: {pins}")
    else:
        parts.append("Student sketch: none yet.")
    parts.append(describe_compile(compile_result))
    tail = list(serial_tail)[-40:]
    parts.append(("Recent serial output from the target board (last 40 lines):\n" + "\n".join(tail))
                 if tail else "Recent serial output from the target board: none.")
    parts.append("A photo of the breadboard is attached." if image else "No photo this turn.")
    if mode == "auto":
        parts.append("The checker just detected a problem and the student has not asked yet. Speak up: say what "
                     "seems wrong and the single first thing to check.")
    elif mode == "compile_hint":
        parts.append("The compile just failed and the student has not asked yet. Give ONE sentence: a hint that "
                     "points at the line and the idea. Do not write the fixed code.")
    else:
        parts.append(f'The student said: "{question}"')
    msg = {"role": "user", "content": "\n".join(parts)}
    if image:
        msg["images"] = [image]
    return [{"role": "system", "content": SYSTEM}] + list(history) + [msg]


def mock_reply(mode: str, hint: str, compile_result: Optional[dict]) -> str:
    errs = [e for e in (compile_result or {}).get("errors", []) if e["severity"] == "error"]
    if mode == "compile_hint" and errs:
        return f"Hint: look at line {errs[0]['line']}. The compiler is telling you {errs[0]['message'].split(';')[0]}."
    return f"Mock Gemma here. The meter says: {(hint or 'everything reads as expected').rstrip('.')}."


def call_model(messages: list) -> str:
    r = requests.post(f"{OLLAMA_URL}/api/chat", timeout=120, json={
        "model": MODEL, "messages": messages, "stream": False,
        "options": {"temperature": 0.3, "num_ctx": 8192},
    })
    r.raise_for_status()
    reply = r.json()["message"]["content"]
    return re.sub(r"<think>.*?</think>", "", reply, flags=re.S).strip() or "Hmm, I lost my train of thought. Ask me again?"


@app.post("/api/ask")
def ask(q: AskReq):
    global last_code
    mode = "auto" if q.auto else q.mode
    with lock:
        lab = LABS[state["lab"]]
        i = state["step"]
        v = list(state["v"])
        status, hint = state["status"], state["hint"]
        state["thinking"] = True
        set_led("y")
    if q.code is not None:
        last_code = q.code
    messages = build_messages(lab=lab, step_i=i, v=v, status=status, hint=hint, question=q.text, mode=mode,
                              code=last_code, compile_result=last_compile, serial_tail=recent_serial(40),
                              image=q.image, history=chat)
    t0 = time.time()
    try:
        reply = mock_reply(mode, hint, last_compile) if MOCK_MODEL else call_model(messages)
    except Exception as e:
        print(f"[ohmni] model error: {e}")
        reply = f"I can't reach the model right now. Here's what the meter says: {hint or status}."
    finally:
        with lock:
            state["thinking"] = False
            evaluate()

    asked = {"auto": "(Ohmni spoke up about a fault)", "compile_hint": "(Ohmni hinted after a failed compile)"}
    chat.append({"role": "user", "content": asked.get(mode, q.text)})
    chat.append({"role": "assistant", "content": reply})
    return {"reply": reply, "latency": round(time.time() - t0, 1)}


def _ndjson(events, kind: str, fqbn: str, code: str):
    """Stream build events to the browser and remember the result for Gemma."""
    global last_compile, last_code, sim_sketch
    last_code = code
    for ev in events:
        if ev["type"] == "result":
            last_compile = {"ok": ev["ok"], "errors": ev.get("errors", []), "fqbn": fqbn, "kind": kind,
                            "message": ev.get("message"), "install": ev.get("install")}
            if kind == "upload" and ev["ok"] and ev.get("sim"):
                sim_sketch = code
        yield json.dumps(ev) + "\n"


@app.get("/api/tools")
def tools(fqbn: str = "esp32:esp32:esp32"):
    if fqbn not in arduino.CORES:
        return {"error": f"Unknown board {fqbn}"}
    return {"ok": True, "fqbn": fqbn} if not (m := arduino.check_tools(fqbn)) else {"ok": False, "fqbn": fqbn, **m}


@app.post("/api/compile")
def compile_sketch(r: BuildReq):
    fqbn = r.fqbn or target["fqbn"]
    return StreamingResponse(_ndjson(arduino.compile_events(r.session, r.code, fqbn), "compile", fqbn, r.code),
                             media_type="application/x-ndjson")


@app.post("/api/upload")
def upload_sketch(r: BuildReq):
    fqbn = r.fqbn or target["fqbn"]
    events = arduino.upload_events(r.session, r.code, fqbn, target["port"], SIM, pause=pause_monitor, resume=resume_monitor)
    return StreamingResponse(_ndjson(events, "upload", fqbn, r.code), media_type="application/x-ndjson")


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    seen = 0
    try:
        while True:
            with lock:
                snap = dict(state, v=list(state["v"]))
            with serial_lock:
                new = [(n, t) for n, t in serial_lines if n > seen]
            if new:
                seen = new[-1][0]
            snap["serial"] = [t for _, t in new]
            snap["target"] = dict(target)
            await sock.send_json(snap)
            await asyncio.sleep(0.1)
    except Exception:
        pass


def start_background():
    if SIM:
        threading.Thread(target=sim_loop, daemon=True).start()
        threading.Thread(target=sim_target_loop, daemon=True).start()
    else:
        threading.Thread(target=instrument_loop, daemon=True).start()
        threading.Thread(target=target_loop, daemon=True).start()


if __name__ == "__main__":
    start_background()
    print(f"[ohmni] model={MODEL} sim={SIM} -> open http://localhost:{PORT}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
