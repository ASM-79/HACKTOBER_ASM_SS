"""Stand-in for arduino-cli in tests and the smoke test. Never touches hardware, never runs sketch code.

Behavior is driven by markers in the sketch text: pinMod( gives a compile error on that line,
SLEEP_FOREVER hangs (for timeout tests), ROSETTA prints the Apple Silicon ctags failure.
"""
import json
import os
import sys
import time

args = [a for a in sys.argv[1:] if a != "--no-color"]
log = os.environ.get("FAKE_CLI_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps(args) + "\n")

if args[:2] == ["core", "list"]:
    cores = os.environ.get("FAKE_CORES", "esp32:esp32,arduino:avr").split(",")
    print(json.dumps({"platforms": [{"id": c} for c in cores if c]}))
    sys.exit(0)

if args and args[0] == "compile":
    sketch = args[-1]
    code = open(os.path.join(sketch, "sketch.ino")).read()
    if "SLEEP_FOREVER" in code:
        time.sleep(30)
    if "ROSETTA" in code:
        print("Error during build: fork/exec /x/ctags: bad CPU type in executable")
        sys.exit(1)
    bad = False
    for n, line in enumerate(code.splitlines(), 1):
        col = line.find("pinMod(")
        if col >= 0:
            bad = True
            print(f"{sketch}/sketch.ino:{n}:{col + 1}: error: 'pinMod' was not declared in this scope; did you mean 'pinMode'?")
            print(f"    {n} |   {line.strip()}")
    if bad:
        print("exit status 1")
        print("Compilation error: exit status 1")
        sys.exit(1)
    print("Sketch uses 289333 bytes (22%) of program storage space. Maximum is 1310720 bytes.")
    print("Global variables use 20412 bytes (6%) of dynamic memory.")
    sys.exit(0)

if args and args[0] == "upload":
    print("Writing at 0x00010000... (100 %)")
    print("Hard resetting via RTS pin...")
    sys.exit(0)

print(f"fake arduino-cli: unsupported {args}")
sys.exit(2)
