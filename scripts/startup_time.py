"""Час від `docker compose up` до першого 200 на /healthz: образи зібрані, томи порожні, 3 повтори."""
import sys as _sys
_sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows за замовчуванням cp1252
import json
import statistics
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = next((l.split("=", 1)[1].strip() for l in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
             if l.startswith("LB_PORT=")), "8080")


def dc(*args):
    subprocess.run(["docker", "compose", *args], cwd=ROOT, check=True, capture_output=True)


dc("build")
times = []
for i in range(3):
    dc("down", "-v")
    t = time.perf_counter()
    dc("up", "-d")
    while True:
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{PORT}/healthz", timeout=2).status == 200:
                break
        except Exception:
            time.sleep(0.05)
    times.append(time.perf_counter() - t)
    print(f"повтор {i + 1}: {times[-1]:.2f} с")
print(f"медіана: {statistics.median(times):.2f} с")
(ROOT / "results" / "startup_time.json").write_text(json.dumps({"runs_s": times,
                                                                 "median_s": statistics.median(times)}))
