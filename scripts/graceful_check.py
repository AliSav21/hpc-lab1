"""Крок 3: перевірка коректної зупинки під навантаженням на двох екземплярах.

Під постійним навантаженням зупиняє один контейнер web, перевіряє код завершення 0
і відсутність відповідей не 2xx та помилок зʼєднання в вікні ±10 с навколо зупинки.
"""
import sys as _sys
_sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows: cp1252
import argparse
import csv
import json
import os
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in _sys.path:
    _sys.path.insert(0, str(ROOT))

AROUND = 10.0   # вікно перевірки навколо зупинки, с


def sh(*args, capture=True, check=True):
    return subprocess.run(args, cwd=ROOT, capture_output=capture, text=True,
                          encoding="utf-8", check=check)


def web_containers():
    out = sh("docker", "compose", "ps", "-q", "web").stdout.split()
    return [c for c in out if c]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--rate", type=float, default=200.0, help="постійна інтенсивність, rps")
    ap.add_argument("--seconds", type=float, default=60.0, help="тривалість навантаження")
    ap.add_argument("--stop-at", type=float, default=30.0, help="коли зупиняти контейнер, с")
    ap.add_argument("--out", default="results/lab2/graceful")
    a = ap.parse_args()

    # постійний профіль: прогрів 5 с і одна довга «сходинка» без приросту
    os.environ.update({
        "STEP_WARMUP_S": "5", "STEP_WARMUP_RATE": str(a.rate),
        "STEP_DURATION_S": str(a.seconds), "STEP_TRANSIENT_S": "5",
        "STEP_RATE0": str(a.rate), "STEP_RATE_STEP": "0", "STEP_MAX_STEPS": "1",
    })
    from loadgen import step
    from loadgen.seed import ensure_ids

    base = step.default_base()
    cont = web_containers()
    if len(cont) < 2:
        raise SystemExit(f"потрібні два екземпляри web, запущено {len(cont)}: "
                         f"docker compose up -d --scale web=2 --wait")
    victim = cont[0]
    ids = ensure_ids(base, 100)
    out = ROOT / a.out
    result = {}

    def stopper():
        time.sleep(step.START_DELAY + 1.0 + a.stop_at)
        result["stop_wall"] = time.time()
        print(f"docker stop {victim[:12]}", flush=True)
        t0 = time.time()
        sh("docker", "stop", victim)
        result["stop_took_s"] = time.time() - t0
        result["exit_code"] = int(sh("docker", "inspect", "--format",
                                     "{{.State.ExitCode}}", victim).stdout.strip())
        print(f"код завершення: {result['exit_code']}, "
              f"зупинка тривала {result['stop_took_s']:.1f} с", flush=True)

    t = threading.Thread(target=stopper, daemon=True)
    t.start()
    summary = step.run(base, "/donor-registry/{id}", ids, out, label="коректна зупинка")
    t.join(timeout=60)

    # вікно ±10 с навколо зупинки у секундах від початку прогону
    rel = result.get("stop_wall", 0) - summary["start_wall"]
    lo, hi = rel - AROUND, rel + AROUND
    bad = {"non2xx": 0, "connerr": 0, "seconds": []}
    with out.with_suffix(".csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            sec = float(r["t_s"])
            if lo <= sec <= hi:
                n2, ce = int(r["non2xx"]), int(r["connerr"])
                bad["non2xx"] += n2
                bad["connerr"] += ce
                if n2 or ce:
                    bad["seconds"].append({"t_s": sec, "non2xx": n2, "connerr": ce})

    verdict = {
        "victim": victim,
        "stopped_at_run_s": rel,
        "window_run_s": [lo, hi],
        "exit_code": result.get("exit_code"),
        "stop_took_s": result.get("stop_took_s"),
        "non2xx_in_window": bad["non2xx"],
        "connerr_in_window": bad["connerr"],
        "bad_seconds": bad["seconds"],
        "passed": result.get("exit_code") == 0 and bad["non2xx"] == 0 and bad["connerr"] == 0,
        "achieved_rps": summary["X"],
        "target_rps": a.rate,
    }
    out.with_name(out.name + "_verdict.json").write_text(
        json.dumps(verdict, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(verdict, indent=2, ensure_ascii=False))
    print("ПРОЙДЕНО" if verdict["passed"] else "НЕ ПРОЙДЕНО")
    print(f"-> {out.with_name(out.name + '_verdict.json')}")


if __name__ == "__main__":
    main()
