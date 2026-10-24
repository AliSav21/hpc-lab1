"""Один ступінчастий прогін на заданій кількості екземплярів: масштабування стенду,
прогін генератора, збір метрик Prometheus за сходинками і знімків docker stats.
"""
import sys as _sys
_sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows: cp1252
import argparse
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from loadgen import prom, step
from loadgen.seed import ensure_ids

ROOT = Path(__file__).resolve().parent.parent
STATS_EVERY = 2.0


def sh(*args, check=True, capture=False):
    print("$ " + " ".join(args), flush=True)
    return subprocess.run(args, cwd=ROOT, check=check,
                          capture_output=capture, text=True, encoding="utf-8")


def scale(n):
    """Піднімає стенд на n екземплярах web і перезапускає балансувальник,
    щоб nginx отримав нові адреси екземплярів."""
    sh("docker", "compose", "up", "-d", "--scale", f"web={n}", "--wait")
    sh("docker", "compose", "restart", "lb")


def wait_ready(base, n, timeout=120.0):
    """Чекає, поки /healthz віддає 200 і через балансувальник видно n різних екземплярів.

    Docker Desktop іноді втрачає проброс опублікованого порту на хост: порт слухає, але
    зʼєднання обриваються. Якщо за 30 с не прийшло жодної відповіді, балансувальник
    пересоздається, що відновлює проброс.
    """
    deadline = time.time() + timeout
    pods = set()
    recreated = False
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/healthz", timeout=3) as r:
                if r.status == 200:
                    pods.add(r.headers.get("X-Pod", ""))
        except Exception:
            pass
        if len(pods) >= n:
            return sorted(pods)
        if not pods and not recreated and time.time() > deadline - timeout + 30:
            print("порт не відповідає: пересоздаю балансувальник", flush=True)
            sh("docker", "compose", "up", "-d", "--force-recreate", "lb", check=False)
            recreated = True
        time.sleep(0.3)
    raise RuntimeError(f"видно лише {len(pods)} з {n} екземплярів: {sorted(pods)}")


class StatsSampler(threading.Thread):
    """Періодичні знімки docker stats під час прогону."""

    def __init__(self):
        super().__init__(daemon=True)
        self.samples = []
        self._done = threading.Event()   # не _stop: таке імʼя вже має Thread

    def run(self):
        while not self._done.is_set():
            t = time.time()
            try:
                r = subprocess.run(
                    ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
                    cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=20)
                rows = [json.loads(x) for x in r.stdout.splitlines() if x.strip()]
            except Exception as e:
                rows = [{"error": str(e)}]
            self.samples.append({"t": t, "rows": rows})
            self._done.wait(STATS_EVERY)

    def stop(self):
        self._done.set()
        self.join(timeout=25)


class HostSampler(threading.Thread):
    """CPU хоста і сумарний CPU процесів генератора (крок 11).

    Один довгий процес PowerShell пише рядки
    `unix;усього_%;секунди_CPU_python;байти_памʼяті_python`, щоб не платити за запуск
    оболонки на кожен знімок.
    """

    SCRIPT = (
        "$prev=$null; while($true){"
        "$t=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds();"   # -UFormat %s дає локальний час
        "$cpu=(Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor "
        "| Where-Object {$_.Name -eq '_Total'}).PercentProcessorTime;"
        "$p=Get-Process python -ErrorAction SilentlyContinue;"
        "$py=($p | Measure-Object -Property CPU -Sum).Sum;"
        "$mem=($p | Measure-Object -Property WorkingSet64 -Sum).Sum;"
        "Write-Output \"$t;$cpu;$py;$mem\"; Start-Sleep -Milliseconds 2000}"
    )

    def __init__(self):
        super().__init__(daemon=True)
        self.samples = []
        self.proc = None

    def run(self):
        try:
            self.proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", self.SCRIPT],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                encoding="utf-8", bufsize=1)
            for line in self.proc.stdout:
                parts = line.strip().split(";")
                if len(parts) == 4 and parts[0].isdigit():
                    self.samples.append({"t": float(parts[0]),
                                         "host_cpu_pct": float(parts[1] or 0),
                                         "python_cpu_s": float(parts[2] or 0),
                                         "python_mem_bytes": float(parts[3] or 0)})
        except Exception:
            pass

    def stop(self):
        if self.proc:
            self.proc.terminate()
        self.join(timeout=10)


