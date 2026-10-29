"""Один ступінчастий прогін у кластері Kubernetes (крок 7 ЛР3).
"""
import sys as _sys
_sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows: cp1252
import argparse
import json
import subprocess
import time
import urllib.request
from pathlib import Path

from loadgen import prom, step
from loadgen.seed import ensure_ids

ROOT = Path(__file__).resolve().parent.parent
NS = "donors"
CTX = "kind-hpc"
SEL = f'namespace="{NS}"'
WEB = f'{SEL},container="web"'


def kubectl(*args, capture=True, check=True):
    cmd = ["kubectl", "--context", CTX, *args]
    print("$ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT, check=check, capture_output=capture,
                          text=True, encoding="utf-8")


def scale(n):
    kubectl("scale", f"deployment/web", "-n", NS, f"--replicas={n}")
    kubectl("rollout", "status", "deployment/web", "-n", NS, "--timeout=300s", capture=False)


def wait_ready(base, n, timeout=180.0):
    """Чекає, поки через NodePort видно n різних подів (заголовок X-Pod)."""
    deadline = time.time() + timeout
    pods = set()
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/healthz", timeout=3) as r:
                if r.status == 200:
                    pods.add(r.headers.get("X-Pod", ""))
        except Exception:
            pass
        if len(pods) >= n:
            return sorted(pods)
        time.sleep(0.3)
    raise RuntimeError(f"видно лише {len(pods)} з {n} подів: {sorted(pods)}")


def pods_by_node(only=None):
    """Розподіл подів web по вузлах: {вузол: [поди]} (підтвердження Д1).

    only: перелік подів, які справді обслуговують запити (з wait_ready). Поди, що
    завершуються після зменшення кількості реплік, лишаються Running ще кілька секунд,
    і без цього фільтра потрапили б у розподіл.
    """
    # лише робочі поди: ті, що завершуються після зменшення кількості реплік, не рахуються
    r = kubectl("get", "pods", "-n", NS, "-l", "app=web",
                "--field-selector=status.phase=Running",
                "-o", "jsonpath={range .items[*]}{.metadata.name}={.spec.nodeName} {end}")
    out = {}
    for item in r.stdout.split():
        pod, _, node = item.partition("=")
        if only and pod not in only:
            continue
        out.setdefault(node, []).append(pod)
    return out


def cadvisor(url, expr, at):
    """Запит до лічильників cAdvisor"""
    for win in ("30s", "60s"):      # WIN у виразі заміняємо самі: .format псує дужки PromQL
        res = prom.query(url, expr.replace("WIN", win), at)
        if res:
            return dict((lbl.get("pod", "?"), v) for lbl, v in res)
    return {}


def step_metrics(url, start_wall, steps):
    """Метрики Prometheus за вимірювальним вікном кожної сходинки (останні 20 с)."""
    out = []
    for m in steps:
        begin, end = step.step_window(m["step"])
        w0 = start_wall + begin + step.TRANSIENT_S
        w1 = start_wall + end
        win = f"{int(w1 - w0)}s"
        try:
            per_pod = dict(
                (lbl.get("pod", "?"), v) for lbl, v in prom.query(
                    url, f'sum by (pod) (rate(http_requests_total{{{SEL},code=~"2.."}}[{win}]))', w1))
            vals = [v for v in per_pod.values() if v is not None]
            x_max = max(vals) if vals else None
            x_avg = (sum(vals) / len(vals)) if vals else None
            throttle = cadvisor(
                url,
                f'(increase(container_cpu_cfs_throttled_periods_total{{{WEB}}}[WIN]) '
                f'/ increase(container_cpu_cfs_periods_total{{{WEB}}}[WIN]))', w1)
            cpu = cadvisor(url, f'rate(container_cpu_usage_seconds_total{{{WEB}}}[WIN])', w1)
            busiest = max(per_pod, key=per_pod.get) if per_pod else None
            idlest = min(per_pod, key=per_pod.get) if per_pod else None
            row = {
                "step": m["step"], "target_rps": m["target_rps"],
                "window_unix": [w0, w1],
                "rps_per_pod": per_pod,
                "rps_server_total": sum(vals) if vals else None,
                "imbalance_I": (x_max / x_avg) if x_avg else None,
                "rps_per_node": dict(
                    (lbl.get("node", "?"), v) for lbl, v in prom.query(
                        url,
                        f'sum by (node) (sum by (pod) '
                        f'(rate(http_requests_total{{{SEL},code=~"2.."}}[{win}])) '
                        f'* on (pod) group_left(node) kube_pod_info{{{SEL}}})', w1)),
                "throttle_per_pod": throttle,
                "throttle_avg": (sum(throttle.values()) / len(throttle)) if throttle else None,
                "throttle_max": max(throttle.values()) if throttle else None,
                "cpu_cores_per_pod": cpu,
                "server_p99": prom.server_quantile(
                    url, "http_request_duration_seconds", w0, w1, grouping=f'{{{SEL}}}'),
                "pool_wait_p99": prom.server_quantile(
                    url, "db_pool_wait_seconds", w0, w1, grouping=f'{{{SEL}}}'),
                "pool_busy_per_pod": prom.scalar(
                    url, f'avg(avg_over_time(db_pool_connections_busy{{{SEL}}}[{win}]))', w1),
                "pool_busy_max": prom.scalar(
                    url, f'max(max_over_time(db_pool_connections_busy{{{SEL}}}[{win}]))', w1),
                "in_flight_per_pod": prom.scalar(
                    url, f'avg(avg_over_time(http_requests_in_flight{{{SEL}}}[{win}]))', w1),
                "replicas_ready": prom.scalar(
                    url, f'kube_deployment_status_replicas_ready{{{SEL},deployment="web"}}', w1),
                "busiest_pod": busiest,
                "idlest_pod": idlest,
            }
            # крок 8: p99 сервера і дроселювання для найзавантаженішого і найменш завантаженого
            for tag, pod in (("busiest", busiest), ("idlest", idlest)):
                if pod:
                    row[f"{tag}_p99"] = prom.server_quantile(
                        url, "http_request_duration_seconds", w0, w1,
                        grouping=f'{{{SEL},pod="{pod}"}}')
                    row[f"{tag}_throttle"] = throttle.get(pod)
                    row[f"{tag}_rps"] = per_pod.get(pod)
        except Exception as e:
            row = {"step": m["step"], "error": repr(e)}
        out.append(row)
    return out


