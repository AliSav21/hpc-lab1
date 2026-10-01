"""Медіани за трьома повторами для кожної конфігурації -> results/aggregate.json і results/table.md."""
import sys as _sys
_sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows за замовчуванням cp1252
import json
import statistics
from pathlib import Path

RESULTS = Path(__file__).resolve().parent.parent / "results"
METRICS = ["achieved_rps", "p50_ms", "p95_ms", "p99_ms", "mean_ms", "non2xx_share", "L_mean", "L_max"]


def main():
    agg = {}
    for inst in (1, 2):
        by_rate = {}
        for p in sorted((RESULTS / f"{inst}x").glob("rate*_r*.json")):
            s = json.loads(p.read_text())
            by_rate.setdefault(int(s["rate"]), []).append(s)
        for rate, runs in sorted(by_rate.items()):
            agg[f"{inst}x/{rate}"] = {
                "instances": inst, "rate": rate, "runs": len(runs),
                **{m: statistics.median(r[m] for r in runs) for m in METRICS},
            }
    (RESULTS / "aggregate.json").write_text(json.dumps(agg, indent=2), encoding="utf-8")
    rows = ["| інстансів | задано, rps | досягнуто, rps | p50, мс | p95, мс | p99, мс | не-2xx, % |",
            "|---|---|---|---|---|---|---|"]
    for v in agg.values():
        rows.append(f"| {v['instances']} | {v['rate']} | {v['achieved_rps']:.1f} | {v['p50_ms']:.1f} | "
                    f"{v['p95_ms']:.1f} | {v['p99_ms']:.1f} | {v['non2xx_share'] * 100:.2f} |")
    (RESULTS / "table.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print("\n".join(rows))


if __name__ == "__main__":
    main()
