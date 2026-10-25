"""Instrument identification by JSON stream, target port selection."""
import app

UNO = ['{"t":100,"v":[3301,1650,0,2],"ptt":0}'] * 3
ESP32_BOOT = ["ets Jun  8 2016 00:22:57", "rst:0x1 (POWERON_RESET),boot:0x13 (SPI_FAST_FLASH_BOOT)",
              "configsip: 0, SPIWP:0xee", "load:0x3fff0030,len:1184", "entry 0x400805e4"]


def test_parse_probe_line():
    assert app.parse_probe_line('{"t":1,"v":[1,2,3,4],"ptt":1}') == {"v": [1, 2, 3, 4], "ptt": 1}
    assert app.parse_probe_line("tick") is None
    assert app.parse_probe_line('{"v":[1,2]}') is None
    assert app.parse_probe_line('{"v":[true,2,3,4]}') is None
    assert app.parse_probe_line('{"v":"1,2,3,4"}') is None
    assert app.parse_probe_line("{not json") is None


def test_looks_like_instrument():
    assert app.looks_like_instrument(["", "garbage"] + UNO)
    assert not app.looks_like_instrument(ESP32_BOOT)
    assert not app.looks_like_instrument(["tick", "button: 1", "{}"] * 5)
    assert not app.looks_like_instrument(UNO[:2])


def test_candidate_ports():
    ports = [("/dev/cu.Bluetooth-Incoming-Port", "n/a"), ("/dev/cu.usbmodem1101", "Arduino Uno"),
             ("/dev/cu.usbserial-0001", "CP2102 USB to UART"), ("/dev/cu.debug-console", "n/a")]
    assert app.candidate_ports(ports) == ["/dev/cu.usbmodem1101", "/dev/cu.usbserial-0001"]
    assert app.candidate_ports(ports, exclude={"/dev/cu.usbserial-0001"}) == ["/dev/cu.usbmodem1101"]


def test_auto_target():
    assert app.auto_target(["/a", "/b"], "/a", None) == "/b"
    assert app.auto_target(["/a", "/b", "/c"], "/a", None) is None
    assert app.auto_target(["/a", "/b"], "/a", "/x") == "/x"


class FakeSerial:
    def __init__(self, lines):
        self.lines = [ln.encode() + b"\n" for ln in lines]
        self.closed = False

    def readline(self):
        return self.lines.pop(0) if self.lines else b""

    def close(self):
        self.closed = True


def test_sniff_identifies_uno_by_stream(monkeypatch):
    streams = {"/dev/uno": FakeSerial(["boot"] + UNO), "/dev/esp": FakeSerial(ESP32_BOOT)}
    monkeypatch.setattr(app, "open_port", lambda dev, baud, timeout=0.3: streams[dev])
    monkeypatch.setattr(app, "SNIFF_S", 0.2)
    assert app.sniff_instrument("/dev/uno") is streams["/dev/uno"]
    assert app.sniff_instrument("/dev/esp") is None and streams["/dev/esp"].closed


def test_ports_endpoint_and_target(client, monkeypatch):
    monkeypatch.setattr(app, "system_ports", lambda: [("/dev/uno", "Arduino Uno"), ("/dev/esp", "CP2102")])
    app.state["instrument_port"] = "/dev/uno"
    r = client.post("/api/target", json={"port": "/dev/uno"}).json()
    assert "instrument" in r["error"]
    assert client.post("/api/target", json={"port": "/dev/esp", "baud": 9600}).json()["ok"]
    assert "error" in client.post("/api/target", json={"fqbn": "foo:bar:baz"}).json()
    assert "error" in client.post("/api/target", json={"baud": 1234}).json()
    data = client.get("/api/ports").json()
    roles = {p["device"]: p["role"] for p in data["ports"]}
    assert roles == {"/dev/uno": "instrument", "/dev/esp": "target"}
    assert data["target"] == {"port": "/dev/esp", "fqbn": "esp32:esp32:esp32", "baud": 9600}
