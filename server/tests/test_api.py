"""HTTP and WebSocket endpoints."""
import app


def test_index_is_light_design(client):
    html = client.get("/").text
    for token in ["Fraunces", "Inter", "--parchment:#fefffc", "--signal:#41a1cf", "backdrop-filter", 'data-tab="code"',
                  'data-tab="guide"', "monaco-editor", "codearea"]:
        assert token in html, token
    assert "#0d0f12" not in html and "—" not in html


def test_labs_endpoint(client):
    labs = client.get("/api/labs").json()
    assert list(labs) == ["divider", "led", "button", "blink", "button_led"]
    assert labs["blink"]["steps"][1]["starter"].count("TODO") >= 2
    assert labs["button_led"]["steps"][1]["starter"].count("TODO") >= 2
    assert labs["divider"]["steps"][1]["starter"] is None


def test_lab_and_step(client):
    assert "error" in client.post("/api/lab", json={"lab": "nope"}).json()
    client.post("/api/lab", json={"lab": "led"})
    assert app.state["lab"] == "led" and app.state["step"] == 0
    for _ in range(5):
        client.post("/api/step", json={"delta": 1})
    assert app.state["step"] == 2
    client.post("/api/step", json={"delta": -9})
    assert app.state["step"] == 0


def test_sim_endpoint(client):
    client.post("/api/sim", json={"v": [3300, 100, 0, 0], "ptt": 1, "wave": [1, 9]})
    assert app.sim_input == {"v": [3300, 100, 0, 0], "ptt": 1, "wave": [1]}


def test_websocket_snapshot(client):
    app.push_serial("hello from the target")
    app.target["port"] = "/dev/esp"
    with client.websocket_connect("/ws") as ws:
        snap = ws.receive_json()
    assert snap["serial"] == ["hello from the target"]
    assert snap["target"]["port"] == "/dev/esp" and snap["lab"] == "divider"


def test_fault_raises_alert():
    app.ingest([3300, 3300, 0, 0], 0)
    app.state["step"] = 1
    app._fault_since = None
    app.ingest([3300, 3300, 0, 0], 0)
    assert app.state["status"] == "fault"
