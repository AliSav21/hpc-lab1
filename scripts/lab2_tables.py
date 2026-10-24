"""Таблиці звіту лабораторної роботи 2 (кроки 6, 7, 9, 11) з файлів results/lab2."""
import sys as _sys
_sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows: cp1252
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in _sys.path:
    _sys.path.insert(0, str(ROOT))

from loadgen import usl  # noqa: E402

BUCKETS_MS = [5, 10, 25, 50, 100, 250, 500, 1000]   # набір Г1 варіанта 19


def num(v, nd=1):
    return "—" if v is None else f"{v:.{nd}f}".replace(".", ",")


def pct(v, nd=2):
    return "—" if v is None else f"{v * 100:.{nd}f}".replace(".", ",")


def ms(v, nd=1):
    return "—" if v is None else f"{v * 1000:.{nd}f}".replace(".", ",")


def plural(n):
    """1 екземплярі, 2 екземплярах — щоб заголовки таблиць читались."""
    return f"{n} екземплярі" if n == 1 else f"{n} екземплярах"


def load(d, name):
    p = d / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def prom_step(run, s):
    for p in run.get("prom_steps", []):
        if p.get("step") == s:
            return p
    return {}


def server_p99_cell(p):
    """Оцінка p99 сервера з бакетом і похибкою; «> b(k)» за останньою скінченною межею."""
    est = (p or {}).get("server_p99")
    if not est:
        return "—", "—", "—"
    if est.get("beyond_last"):
        return f"> {est['bucket'][0] * 1000:.0f}".replace(".", ","), \
               f"({est['bucket'][0] * 1000:.0f}; ∞)".replace(".", ","), "не обмежена"
    lo, hi = (x * 1000 for x in est["bucket"])
    return ms(est["value_s"]), f"({lo:.0f}; {hi:.0f}]".replace(".", ","), \
        f"{est['max_error_s'] * 1000:.0f}".replace(".", ",")


def table_steps(run, title):
    """Усі сходинки одного прогону."""
    out = [f"**{title}**", "",
           "| Сходинка | Задана, rps | Надіслана, rps | Досягнута, rps | Розкид, rps | "
           "p50 клієнта, мс | p95 клієнта, мс | p99 клієнта, мс | Не-2xx, % |", "|" + "---|" * 9]
    for m in run["steps"]:
        out.append(f"| {m['step']} | {m['target_rps']:.0f} | {num(m['sent_rps'])} | "
                   f"{num(m['achieved_rps'])} | {num(m['achieved_sd'])} | "
                   f"{num(m['p50_ms'])} | {num(m['p95_ms'])} | {num(m['p99_ms'])} | "
                   f"{pct(m['non2xx_share'])} |")
    out.append("")
    out.append(f"Зупинка прогону: `{run['stop_reason']}`. "
               f"X = {num(run['X'])} rps (сходинка {run['X_step']}, "
               f"задано {run['X_target_rps']:.0f} rps).")
    return "\n".join(out) + "\n"


def table_step7(runs, g):
    """Крок 7: зведення по кількості екземплярів."""
    out = ["| N | X(N), rps | Розкид, rps | Сходинка (задана, rps) | p99 клієнта, мс | "
           "p99 сервера, мс | Не-2xx, % | CPU на екземпляр, ядер | В обробці на екземпляр | "
           "Обмежено генератором |", "|" + "---|" * 10]
    for n, run in sorted(runs.items()):
        p = prom_step(run, run["X_step"])
        sp99, _, _ = server_p99_cell(p)
        limited = (g and run["X"] >= 0.9 * g) or run["last_step_held"]
        out.append(
            f"| {n} | {num(run['X'])} | {num(run['X_sd'])} | "
            f"{run['X_step']} ({run['X_target_rps']:.0f}) | {num(run['X_p99_ms'])} | {sp99} | "
            f"{pct(run['X_non2xx_share'])} | {num(p.get('cpu_cores_per_instance'), 3)} | "
            f"{num(p.get('in_flight_per_instance'))} | {'так' if limited else 'ні'} |")
    return "\n".join(out) + "\n"


def table_step9(run):
    """Крок 9: p99 клієнта проти p99 сервера на кожній сходинці прогону на одному екземплярі."""
    out = ["| Сходинка | Задана, rps | p99 клієнта, мс | p99 сервера, мс | Бакет, мс | "
           "Макс. похибка, мс | Різниця клієнт − сервер, мс |", "|" + "---|" * 7]
    for m in run["steps"]:
        p = prom_step(run, m["step"])
        val, bucket, err = server_p99_cell(p)
        est = (p or {}).get("server_p99") or {}
        diff = "—"
        if m["p99_ms"] is not None and est.get("value_s") is not None:
            diff = num(m["p99_ms"] - est["value_s"] * 1000)
        out.append(f"| {m['step']} | {m['target_rps']:.0f} | {num(m['p99_ms'])} | {val} | "
                   f"{bucket} | {err} | {diff} |")
    return "\n".join(out) + "\n"


def table_m1(runs):
    """Крок 11 / метрика М1: зайнятість пулу і p99 очікування зʼєднання за сходинками."""
    out = ["| N | Сходинка | Задана, rps | Зайнято зʼєднань (середнє) | Зайнято (максимум) | "
           "p99 очікування зʼєднання, мс | Бакет, мс |", "|" + "---|" * 7]
    for n, run in sorted(runs.items()):
        for m in run["steps"]:
            p = prom_step(run, m["step"])
            w = (p or {}).get("pool_wait_p99") or {}
            if w.get("beyond_last"):
                val = f"> {w['bucket'][0] * 1000:.0f}".replace(".", ",")
                bucket = f"({w['bucket'][0] * 1000:.0f}; ∞)".replace(".", ",")
            elif w:
                val = ms(w["value_s"])
                bucket = f"({w['bucket'][0] * 1000:.0f}; {w['bucket'][1] * 1000:.0f}]" \
                    .replace(".", ",")
            else:
                val, bucket = "—", "—"
            out.append(f"| {n} | {m['step']} | {m['target_rps']:.0f} | "
                       f"{num(p.get('pool_busy_per_instance'), 2)} | "
                       f"{num(p.get('pool_busy_max'), 0)} | {val} | {bucket} |")
    return "\n".join(out) + "\n"


