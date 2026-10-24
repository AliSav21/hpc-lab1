"""Діаграми звіту лабораторної роботи 2 (кроки 8, 10, 11) за файлами results/lab2.

Потрібен matplotlib: pip install -r requirements-dev.txt
"""
import sys as _sys
_sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows: cp1252
import argparse
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys_path = Path(__file__).resolve().parent.parent
if str(sys_path) not in _sys.path:
    _sys.path.insert(0, str(sys_path))

from loadgen import step, usl  # noqa: E402
from loadgen.prom import quantile_from_buckets  # noqa: E402

WINDOW = 15.0   # вікно p99 сервера, с
EVERY = 5.0     # оновлення p99 сервера, с


# ---------------------------------------------------------------- читання даних

def load_run(d, name):
    run = json.loads((d / f"{name}.json").read_text(encoding="utf-8"))
    rows = []
    with (d / f"{name}.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append({k: (float(v) if v not in ("", None) else None)
                         for k, v in r.items()})
    prom_file = d / f"{name}_prom.json"
    series = json.loads(prom_file.read_text(encoding="utf-8")) if prom_file.exists() else None
    return run, rows, series


def series_by_instance(series, name):
    """[(імʼя екземпляра, [(t_rel, value)])] для ряду з мітками на екземпляр."""
    if not series or name not in series["series"]:
        return []
    out = []
    for s in series["series"][name]:
        label = s["labels"].get("instance", "?")
        vals = s["values"]
        # Prometheus лишає ряди екземплярів попередніх прогонів: у цьому вікні вони порожні
        if max((v for _, v in vals), default=0.0) <= 0.0:
            continue
        out.append((label, vals))
    return sorted(out)


def bucket_table(series, name):
    """{t: [(межа, накопичена кількість)]} з ряду bucket."""
    if not series or name not in series["series"]:
        return {}
    table = {}
    for s in series["series"][name]:
        le = float(s["labels"]["le"])
        for t, v in s["values"]:
            table.setdefault(t, []).append((le, v))
    return {t: sorted(v) for t, v in table.items()}


def sliding_server_p99(series, name, start_wall, q=0.99):
    """p99 сервера на вікні WINDOW з оновленням EVERY, за приростами бакетів."""
    table = bucket_table(series, name)
    ts = sorted(table)
    if not ts:
        return [], []
    xs, ys = [], []
    t = ts[0] + WINDOW
    while t <= ts[-1]:
        a = min(ts, key=lambda x: abs(x - (t - WINDOW)))
        b = min(ts, key=lambda x: abs(x - t))
        da, db = dict(table[a]), dict(table[b])
        inc = sorted((le, db.get(le, 0.0) - da.get(le, 0.0)) for le in set(da) | set(db))
        est = quantile_from_buckets(inc, q)
        if est:
            xs.append(b - start_wall)
            ys.append(est["value_s"] * 1000.0)
        t += EVERY
    return xs, ys


def step_bands(ax, run):
    """Межі сходинок на часовій осі."""
    for s in range(-1, len(run["steps"])):
        begin, _ = step.step_window(s)
        ax.axvline(begin, color="0.85", lw=0.8, zorder=0)


# ---------------------------------------------------------------- діаграми кроку 8

def chart_rates(d, name, run, rows, out):
    fig, ax = plt.subplots(figsize=(9, 3.6))
    t = [r["t_s"] for r in rows]
    ax.plot(t, [r["target_rps"] for r in rows], label="задана", color="tab:gray", ls="--")
    ax.plot(t, [r["achieved_rps"] for r in rows], label="досягнута", color="tab:blue")
    ax.plot(t, [r["sent_rps"] for r in rows], label="надіслана", color="tab:orange", lw=0.8)
    step_bands(ax, run)
    ax.set_xlabel("час прогону, с")
    ax.set_ylabel("запитів за секунду")
    ax.set_title(f"{name}: задана і досягнута інтенсивність")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.3)
    save(fig, out)


def chart_p99(d, name, run, rows, series, out):
    fig, ax = plt.subplots(figsize=(9, 3.6))
    t = [r["t_s"] for r in rows]
    ax.plot(t, [r["p99_ms"] for r in rows], label="p99 клієнта", color="tab:red")
    xs, ys = sliding_server_p99(series, "dur_buckets", run["start_wall"])
    if xs:
        ax.plot(xs, ys, label="p99 сервера, вікно 15 с кожні 5 с", color="tab:green")
    step_bands(ax, run)
    ax.set_yscale("log")
    ax.set_xlabel("час прогону, с")
    ax.set_ylabel("мс (логарифмічна шкала)")
    ax.set_title(f"{name}: p99 клієнта і p99 сервера")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.3, which="both")
    save(fig, out)


def chart_instances(d, name, run, series, out):
    fig, axes = plt.subplots(3, 1, figsize=(9, 8), sharex=True)
    specs = [("rps", "запитів за секунду", "кількість запитів за секунду"),
             ("cpu", "ядер", "використання CPU"),
             ("in_flight", "запитів", "кількість запитів в обробці")]
    t0 = run["start_wall"]
    for ax, (key, ylabel, title) in zip(axes, specs):
        for label, vals in series_by_instance(series, key):
            ax.plot([t - t0 for t, _ in vals], [v for _, v in vals], lw=1.0, label=label)
        ax.set_ylabel(ylabel)
        ax.set_title(f"{name}: {title} на кожному екземплярі", fontsize=10)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7, ncol=2)
    axes[-1].set_xlabel("час прогону, с")
    save(fig, out)


