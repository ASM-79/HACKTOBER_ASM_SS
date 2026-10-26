"""Send one photo-free question to Gemma 4 through Ollama and print the reply and latency.

Run:  python scripts/check_gemma.py            (uses OLLAMA_MODEL, default gemma4:e4b)
"""
import os
import sys
import time

import requests

URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e4b")
QUESTION = ("Lab: Blink and Measure. The sketch calls digitalWrite(13, HIGH) but probe P2 on the GPIO 13 row reads "
            "0.00 V. Last compile succeeded. In one sentence, what should the student check first?")

t0 = time.time()
try:
    r = requests.post(f"{URL}/api/chat", timeout=300, json={
        "model": MODEL, "stream": False, "options": {"temperature": 0.3},
        "messages": [{"role": "system", "content": "You are Ohmni, a friendly electronics lab TA. Answer in at most two sentences."},
                     {"role": "user", "content": QUESTION}]})
    r.raise_for_status()
except Exception as e:
    sys.exit(f"Could not reach {MODEL} at {URL}: {e}")
print(f"model:   {MODEL}")
print(f"reply:   {r.json()['message']['content'].strip()}")
print(f"latency: {time.time() - t0:.1f} s")
