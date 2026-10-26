"""Compile every starter sketch for the ESP32 target and the Uno instrument firmware with real arduino-cli.

Run from the repo root:  python scripts/compile_starters.py
Cores that are not installed are reported and skipped, never faked.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
from labs import LABS  # noqa: E402

cli = shutil.which("arduino-cli")
if not cli:
    sys.exit("arduino-cli is not installed. Install it with: brew install arduino-cli")
cores = subprocess.run([cli, "core", "list"], capture_output=True, text=True).stdout
print(f"$ arduino-cli version\n{subprocess.run([cli, 'version'], capture_output=True, text=True).stdout.strip()}")
print(f"$ arduino-cli core list\n{cores.strip()}\n")

jobs = []
for lab_id, lab in LABS.items():
    for i, step in enumerate(lab["steps"]):
        if step.get("starter"):
            jobs.append((f"starter {lab_id} step {i + 1}", "esp32:esp32:esp32", step["starter"]))
jobs.append(("firmware/ohmni_probe_uno", "arduino:avr:uno", (ROOT / "firmware/ohmni_probe_uno/ohmni_probe_uno.ino").read_text()))

failed = 0
with tempfile.TemporaryDirectory(prefix="ohmni-check-") as tmp:
    for n, (name, fqbn, code) in enumerate(jobs):
        core = ":".join(fqbn.split(":")[:2])
        if core not in cores:
            print(f"SKIP  {name} for {fqbn}: core {core} is not installed (arduino-cli core install {core})")
            continue
        sk = Path(tmp) / f"s{n}" / f"s{n}"
        sk.mkdir(parents=True)
        (sk / f"s{n}.ino").write_text(code)
        r = subprocess.run([cli, "compile", "--no-color", "--fqbn", fqbn, str(sk)], capture_output=True, text=True, timeout=600)
        tail = [ln for ln in (r.stdout + r.stderr).splitlines() if ln.strip()][-3:]
        status = "PASS" if r.returncode == 0 else "FAIL"
        failed += r.returncode != 0
        print(f"{status}  {name} for {fqbn}")
        for ln in tail:
            print(f"      {ln}")
print(f"\n{len(jobs) - failed} of {len(jobs)} compiled, {failed} failed")
sys.exit(1 if failed else 0)
