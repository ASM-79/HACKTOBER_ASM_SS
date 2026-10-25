"""Gemma's prompt carries the sketch, last compile result, serial output, probe readings, checker diagnosis and photo."""
import app
from labs import LABS

SKETCH = "const int LED_PIN = 13;\nvoid setup() {\n  pinMod(LED_PIN, OUTPUT);\n}\nvoid loop() {\n  digitalWrite(LED_PIN, HIGH);\n}\n"
COMPILE = {"ok": False, "fqbn": "esp32:esp32:esp32", "kind": "compile",
           "errors": [{"line": 3, "col": 3, "severity": "error", "message": "'pinMod' was not declared in this scope"}]}
PHOTO = "aGVsbG8="  # base64 stand-in for a JPEG


def test_build_messages_has_everything():
    msgs = app.build_messages(lab=LABS["blink"], step_i=1, v=[3300, 0, 0, 0], status="fault",
                              hint="P2 on the LED pin stays at 0 V", question="why no blink?", code=SKETCH,
                              compile_result=COMPILE, serial_tail=[f"tick {i}" for i in range(60)], image=PHOTO)
    assert msgs[0]["role"] == "system" and "one-sentence hint" in msgs[0]["content"]
    user = msgs[-1]
    c = user["content"]
    assert "  3|   pinMod(LED_PIN, OUTPUT);" in c                                 # sketch, numbered
    assert "digitalWrite(LED_PIN=13)" in c                                       # pins resolved
    assert "FAILED" in c and "line 3: 'pinMod' was not declared" in c            # compile errors
    assert "tick 59" in c and "tick 20" in c and "tick 19" not in c              # last 40 serial lines
    assert "P1 (+ rail) = 3.30 V" in c and "P2 (LED pin (GPIO 13 row)) = 0.00 V" in c  # readings
    assert "Checker status: fault" in c and "stays at 0 V" in c                  # checker diagnosis
    assert user["images"] == [PHOTO] and "photo of the breadboard is attached" in c
    assert 'The student said: "why no blink?"' in c


def test_compile_hint_mode_and_trimming():
    long = "\n".join(f"// line {i}" for i in range(400))
    msgs = app.build_messages(lab=LABS["blink"], step_i=1, v=[0] * 4, status="waiting", hint="", mode="compile_hint",
                              code=long, compile_result=None)
    c = msgs[-1]["content"]
    assert "Give ONE sentence" in c and "Do not write the fixed code" in c
    assert "more lines trimmed" in c and "not compiled yet" in c and "images" not in msgs[-1]


def test_ask_endpoint_sends_full_context_to_ollama(client, monkeypatch):
    sent = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "<think>x</think>Look at line 3: is that the right function name?"}}

    def fake_post(url, json=None, timeout=None):
        sent.update(url=url, body=json)
        return Resp()

    monkeypatch.setattr(app.requests, "post", fake_post)
    client.post("/api/lab", json={"lab": "blink"})
    client.post("/api/step", json={"delta": 1})
    app.ingest([3300, 0, 0, 0], 0)
    app.last_compile = COMPILE
    for i in range(5):
        app.push_serial(f"tick {i}")
    r = client.post("/api/ask", json={"text": "why no blink?", "code": SKETCH, "image": PHOTO}).json()
    assert r["reply"] == "Look at line 3: is that the right function name?"
    assert sent["url"].endswith("/api/chat") and sent["body"]["model"] == app.MODEL
    user = sent["body"]["messages"][-1]
    for piece in ["pinMod(LED_PIN, OUTPUT)", "line 3: 'pinMod'", "tick 4", "P1 (+ rail) = 3.30 V",
                  "Checker status: waiting", "Watching the LED pin", "why no blink?"]:
        assert piece in user["content"], piece
    assert user["images"] == [PHOTO]
    # history keeps text only, not the whole sketch
    assert app.chat[-2]["content"] == "why no blink?"


def test_mock_model_reply(client, monkeypatch):
    monkeypatch.setattr(app, "MOCK_MODEL", True)
    app.last_compile = COMPILE
    r = client.post("/api/ask", json={"mode": "compile_hint", "code": SKETCH}).json()
    assert r["reply"].startswith("Hint: look at line 3")


def test_model_down_falls_back(client, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("no ollama")
    monkeypatch.setattr(app.requests, "post", boom)
    r = client.post("/api/ask", json={"text": "hi"}).json()
    assert "can't reach the model" in r["reply"]
