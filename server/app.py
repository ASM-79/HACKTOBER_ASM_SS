"""Ohmni server: reads the ESP32 probe, runs lab checks, and asks Gemma 4 (via Ollama) for TA guidance.

Run:   python app.py            (real ESP32 over USB)
       SIM=1 python app.py      (no hardware: sliders in the UI fake the probe)
Env:   SERIAL_PORT=/dev/cu.usbserial-0001   (auto-detected if unset)
       OLLAMA_MODEL=gemma4:e4b              (or gemma4:e2b on weaker laptops)
       OLLAMA_URL=http://localhost:11434    (point at a teammate's laptop to share one model)
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
from fastapi.responses import FileResponse
from pydantic import BaseModel

from labs import LABS

SERIAL_PORT = os.getenv("SERIAL_PORT")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e4b")
SIM = os.getenv("SIM") == "1"
PORT = int(os.getenv("PORT", "8000"))

SYSTEM = """You are Ohmni, a friendly electronics lab TA standing at a student's bench.
Each turn you get: a photo of their breadboard, live voltages from probe wires an ESP32 is measuring,
and a rule checker's verdict with a short diagnosis.
Rules:
- Trust the probe readings and the checker for numbers. Never invent a measurement.
- Use the photo to point at WHERE: which component, which row, which leg, which color wire.
- Your reply is spoken aloud: at most 3 short sentences, plain words, no markdown, no lists.
- Teach, don't just fix. Give a hint or a guiding question first. Give the full answer if the student asks directly or is stuck.
- If the student makes a prediction, tell them whether it matches the physics and why, in one sentence.
- If the step is OK, say so briefly and tell them what to do next."""

app = FastAPI()
lock = threading.Lock()
hist: deque = deque(maxlen=10)
chat: deque = deque(maxlen=8)  # last 4 exchanges, text only
state = {
    "v": [0, 0, 0, 0], "ptt": 0, "lab": next(iter(LABS)), "step": 0,
    "status": "waiting", "hint": "", "connected": SIM, "sim": SIM,
    "alert_id": 0, "thinking": False, "model": MODEL,
}
sim_input = {"v": [3100, 1650, 0, 0], "ptt": 0}
_ser: Optional[serial.Serial] = None
_last_led: Optional[str] = None
_fault_since: Optional[float] = None
_alerted = False
_was_ok = False


# ---------------- hardware ----------------

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


def find_port():
    for p in serial.tools.list_ports.comports():
        d = f"{p.device} {p.description} {p.manufacturer}".lower()
        if any(k in d for k in ("usbserial", "usbmodem", "ttyusb", "ttyacm", "cp210", "ch340", "ch910", "silicon labs", "wch", "uart")):
            if "bluetooth" not in d:
                return p.device
    return None


def serial_loop():
    global _ser, _last_led
    while True:
        port = SERIAL_PORT or find_port()
        if not port:
            time.sleep(1)
            continue
        try:
            _ser = serial.Serial(port, 115200, timeout=1)
            _last_led = None
            with lock:
                state["connected"] = True
            print(f"[ohmni] probe connected on {port}")
            while True:
                line = _ser.readline().decode(errors="ignore").strip()
                if not line.startswith("{"):
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ingest(d["v"], d.get("ptt", 0))
        except Exception as e:
            print(f"[ohmni] serial error on {port}: {e}. Retrying.")
            _ser = None
            with lock:
                state["connected"] = False
            time.sleep(1)


def sim_loop():
    import random
    while True:
        ingest([max(0, x + random.randint(-15, 15)) for x in sim_input["v"]], sim_input["ptt"])
        time.sleep(0.1)


# ---------------- API ----------------

class LabReq(BaseModel):
    lab: str


class StepReq(BaseModel):
    delta: int = 1


class AskReq(BaseModel):
    text: str = ""
    auto: bool = False
    image: Optional[str] = None  # base64 JPEG, no data: prefix


class SimReq(BaseModel):
    v: list
    ptt: int = 0


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
                "steps": [{"instruction": s["instruction"]} for s in l["steps"]]}
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
    return {"ok": True}


@app.post("/api/ask")
def ask(q: AskReq):
    with lock:
        lab = LABS[state["lab"]]
        i = state["step"]
        step = lab["steps"][i]
        v = list(state["v"])
        status, hint = state["status"], state["hint"]
        state["thinking"] = True
        set_led("y")

    readings = ", ".join(f"{k} ({name}) = {v[int(k[1]) - 1] / 1000:.2f} V" for k, name in lab["probes"].items())
    ctx = (f"Lab: {lab['title']}. Goal: {lab['goal']}\n"
           f"Current step {i + 1} of {len(lab['steps'])}: {step['instruction']}\n"
           f"Live probe readings: {readings}\n"
           f"Checker status: {status}. Checker diagnosis: {hint or 'none'}")
    if q.auto:
        ctx += ("\nThe checker just detected a problem and the student has not asked yet. Speak up: say what "
                "seems wrong and the single first thing to check.")
    else:
        ctx += f'\nThe student said: "{q.text}"'

    msg = {"role": "user", "content": ctx}
    if q.image:
        msg["images"] = [q.image]
    messages = [{"role": "system", "content": SYSTEM}] + list(chat) + [msg]

    t0 = time.time()
    try:
        r = requests.post(f"{OLLAMA_URL}/api/chat", timeout=120, json={
            "model": MODEL, "messages": messages, "stream": False,
            "options": {"temperature": 0.3, "num_ctx": 8192},
        })
        r.raise_for_status()
        reply = r.json()["message"]["content"]
        reply = re.sub(r"<think>.*?</think>", "", reply, flags=re.S).strip() or "Hmm, I lost my train of thought. Ask me again?"
    except Exception as e:
        print(f"[ohmni] model error: {e}")
        reply = f"I can't reach the model right now. Here's what the meter says: {hint or status}."
    finally:
        with lock:
            state["thinking"] = False
            evaluate()

    chat.append({"role": "user", "content": ctx})
    chat.append({"role": "assistant", "content": reply})
    return {"reply": reply, "latency": round(time.time() - t0, 1)}


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    try:
        while True:
            with lock:
                snap = dict(state, v=list(state["v"]))
            await sock.send_json(snap)
            await asyncio.sleep(0.1)
    except Exception:
        pass


if __name__ == "__main__":
    threading.Thread(target=sim_loop if SIM else serial_loop, daemon=True).start()
    print(f"[ohmni] model={MODEL} sim={SIM} -> open http://localhost:{PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
