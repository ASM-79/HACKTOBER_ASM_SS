"""arduino-cli wrapper for Ohmni: compile and upload student sketches for the target board.

Student code is only ever compiled here, inside a per-session temp sketch folder, and uploaded to the
target board. It is never executed on the laptop. Compiles are capped at COMPILE_TIMEOUT seconds.

Set ARDUINO_CLI to override the binary (it is split like a shell command, so tests can point it at a fake).
"""
import atexit
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Iterator, Optional

COMPILE_TIMEOUT = 120  # seconds
MAX_CODE = 100_000     # characters
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
CORES = {"esp32:esp32:esp32": "esp32:esp32", "arduino:avr:uno": "arduino:avr"}
INSTALL_CLI = "brew install arduino-cli"
INSTALL_ROSETTA = "softwareupdate --install-rosetta --agree-to-license"


def install_core_cmd(core: str) -> str:
    return f"arduino-cli core update-index && arduino-cli core install {core}"


def cli_cmd() -> Optional[list]:
    env = os.getenv("ARDUINO_CLI")
    if env:
        return shlex.split(env)
    path = shutil.which("arduino-cli")
    return [path] if path else None


def installed_cores(cli: list) -> set:
    r = subprocess.run(cli + ["core", "list", "--format", "json"], capture_output=True, text=True, timeout=30)
    data = json.loads(r.stdout or "{}")
    plats = data.get("platforms") if isinstance(data, dict) else data  # 1.x wraps the list, 0.x does not
    return {p.get("id") for p in plats or [] if isinstance(p, dict)}


def check_tools(fqbn: str) -> Optional[dict]:
    """None when arduino-cli and the core for fqbn are ready, else what is missing and the exact install command."""
    cli = cli_cmd()
    if not cli:
        return {"missing": "arduino-cli", "message": "arduino-cli is not installed.", "install": INSTALL_CLI}
    core = CORES[fqbn]
    try:
        cores = installed_cores(cli)
    except Exception as e:
        return {"missing": "arduino-cli", "message": f"arduino-cli did not run ({e}).", "install": INSTALL_CLI}
    if core not in cores:
        return {"missing": core, "message": f"The {core} core for {fqbn} is not installed.",
                "install": install_core_cmd(core)}
    return None


# ---------------- diagnostics ----------------

DIAG_RE = re.compile(r"(?:^|\s)(?P<file>[^\s:]*sketch\.ino):(?P<line>\d+):(?:(?P<col>\d+):)?\s*"
                     r"(?P<sev>fatal error|error|warning):\s*(?P<msg>.+)$")


def parse_diagnostics(output: str) -> list:
    """Turn gcc output into editor markers: [{"line", "col", "severity", "message"}]."""
    out, seen = [], set()
    for raw in output.splitlines():
        m = DIAG_RE.search(raw.strip())
        if not m:
            continue
        sev = "warning" if m["sev"] == "warning" else "error"
        item = {"line": int(m["line"]), "col": int(m["col"] or 1), "severity": sev, "message": m["msg"].strip()}
        key = (item["line"], item["col"], item["message"])
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def fallback_error(output: str) -> Optional[dict]:
    """A failed build with no line info (linker errors, bad board): surface the first error line."""
    for raw in output.splitlines():
        if "error" in raw.lower():
            return {"line": 1, "col": 1, "severity": "error", "message": raw.strip()[:300]}
    return None


# ---------------- sessions ----------------

_sessions: dict = {}
_sessions_lock = threading.Lock()
_build_lock = threading.Lock()


def session_dir(sid: str) -> Path:
    if not SESSION_RE.match(sid or ""):
        raise ValueError("bad session id")
    with _sessions_lock:
        if sid not in _sessions:
            _sessions[sid] = tempfile.mkdtemp(prefix="ohmni-")
        return Path(_sessions[sid])


def write_sketch(sid: str, code: str):
    root = session_dir(sid)
    sketch = root / "sketch"
    build = root / "build"
    sketch.mkdir(exist_ok=True)
    build.mkdir(exist_ok=True)
    (sketch / "sketch.ino").write_text(code)
    return sketch, build


@atexit.register
def _cleanup():
    for d in _sessions.values():
        shutil.rmtree(d, ignore_errors=True)


# ---------------- running arduino-cli ----------------

