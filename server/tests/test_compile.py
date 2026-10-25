"""Compile and upload through a fake arduino-cli. Nothing is uploaded and no sketch code runs."""
import json
import tempfile
from pathlib import Path

import arduino
import app

GOOD = "void setup() {\n  pinMode(13, OUTPUT);\n}\nvoid loop() {}\n"
BAD = "void setup() {\n  Serial.begin(115200);\n  pinMod(13, OUTPUT);\n}\nvoid loop() {}\n"
SID = "testsession01"


def events(gen):
    evs = list(gen)
    return evs, evs[-1]


def cli_calls(log):
    return [json.loads(ln) for ln in Path(log).read_text().splitlines()]


def test_parse_diagnostics():
    out = ("/tmp/x/sketch/sketch.ino:7:3: error: 'pinMod' was not declared in this scope\n"
           "/tmp/x/sketch/sketch.ino:9: warning: unused variable 'x'\n"
           "/tmp/x/sketch/sketch.ino:2:10: fatal error: Foo.h: No such file or directory\n"
           "/tmp/x/sketch/sketch.ino:7:3: error: 'pinMod' was not declared in this scope\n"
           "some other line\n")
    d = arduino.parse_diagnostics(out)
    assert d == [
        {"line": 7, "col": 3, "severity": "error", "message": "'pinMod' was not declared in this scope"},
        {"line": 9, "col": 1, "severity": "warning", "message": "unused variable 'x'"},
        {"line": 2, "col": 10, "severity": "error", "message": "Foo.h: No such file or directory"},
    ]


def test_compile_ok(fake_cli):
    evs, res = events(arduino.compile_events(SID, GOOD, "esp32:esp32:esp32"))
    assert res["ok"] and res["errors"] == []
    assert evs[0]["type"] == "start"
    assert any("Sketch uses" in e.get("text", "") for e in evs)
    compile_call = [c for c in cli_calls(fake_cli) if c[0] == "compile"][0]
    assert compile_call[1:3] == ["--fqbn", "esp32:esp32:esp32"]
    sketch_dir = Path(compile_call[-1])
    assert sketch_dir.name == "sketch" and str(sketch_dir).startswith(str(Path(tempfile.gettempdir()).resolve())) \
        or str(sketch_dir).startswith(tempfile.gettempdir())
    assert (sketch_dir / "sketch.ino").read_text() == GOOD


def test_compile_error_markers(fake_cli):
    _, res = events(arduino.compile_events(SID, BAD, "esp32:esp32:esp32"))
    assert not res["ok"]
    assert res["errors"] == [{"line": 3, "col": 3, "severity": "error",
                              "message": "'pinMod' was not declared in this scope; did you mean 'pinMode'?"}]


def test_sessions_get_separate_folders(fake_cli):
    a, _ = arduino.write_sketch("sessionaaaa", GOOD)
    b, _ = arduino.write_sketch("sessionbbbb", GOOD)
    assert a != b and a.parent != b.parent


def test_missing_cli(monkeypatch):
    monkeypatch.delenv("ARDUINO_CLI", raising=False)
    monkeypatch.setattr(arduino.shutil, "which", lambda name: None)
    _, res = events(arduino.compile_events(SID, GOOD, "esp32:esp32:esp32"))
    assert not res["ok"] and res["install"] == "brew install arduino-cli"


def test_missing_core(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_CORES", "arduino:avr")
    _, res = events(arduino.compile_events(SID, GOOD, "esp32:esp32:esp32"))
    assert res["install"] == "arduino-cli core update-index && arduino-cli core install esp32:esp32"
    assert arduino.check_tools("arduino:avr:uno") is None


def test_rosetta_hint(fake_cli):
    _, res = events(arduino.compile_events(SID, "// ROSETTA\n" + GOOD, "esp32:esp32:esp32"))
    assert res["install"] == arduino.INSTALL_ROSETTA


def test_timeout(fake_cli):
    _, res = events(arduino.compile_events(SID, "// SLEEP_FOREVER\n" + GOOD, "esp32:esp32:esp32", timeout=1))
    assert not res["ok"] and "stopped after" in res["message"]


def test_rejects_bad_input(fake_cli):
    assert "session" in events(arduino.compile_events("../../etc", GOOD, "esp32:esp32:esp32"))[1]["message"]
    assert "Unknown board" in events(arduino.compile_events(SID, GOOD, "foo:bar:baz"))[1]["message"]
    assert "too long" in events(arduino.compile_events(SID, "x" * (arduino.MAX_CODE + 1), "esp32:esp32:esp32"))[1]["message"]


def test_upload_sim_touches_nothing(fake_cli):
    _, res = events(arduino.upload_events(SID, GOOD, "esp32:esp32:esp32", "/dev/sim-esp32-target", sim=True))
    assert res["ok"] and res["sim"]
    assert not any(c[0] == "upload" for c in cli_calls(fake_cli))


def test_upload_pauses_monitor_and_calls_cli(fake_cli):
    calls = []
    evs, res = events(arduino.upload_events(SID, GOOD, "esp32:esp32:esp32", "/dev/fake-esp32", sim=False,
                                            pause=lambda: calls.append("pause") or True,
                                            resume=lambda: calls.append("resume")))
    assert res["ok"] and calls == ["pause", "resume"]
    up = [c for c in cli_calls(fake_cli) if c[0] == "upload"][0]
    assert up[1:5] == ["-p", "/dev/fake-esp32", "--fqbn", "esp32:esp32:esp32"]


def test_upload_stops_on_compile_error(fake_cli):
    _, res = events(arduino.upload_events(SID, BAD, "esp32:esp32:esp32", "/dev/fake-esp32", sim=False))
    assert not res["ok"] and res["stage"] == "compile"
    assert not any(c[0] == "upload" for c in cli_calls(fake_cli))


def test_upload_needs_port(fake_cli):
    _, res = events(arduino.upload_events(SID, GOOD, "esp32:esp32:esp32", None, sim=False))
    assert "Pick the target board port" in res["message"]


def test_compile_endpoint_streams_and_remembers(client, fake_cli):
    r = client.post("/api/compile", json={"session": SID, "code": BAD, "fqbn": "esp32:esp32:esp32"})
    lines = [json.loads(ln) for ln in r.text.splitlines()]
    assert lines[0]["type"] == "start" and lines[-1]["type"] == "result"
    assert app.last_compile["ok"] is False and app.last_compile["errors"][0]["line"] == 3
    assert app.last_code == BAD


def test_tools_endpoint(client, fake_cli, monkeypatch):
    assert client.get("/api/tools?fqbn=esp32:esp32:esp32").json()["ok"]
    monkeypatch.setenv("FAKE_CORES", "")
    r = client.get("/api/tools?fqbn=arduino:avr:uno").json()
    assert not r["ok"] and r["install"].endswith("arduino-cli core install arduino:avr")
