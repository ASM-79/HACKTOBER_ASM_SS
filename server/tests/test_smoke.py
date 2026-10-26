"""Playwright smoke test: SIM=1 server with a mock model and a fake arduino-cli, real browser, screenshots at 1280x800.

Saves screenshots/01_guide.png, 02_code_compile.png, 03_fault.png in the repo root.
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

sync_api = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
SHOTS = ROOT / "screenshots"
FAKE_CLI = Path(__file__).resolve().parent / "fake_arduino_cli.py"
BAD_SKETCH = """// Blink and measure
const int LED_PIN = 13;

void setup() {
  Serial.begin(115200);
  pinMod(LED_PIN, OUTPUT);
}

void loop() {
  digitalWrite(LED_PIN, HIGH);
  delay(500);
  digitalWrite(LED_PIN, LOW);
  delay(500);
}
"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    port = free_port()
    env = dict(os.environ, SIM="1", MOCK_MODEL="1", PORT=str(port),
               ARDUINO_CLI=f'"{sys.executable}" "{FAKE_CLI}"')
    proc = subprocess.Popen([sys.executable, "app.py"], cwd=SERVER, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    url = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            requests.get(url + "/api/labs", timeout=0.5)
            break
        except requests.RequestException:
            time.sleep(0.25)
    else:
        proc.kill()
        pytest.fail("server did not start: " + proc.stdout.read())
    yield url
    proc.terminate()
    proc.wait(5)


@pytest.fixture(scope="module")
def page(server):
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:
            pytest.skip(f"chromium not available: {e}")
        pg = browser.new_page(viewport={"width": 1280, "height": 800})
        pg.goto(server)
        yield pg
        browser.close()


def test_smoke(page, server):
    expect = sync_api.expect
    SHOTS.mkdir(exist_ok=True)
    expect(page.locator("#welcome")).to_be_visible()

    # switch labs: Blink and Measure, step 2, with the simulated LED pin blinking at 1 Hz
    page.select_option("#lab", "blink")
    expect(page.locator("#labtitle")).to_have_text("Blink and Measure")
    page.click("#next")
    expect(page.locator("#stepn")).to_have_text("Step 2 of 2")
    page.check(".wave[data-i='1']")
    expect(page.locator("#badgetext")).to_have_text("Looks good", timeout=8000)
    page.wait_for_timeout(500)
    page.screenshot(path=str(SHOTS / "01_guide.png"))

    # Code tab: starter is loaded, type a sketch with a typo, compile
    page.click("[data-tab=code]")
    page.wait_for_function("window.ohmniEditor !== undefined", timeout=15000)
    kind = page.evaluate("window.ohmniEditor")
    if kind == "monaco":
        expect(page.locator(".monaco-editor")).to_be_visible()
        page.evaluate("s => monaco.editor.getModels()[0].setValue(s)", BAD_SKETCH)
        page.locator(".monaco-editor .view-lines").click()
        page.keyboard.press("ControlOrMeta+End")
        page.keyboard.type("// typed by the smoke test")
        expect(page.locator(".monaco-editor .view-lines")).to_contain_text("typed by the smoke test")
    else:
        page.fill("#codearea", BAD_SKETCH)
    page.click("#compile")
    expect(page.locator("#buildstatus")).to_contain_text("1 error", timeout=20000)
    expect(page.locator(".problem")).to_contain_text("Line 6")
    assert page.evaluate("window.ohmniMarkers[0].line") == 6
    if kind == "monaco":
        assert page.evaluate("monaco.editor.getModelMarkers({}).length") == 1
        expect(page.locator(".errglyph")).to_have_count(1)
    expect(page.locator("#codelog")).to_contain_text("Hint: look at line 6", timeout=10000)
    expect(page.locator("#compileout")).to_contain_text("'pinMod' was not declared")
    page.wait_for_timeout(400)
    page.screenshot(path=str(SHOTS / "02_code_compile.png"))

    # ask a typed question from the Code tab
    page.fill("#codeq", "Why is my LED not blinking?")
    page.press("#codeq", "Enter")
    expect(page.locator("#codelog .msg").last).to_contain_text("Mock Gemma", timeout=10000)

    # upload in SIM mode never touches hardware
    fixed = BAD_SKETCH.replace("pinMod(", "pinMode(")
    if kind == "monaco":
        page.evaluate("s => monaco.editor.getModels()[0].setValue(s)", fixed)
    else:
        page.fill("#codearea", fixed)
    page.click("#upload")
    expect(page.locator("#buildstatus")).to_contain_text("Uploaded (simulated)", timeout=20000)

    # fault state: divider step 2 with R2 lifted out of GND, then ask a question
    page.click("[data-tab=guide]")
    page.select_option("#lab", "divider")
    expect(page.locator("#labtitle")).to_have_text("Voltage Divider")
    page.click("#next")
    page.uncheck(".wave[data-i='1']")
    page.request.post(server + "/api/sim", data={"v": [3300, 3300, 0, 0], "ptt": 0, "wave": []})
    expect(page.locator("#badgetext")).to_have_text("Something is off", timeout=8000)
    expect(page.locator("#hint")).to_contain_text("R2 is not reaching GND")
    page.fill("#typed", "Is this right?")
    page.press("#typed", "Enter")
    expect(page.locator("#log .msg").last).to_contain_text("Mock Gemma", timeout=10000)
    page.wait_for_timeout(400)
    page.screenshot(path=str(SHOTS / "03_fault.png"))

    for name in ["01_guide.png", "02_code_compile.png", "03_fault.png"]:
        assert (SHOTS / name).stat().st_size > 10_000