def run_streaming(cmd: list, timeout: float, cwd: Optional[str] = None) -> Iterator[tuple]:
    """Yield ("out", line) for each output line, then ("exit", returncode, timed_out)."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                         cwd=cwd, errors="replace")
    timed_out = threading.Event()

    def kill():
        timed_out.set()
        p.kill()

    timer = threading.Timer(timeout, kill)
    timer.start()
    try:
        for line in p.stdout:
            yield ("out", line.rstrip("\n"))
        p.wait()
    finally:
        timer.cancel()
        if p.poll() is None:
            p.kill()
            p.wait()
    yield ("exit", p.returncode, timed_out.is_set())


def _validate(sid: str, code: str, fqbn: str) -> Optional[str]:
    if fqbn not in CORES:
        return f"Unknown board {fqbn}."
    if not SESSION_RE.match(sid or ""):
        return "Bad session id."
    if len(code) > MAX_CODE:
        return f"Sketch is too long ({len(code)} characters, limit {MAX_CODE})."
    return None


def compile_events(sid: str, code: str, fqbn: str, timeout: float = None) -> Iterator[dict]:
    """Stream a compile as events: start, out*, result."""
    timeout = timeout or COMPILE_TIMEOUT
    err = _validate(sid, code, fqbn)
    if err:
        yield {"type": "result", "ok": False, "errors": [], "message": err}
        return
    missing = check_tools(fqbn)
    if missing:
        yield {"type": "result", "ok": False, "errors": [], **missing}
        return
    if not _build_lock.acquire(blocking=False):
        yield {"type": "result", "ok": False, "errors": [], "message": "Another compile is running. Try again in a moment."}
        return
    try:
        sketch, build = write_sketch(sid, code)
        cmd = cli_cmd() + ["compile", "--no-color", "--fqbn", fqbn, "--build-path", str(build), str(sketch)]
        yield {"type": "start", "cmd": f"arduino-cli compile --fqbn {fqbn} sketch"}
        lines, t0 = [], time.time()
        prefixes = {str(sketch) + os.sep, str(sketch.resolve()) + os.sep}
        for ev in run_streaming(cmd, timeout, cwd=str(sketch.parent)):
            if ev[0] == "out":
                text = ev[1]
                for pre in prefixes:  # show "sketch.ino:6:5: error" instead of the temp folder path
                    text = text.replace(pre, "")
                lines.append(text)
                yield {"type": "out", "text": text}
            else:
                code_rc, timed_out = ev[1], ev[2]
        output = "\n".join(lines)
        errors = parse_diagnostics(output)
        ok = code_rc == 0 and not timed_out
        if not ok and not any(e["severity"] == "error" for e in errors):
            fb = fallback_error(output)
            if fb:
                errors.append(fb)
        res = {"type": "result", "ok": ok, "errors": errors, "seconds": round(time.time() - t0, 1)}
        if "bad CPU type in executable" in output:
            # Arduino's bundled ctags is an Intel binary; Apple Silicon Macs need Rosetta 2 to run it.
            res.update(missing="rosetta", message="An Arduino build tool needs Rosetta 2 on this Apple Silicon Mac.",
                       install=INSTALL_ROSETTA)
        if timed_out:
            res["message"] = f"Compile stopped after {timeout:.0f} s."
        yield res
    finally:
        _build_lock.release()


def upload_events(sid: str, code: str, fqbn: str, port: Optional[str], sim: bool,
                  pause: Callable[[], bool] = lambda: True, resume: Callable[[], None] = lambda: None,
                  timeout: float = None) -> Iterator[dict]:
    """Compile, then upload to the target board. In SIM mode nothing touches hardware."""
    timeout = timeout or COMPILE_TIMEOUT
    if not port:
        yield {"type": "result", "ok": False, "errors": [], "stage": "upload",
               "message": "Pick the target board port in the Code tab first."}
        return
    compiled = None
    for ev in compile_events(sid, code, fqbn, timeout):
        if ev["type"] == "result":
            compiled = ev
        else:
            yield ev
    if not compiled["ok"]:
        yield dict(compiled, stage="compile")
        return
    yield {"type": "out", "text": f"Compiled in {compiled.get('seconds', 0)} s. Uploading to {port}."}
    if sim:
        yield {"type": "out", "text": f"[sim] SIM=1: upload skipped, no hardware touched. Pretending {port} runs the sketch."}
        yield {"type": "result", "ok": True, "errors": compiled["errors"], "stage": "upload", "sim": True}
        return
    sketch, build = write_sketch(sid, code)
    if not pause():
        yield {"type": "result", "ok": False, "errors": [], "stage": "upload",
               "message": f"The serial monitor did not release {port}. Try again."}
        resume()
        return
    try:
        cmd = cli_cmd() + ["upload", "--no-color", "-p", port, "--fqbn", fqbn, "--input-dir", str(build), str(sketch)]
        yield {"type": "start", "cmd": f"arduino-cli upload -p {port} --fqbn {fqbn} sketch"}
        rc, timed_out = 1, False
        for ev in run_streaming(cmd, timeout, cwd=str(sketch.parent)):
            if ev[0] == "out":
                yield {"type": "out", "text": ev[1]}
            else:
                rc, timed_out = ev[1], ev[2]
    finally:
        resume()
    res = {"type": "result", "ok": rc == 0 and not timed_out, "errors": compiled["errors"], "stage": "upload"}
    if rc != 0:
        res["message"] = (f"Upload to {port} failed. Is it the right port? On many ESP32 boards you hold BOOT "
                          "while the upload starts.")
    yield res