def table_docker_stats(run, n):
    """Крок 11: docker stats у вікні сходинки, на якій досягнуто X(N)."""
    from loadgen import step as stepmod
    begin, end = stepmod.step_window(run["X_step"])
    w0 = run["start_wall"] + begin + stepmod.TRANSIENT_S
    w1 = run["start_wall"] + end
    rows = {}
    for sample in run.get("docker_stats", []):
        if not (w0 <= sample["t"] <= w1):
            continue
        for r in sample["rows"]:
            if "Name" not in r:
                continue
            cpu = float(r["CPUPerc"].rstrip("%").replace(",", ".") or 0)
            mem = r["MemUsage"].split("/")[0].strip()
            rows.setdefault(r["Name"], {"cpu": [], "mem": []})
            rows[r["Name"]]["cpu"].append(cpu)
            rows[r["Name"]]["mem"].append(mem)
    out = [f"Вікно сходинки {run['X_step']} прогону на {plural(n)} "
           f"(задано {run['X_target_rps']:.0f} rps), "
           f"{len([s for s in run.get('docker_stats', []) if w0 <= s['t'] <= w1])} знімків.", "",
           "| Контейнер | CPU, % (середнє) | CPU, % (максимум) | Памʼять (останній знімок) |",
           "|---|---|---|---|"]
    for name in sorted(rows):
        c = rows[name]["cpu"]
        out.append(f"| `{name}` | {num(sum(c) / len(c))} | {num(max(c))} | "
                   f"{rows[name]['mem'][-1]} |")
    return "\n".join(out) + "\n"


def table_host_stats(run, n):
    """Крок 11: CPU хоста і процесів генератора у вікні сходинки X(N)."""
    from loadgen import step as stepmod
    begin, end = stepmod.step_window(run["X_step"])
    w0 = run["start_wall"] + begin + stepmod.TRANSIENT_S
    w1 = run["start_wall"] + end
    win = [h for h in run.get("host_stats", []) if w0 <= h["t"] <= w1]
    if not win:
        return "Знімків CPU хоста у вікні немає.\n"
    cpu = [h["host_cpu_pct"] for h in win]
    gen = win[-1]["python_cpu_s"] - win[0]["python_cpu_s"]
    span = win[-1]["t"] - win[0]["t"]
    rows = ["| Величина | Значення |", "|---|---|",
            f"| CPU хоста, % (середнє за вікно) | {num(sum(cpu) / len(cpu))} |",
            f"| CPU хоста, % (максимум) | {num(max(cpu))} |"]
    if span > 0:
        rows.append(f"| CPU процесів генератора, ядер | {num(gen / span, 2)} |")
    mem = [h.get("python_mem_bytes", 0) for h in win]
    if any(mem):
        rows.append(f"| Памʼять процесів генератора, МБ | {num(max(mem) / 1048576)} |")
    rows.append(f"| Знімків у вікні | {len(win)} |")
    return "\n".join(rows) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default="results/lab2")
    ap.add_argument("--out", default="results/lab2/tables.md")
    ap.add_argument("--exclude", default="", help="точки, обмежені генератором")
    a = ap.parse_args()
    d = Path(a.dir)

    noop = load(d, "noop")
    g = noop["X"] if noop else None
    runs = {}
    for n in (1, 2, 3, 4):
        r = load(d, f"N{n}")
        if r:
            runs[n] = r

    parts = ["# Таблиці лабораторної роботи 2 (варіант 19)", ""]
    if g:
        parts += [f"## Крок 6. Межа генератора G", "",
                  f"Прогін проти `/noop` балансувальника через той самий порт: "
                  f"**G = {num(g)} rps** (сходинка {noop['X_step']}, "
                  f"задано {noop['X_target_rps']:.0f} rps, розкид {num(noop['X_sd'])} rps).", "",
                  table_steps(noop, "Сходинки прогону проти /noop")]
    if runs:
        parts += ["## Крок 7. Пропускна здатність на 1, 2, 3 і 4 екземплярах", "",
                  table_step7(runs, g)]
        for n, run in sorted(runs.items()):
            parts.append(table_steps(run, f"Сходинки прогону на {plural(n)}"))
    if 1 in runs:
        parts += ["## Крок 9. p99 клієнта проти p99 сервера (один екземпляр)", "",
                  table_step9(runs[1])]
    if runs:
        drop = {int(x) for x in a.exclude.split(",") if x.strip()}
        pts = {n: r["X"] for n, r in runs.items() if n == 1 or n not in drop}
        if 1 in pts:
            res = usl.fit(pts)
            parts += ["## Крок 10. Універсальний закон масштабованості", "",
                      "```", usl.report(res), "```", ""]
        parts += ["## Крок 11 / метрика М1. Пул зʼєднань", "", table_m1(runs)]
        if 4 in runs:
            parts += ["### docker stats на сходинці X(4)", "", table_docker_stats(runs[4], 4),
                      "### CPU хоста і генератора на сходинці X(4)", "",
                      table_host_stats(runs[4], 4)]

    text = "\n".join(parts)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    print(f"\n-> {a.out}")


if __name__ == "__main__":
    main()