# ряди для діаграм кроків 8 і 9
SERIES = {
    "rps_per_pod": f'sum by (pod) (rate(http_requests_total{{{SEL},code=~"2.."}}[15s]))',
    "rps_total": f'sum(rate(http_requests_total{{{SEL},code=~"2.."}}[15s]))',
    "cpu_per_pod": f'rate(container_cpu_usage_seconds_total{{{WEB}}}[1m])',
    "throttle_per_pod": (f'rate(container_cpu_cfs_throttled_periods_total{{{WEB}}}[1m]) '
                         f'/ rate(container_cpu_cfs_periods_total{{{WEB}}}[1m])'),
    "in_flight_per_pod": f'http_requests_in_flight{{{SEL}}}',
    "pool_busy_per_pod": f'db_pool_connections_busy{{{SEL}}}',
    "replicas_ready": f'kube_deployment_status_replicas_ready{{{SEL},deployment="web"}}',
    "dur_buckets": f'sum by (le) (http_request_duration_seconds_bucket{{{SEL}}})',
    "wait_buckets": f'sum by (le) (db_pool_wait_seconds_bucket{{{SEL}}})',
}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--replicas", type=int, required=True)
    ap.add_argument("--max-rate", type=float, default=None, help="межа стенду G")
    ap.add_argument("--path", default="/donor-registry/{id}")
    ap.add_argument("--out-dir", default="results/lab3")
    ap.add_argument("--name", default=None, help="імʼя файлів; типово N<replicas>")
    ap.add_argument("--procs", type=int, default=None)
    ap.add_argument("--max-steps", type=int, default=step.MAX_STEPS)
    ap.add_argument("--base", default=None)
    ap.add_argument("--prom", default=None)
    ap.add_argument("--no-scale", action="store_true")
    a = ap.parse_args()

    base = a.base or step.default_base()
    url = a.prom or prom.default_url()
    name = a.name or f"N{a.replicas}"
    out = ROOT / a.out_dir / name

    if not a.no_scale:
        scale(a.replicas)
    pods = wait_ready(base, a.replicas)
    print(f"подів видно: {len(pods)} {pods}", flush=True)
    by_node = pods_by_node(pods)
    print("по вузлах: " + ", ".join(f"{k}={len(v)}" for k, v in sorted(by_node.items())),
          flush=True)
    ids = ensure_ids(base, 100) if "{id}" in a.path else []

    summary = step.run(base, a.path, ids, out, a.procs, a.max_rate, a.max_steps,
                       label=f"Kubernetes, N={a.replicas}")
    summary["replicas"] = a.replicas
    summary["pods"] = pods
    summary["pods_by_node"] = by_node
    summary["prom"] = step_metrics(url, summary["start_wall"], summary["steps"])

    end_wall = time.time()
    series = prom.dump(url, summary["start_wall"] - 15, end_wall, 5.0, SERIES)
    Path(f"{out}_prom.json").write_text(json.dumps(series, ensure_ascii=False), encoding="utf-8")
    Path(f"{out}.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                   encoding="utf-8")

    best = next((p for p in summary["prom"] if p.get("step") == summary["X_step"]), {})
    print(f"\nN={a.replicas}: X = {summary['X']:.1f} ±{summary['X_sd']:.1f} rps,"
          f" p99 клієнта {summary['X_p99_ms'] and round(summary['X_p99_ms'])} мс,"
          f" p99 сервера {best.get('server_p99') and round(best['server_p99']['value_s'] * 1000)} мс,"
          f" I = {best.get('imbalance_I') and round(best['imbalance_I'], 2)},"
          f" дроселювання {best.get('throttle_avg') and round(best['throttle_avg'], 3)}"
          f" (макс {best.get('throttle_max') and round(best['throttle_max'], 3)})", flush=True)
    print(f"  {out}.json, {out}.csv, {out}_prom.json")


if __name__ == "__main__":
    main()
