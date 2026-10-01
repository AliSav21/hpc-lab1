"""Три розрахунки за знятими числами -> results/calculations.md. Спершу aggregate.py."""
import sys as _sys
_sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows за замовчуванням cp1252
import json
import statistics
from pathlib import Path

RESULTS = Path(__file__).resolve().parent.parent / "results"


def main():
    agg = json.loads((RESULTS / "aggregate.json").read_text())
    out = []

    out.append("## 1. Прискорення і метрика Карпа-Флатта (N = 2)\n")
    out.append("S = X2 / X1 на тому самому рівні; e = (1/S - 1/N) / (1 - 1/N) = (1/S - 0.5) / 0.5\n")
    out.append("| рівень, rps | X1 | X2 | S | e |\n|---|---|---|---|---|")
    for rate in sorted({v["rate"] for v in agg.values()}):
        a, b = agg.get(f"1x/{rate}"), agg.get(f"2x/{rate}")
        if a and b:
            s = b["achieved_rps"] / a["achieved_rps"]
            e = (1 / s - 0.5) / 0.5
            out.append(f"| {rate} | {a['achieved_rps']:.1f} | {b['achieved_rps']:.1f} | {s:.3f} | {e:.3f} |")

    out.append("\n## 2. Закон Літтла (100 rps, 1 інстанс)\n")
    a = agg.get("1x/100")
    if a:
        lam, w = a["achieved_rps"], a["mean_ms"] / 1000
        out.append(f"λ = {lam:.2f} rps; W = {w * 1000:.2f} мс = {w:.5f} с; λ·W = {lam * w:.4f}; "
                   f"виміряне L = {a['L_mean']:.4f}; розбіжність = "
                   f"{abs(lam * w - a['L_mean']) / a['L_mean'] * 100:.2f} %")

    out.append("\n## 3. Відкритий і закритий контур\n")
    closed = {}
    for p in sorted((RESULTS / "closed").glob("*.json")):
        s = json.loads(p.read_text())
        closed.setdefault(s["concurrency"], []).append(s)
    out.append("Кількість одночасних запитів у закритому контурі дорівнює середньому L, виміряному"
               " у відкритому контурі на тому самому рівні.\n")
    out.append("| Рівень (відкритий контур) | Контур | Одночасних запитів | Досягнуто, rps |"
               " p95, мс | Не-2xx, % |")
    out.append("|---|---|---|---|---|---|")
    for conc, runs in sorted(closed.items()):
        rate = None
        for q in (RESULTS / "closed").glob(f"rate*_c{conc}_r1.json"):
            rate = int(q.name.split("_")[0].removeprefix("rate"))
        o = agg.get(f"1x/{rate}")
        if not o:
            continue
        cx = statistics.median(r["achieved_rps"] for r in runs)
        cp = statistics.median(r["p95_ms"] for r in runs)
        ce = statistics.median(r["non2xx_share"] for r in runs)
        out.append(f"| {rate} rps | відкритий | L ≈ {o['L_mean']:.0f} | {o['achieved_rps']:.1f} |"
                   f" {o['p95_ms']:.1f} | {o['non2xx_share'] * 100:.2f} |")
        out.append(f"| {rate} rps | закритий | {conc} | {cx:.1f} | {cp:.1f} | {ce * 100:.2f} |")

    text = "\n".join(out) + "\n"
    (RESULTS / "calculations.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
