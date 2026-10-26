"""Shared test setup. Tests never open real serial ports, never upload, and never call a real model."""
import os
import sys
from pathlib import Path

import pytest
import serial

SERVER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER))
os.environ.pop("SIM", None)
os.environ.pop("MOCK_MODEL", None)
FAKE_CLI = Path(__file__).resolve().parent / "fake_arduino_cli.py"


def _no_real_serial(self, *a, **k):
    raise AssertionError("tests must never open a real serial port")


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    import app
    monkeypatch.setattr(serial.Serial, "open", _no_real_serial)
    with app.lock:
        app.state.update(lab="divider", step=0, v=[0, 0, 0, 0], status="waiting", hint="", thinking=False,
                         instrument_port=None, connected=False)
    app.hist.clear()
    app.chat.clear()
    app.last_compile, app.last_code, app.sim_sketch = None, "", ""
    with app.serial_lock:
        app.serial_lines.clear()
    app.target.update(port=None, fqbn="esp32:esp32:esp32", baud=115200)
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    import app
    return TestClient(app.app)


@pytest.fixture
def fake_cli(monkeypatch, tmp_path):
    """Point arduino-cli at a fake that logs its arguments. Returns the log path."""
    log = tmp_path / "cli.log"
    monkeypatch.setenv("ARDUINO_CLI", f'"{sys.executable}" "{FAKE_CLI}"')
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    monkeypatch.delenv("FAKE_CORES", raising=False)
    return log