# ---------------------------------------------------------------- діаграма кроку 10

def chart_usl(points, excluded, res, out):
    fig, ax = plt.subplots(figsize=(7, 4.4))
    ns = sorted(points)
    ax.errorbar(ns, [points[n][0] for n in ns], yerr=[points[n][1] for n in ns],
                fmt="o", color="tab:blue", capsize=4, label="виміряна X(N) з розкидом")
    for n in excluded:
        if n in points:
            ax.annotate("обмежено\nгенератором", (n, points[n][0]), textcoords="offset points",
                        xytext=(6, -22), fontsize=7, color="tab:red")
    grid = [1 + i * 0.05 for i in range(int((max(ns) - 1) / 0.05) + 1)]
    lam, sg, kp = res["lambda"], res["sigma"], res["kappa"]
    ax.plot(grid, [usl.model(x, lam, sg, kp) for x in grid], color="tab:green",
            label=f"закон: σ = {sg:.4f}, κ = {kp:.4f}")
    ax.plot(grid, [lam * x for x in grid], color="tab:gray", ls="--", label="λN")
    if res.get("N_star") and res["N_star"] <= max(ns) + 2:
        ax.axvline(res["N_star"], color="tab:orange", ls=":",
                   label=f"N* = {res['N_star']:.2f}")
    ax.set_xlabel("кількість екземплярів N")
    ax.set_ylabel("пропускна здатність X(N), rps")
    ax.set_title("Пропускна здатність і універсальний закон масштабованості")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    save(fig, out)


# ---------------------------------------------------------------- діаграма кроку 11 (М1)

def chart_m1(runs, out):
    """Зайнятість пулу і p99 часу очікування зʼєднання від заданої інтенсивності."""
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for name, run in runs.items():
        prom_steps = {p["step"]: p for p in run.get("prom_steps", [])}
        xs = [m["target_rps"] for m in run["steps"] if m["step"] in prom_steps]
        busy = [prom_steps[m["step"]].get("pool_busy_per_instance") for m in run["steps"]
                if m["step"] in prom_steps]
        wait = [(prom_steps[m["step"]].get("pool_wait_p99") or {}).get("value_s")
                for m in run["steps"] if m["step"] in prom_steps]
        axes[0].plot(xs, busy, "o-", label=name)
        axes[1].plot(xs, [w * 1000 if w is not None else None for w in wait], "o-", label=name)
    axes[0].set_ylabel("зайнятих зʼєднань на екземпляр")
    axes[0].set_title("Зайнятість пулу (розмір 10)", fontsize=10)
    axes[1].set_ylabel("мс")
    axes[1].set_yscale("log")
    axes[1].set_title("p99 часу очікування зʼєднання", fontsize=10)
    for ax in axes:
        ax.set_xlabel("задана інтенсивність, rps")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    save(fig, out)


def save(fig, out):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out.with_suffix(".png"), dpi=140)
    fig.savefig(out.with_suffix(".svg"))
    plt.close(fig)
    print(f"-> {out.with_suffix('.png')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--dir", default="results/lab2")
    ap.add_argument("--runs", default="N1,N2,N3,N4")
    ap.add_argument("--timeline", default="N1,N4", help="прогони для діаграм кроку 8")
    ap.add_argument("--exclude", default="", help="точки, обмежені генератором")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    d = Path(a.dir)
    out = Path(a.out or d / "charts")
    names = [x for x in a.runs.split(",") if x]
    loaded = {}
    for name in names:
        if (d / f"{name}.json").exists():
            loaded[name] = load_run(d, name)
        else:
            print(f"пропущено: немає {d / (name + '.json')}")

    for name in [x for x in a.timeline.split(",") if x in loaded]:
        run, rows, series = loaded[name]
        chart_rates(d, name, run, rows, out / f"{name}_rates")
        chart_p99(d, name, run, rows, series, out / f"{name}_p99")
        if series:
            chart_instances(d, name, run, series, out / f"{name}_instances")

    pts = {}
    for name, (run, _, _) in loaded.items():
        n = run.get("instances") or int(name.lstrip("N"))
        pts[n] = (run["X"], run["X_sd"])
    drop = {int(x) for x in a.exclude.split(",") if x.strip()}
    if pts:
        fitted = {n: v[0] for n, v in pts.items() if n == 1 or n not in drop}
        res = usl.fit(fitted)
        chart_usl(pts, drop, res, out / "usl")
        (out / "usl.json").write_text(json.dumps(res, indent=2, ensure_ascii=False),
                                      encoding="utf-8")
        print(usl.report(res))

    m1 = {name: loaded[name][0] for name in ("N1", "N4") if name in loaded}
    if m1:
        chart_m1(m1, out / "m1_pool")


if __name__ == "__main__":
    main()