def step_metrics(url, start_wall, steps):
    """Метрики Prometheus за вимірювальним вікном кожної сходинки (останні 15 с)."""
    out = []
    for m in steps:
        begin, end = step.step_window(m["step"])
        w0 = start_wall + begin + step.TRANSIENT_S
        w1 = start_wall + end
        win = f"{int(w1 - w0)}s"
        try:
            row = {
                "step": m["step"], "target_rps": m["target_rps"],
                "window_unix": [w0, w1],
                "server_p99": prom.server_quantile(
                    url, "http_request_duration_seconds", w0, w1, grouping='{job="web"}'),
                "pool_wait_p99": prom.server_quantile(
                    url, "db_pool_wait_seconds", w0, w1, grouping='{job="web"}'),
                "instances_up": prom.scalar(url, 'count(up{job="web"} == 1)', w1),
                # job="web": без фільтра в середнє потрапляє і сам Prometheus
                "cpu_per_instance": prom.scalar(
                    url, f'avg(increase(process_cpu_seconds_total{{job="web"}}[{win}]))', w1),
                "rps_server_total": prom.scalar(
                    url, f'sum(rate(http_requests_total{{code=~"2.."}}[{win}]))', w1),
                "in_flight_per_instance": prom.scalar(
                    url, f'avg(avg_over_time(http_requests_in_flight{{job="web"}}[{win}]))', w1),
                "pool_busy_per_instance": prom.scalar(
                    url, f'avg(avg_over_time(db_pool_connections_busy{{job="web"}}[{win}]))', w1),
                "pool_busy_max": prom.scalar(
                    url, f'max(max_over_time(db_pool_connections_busy{{job="web"}}[{win}]))', w1),
            }
            if row["cpu_per_instance"] is not None:
                row["cpu_cores_per_instance"] = row["cpu_per_instance"] / (w1 - w0)
        except Exception as e:
            row = {"step": m["step"], "error": repr(e)}
        out.append(row)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--instances", type=int, required=True)
    ap.add_argument("--max-rate", type=float, default=None, help="межа генератора G")
    ap.add_argument("--path", default="/donor-registry/{id}")
    ap.add_argument("--out-dir", default="results/lab2")
    ap.add_argument("--name", default=None, help="імʼя файлів; типово N<instances>")
    ap.add_argument("--procs", type=int, default=None)
    ap.add_argument("--base", default=None)
    ap.add_argument("--prom", default=None)
    ap.add_argument("--no-scale", action="store_true", help="не піднімати стенд заново")
    a = ap.parse_args()

    base = a.base or step.default_base()
    url = a.prom or prom.default_url()
    name = a.name or f"N{a.instances}"
    out = ROOT / a.out_dir / name

    if not a.no_scale:
        scale(a.instances)
    pods = wait_ready(base, a.instances)
    print(f"екземплярів видно: {len(pods)} {pods}", flush=True)
    ids = ensure_ids(base, 100) if "{id}" in a.path else []

    sampler = StatsSampler()
    sampler.start()
    host = HostSampler()
    host.start()
    summary = step.run(base, a.path, ids, out, a.procs, a.max_rate,
                       label=f"{name}: {a.instances} екземпляр(и)")
    sampler.stop()
    host.stop()

    start, end = summary["start_wall"], time.time()
    extra = {
        "instances": a.instances,
        "pods": pods,
        "docker_stats": sampler.samples,
        "host_stats": host.samples,
    }
    try:
        extra["prom_steps"] = step_metrics(url, start, summary["steps"])
    except Exception as e:
        extra["prom_steps_error"] = repr(e)
    try:
        prom_file = out.with_name(out.name + "_prom.json")
        prom_file.write_text(json.dumps(prom.dump(url, start - 10, end + 10, 5.0),
                                        ensure_ascii=False), encoding="utf-8")
        extra["prom_series_file"] = prom_file.name
    except Exception as e:
        extra["prom_series_error"] = repr(e)

    merged = {**summary, **extra}
    out.with_suffix(".json").write_text(json.dumps(merged, indent=2, ensure_ascii=False),
                                        encoding="utf-8")
    print(f"\nX({a.instances}) = {summary['X']:.1f} ±{summary['X_sd']:.1f} rps "
          f"на сходинці {summary['X_step']} (задано {summary['X_target_rps']:.0f}); "
          f"зупинка: {summary['stop_reason']}")
    print(f"-> {out.with_suffix('.json')}")


if __name__ == "__main__":
    main()
