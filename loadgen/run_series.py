"""Оркестрація вимірювань
"""
import sys as _sys
_sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows за замовчуванням cp1252
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gen  # noqa: E402
import seed  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
RATES = (10, 100, 500)
REPEATS = 3
PATH = "/donor-registry/{id}"


def env_port():
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("LB_PORT="):
            return line.split("=", 1)[1].strip()
    return "8080"


BASE = f"http://127.0.0.1:{env_port()}"


def compose(*args, replicas=None, capture=False):
    env = dict(os.environ)
    if replicas is not None:
        env["WEB_REPLICAS"] = str(replicas)
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, env=env, check=True,
                          capture_output=capture, text=True)


def wait_health(timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if urllib.request.urlopen(f"{BASE}/healthz", timeout=2).status == 200:
                return
        except Exception:
            time.sleep(0.2)
    raise SystemExit("стенд не став готовим")


def bring_up(instances):
    compose("up", "-d", replicas=instances)
    compose("restart", "lb")  # nginx резолвить web лише на старті: після зміни реплік перезапуск
    wait_health()
    time.sleep(2)


class StatsSampler:
    """Пише `docker stats` кожні ~2 с у CSV, поки триває прогін (щоб бачити CPU під навантаженням)."""

    def __init__(self, path):
        self.path, self.stop = Path(path), threading.Event()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("t,name,cpu_percent,mem\n")
            t0 = time.time()
            while not self.stop.is_set():
                out = subprocess.run(
                    ["docker", "stats", "--no-stream", "--format", "{{.Name}},{{.CPUPerc}},{{.MemUsage}}"],
                    capture_output=True, text=True).stdout
                for line in out.strip().splitlines():
                    f.write(f"{time.time() - t0:.1f},{line}\n")
                f.flush()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join()


def cmd_selfcheck(a):
    """Генератор проти ендпоінта, який нічого не робить: якщо тут не дотягує, межа в генераторі."""
    bring_up(1)
    for rate in a.rates:
        s = gen.run_open(BASE, "/_null", [], rate, 5, 15, out=RESULTS / "selfcheck" / f"rate{rate}")
        print(f"/_null  задано {rate}  досягнуто {s['achieved_rps']:.1f}  p95 {s['p95_ms']:.2f} мс  "
              f"non-2xx {s['non2xx_share']:.3f}")


def cmd_series(a):
    bring_up(a.instances)
    ids = seed.ensure_ids(BASE, 100)
    outdir = RESULTS / f"{a.instances}x"
    for rate in a.rates:
        for rep in range(1, REPEATS + 1):
            if a.stats:
                sampler = StatsSampler(RESULTS / "stats" / f"{a.instances}x_rate{rate}_r{rep}.csv")
                sampler.__enter__()
            s = gen.run_open(BASE, PATH, ids, rate, a.warmup, a.duration,
                             out=outdir / f"rate{rate}_r{rep}")
            if a.stats:
                sampler.__exit__(None, None, None)
            print(f"{a.instances} інст. задано {rate} повтор {rep}: досягнуто {s['achieved_rps']:.1f} "
                  f"p50 {s['p50_ms']:.1f} p95 {s['p95_ms']:.1f} p99 {s['p99_ms']:.1f} "
                  f"non-2xx {s['non2xx_share']:.3f}")
            time.sleep(5)


def cmd_distribution(a):
    bring_up(2)
    ids = seed.ensure_ids(BASE, 100)
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    counts = Counter()
    for i in range(a.n):
        r = urllib.request.urlopen(f"{BASE}/donor-registry/{ids[i % len(ids)]}", timeout=5)
        counts[r.headers["X-Instance"]] += 1
    logs = compose("logs", "lb", "--since", since, capture=True).stdout
    upstream = Counter(w.split("=", 1)[1] for l in logs.splitlines() for w in l.split()
                       if w.startswith("upstream=") and w != "upstream=-")
    out = {"requests": a.n, "by_X-Instance_header": counts, "by_nginx_upstream_addr": upstream}
    (RESULTS / "distribution.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


def cmd_closed(a):
    import statistics
    runs = sorted((RESULTS / "1x").glob(f"rate{a.rate}_r*.json"))
    if not runs:
        raise SystemExit("спершу зніміть серію на одному інстансі")
    conc = round(statistics.median(json.loads(p.read_text())["L_mean"] for p in runs))
    print(f"рівень {a.rate}, у відкритому контурі L_mean (медіана) = {conc} -> одночасних запитів {conc}")
    bring_up(1)
    ids = seed.ensure_ids(BASE, 100)
    for rep in range(1, REPEATS + 1):
        s = gen.run_closed(BASE, PATH, ids, conc, a.warmup, a.duration,
                           out=RESULTS / "closed" / f"rate{a.rate}_c{conc}_r{rep}")
        print(f"закритий, {conc} одночасних, повтор {rep}: досягнуто {s['achieved_rps']:.1f} "
              f"p95 {s['p95_ms']:.1f} мс")
        time.sleep(5)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("selfcheck"); p.add_argument("--rates", type=int, nargs="+", default=[500, 1000])
    p.set_defaults(fn=cmd_selfcheck)
    p = sub.add_parser("series"); p.add_argument("--instances", type=int, required=True)
    p.add_argument("--rates", type=int, nargs="+", default=list(RATES))
    p.add_argument("--stats", action="store_true", help="писати docker stats у results/stats/")
    p.add_argument("--warmup", type=float, default=10); p.add_argument("--duration", type=float, default=30)
    p.set_defaults(fn=cmd_series)
    p = sub.add_parser("distribution"); p.add_argument("--n", type=int, default=300)
    p.set_defaults(fn=cmd_distribution)
    p = sub.add_parser("closed"); p.add_argument("--rate", type=int, default=500)
    p.add_argument("--warmup", type=float, default=10); p.add_argument("--duration", type=float, default=30)
    p.set_defaults(fn=cmd_closed)
    a = ap.parse_args()
    a.fn(a)
