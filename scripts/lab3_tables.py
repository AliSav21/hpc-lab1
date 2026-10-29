"""Таблиці кроків 7-9 ЛР3: X(N), дисбаланс, параметри закону масштабованості
і порівняння стендів Compose (ЛР2) та Kubernetes (ЛР3).

    python scripts\\lab3_tables.py
"""
import sys as _sys
_sys.stdout.reconfigure(encoding="utf-8")
import io
import json
from pathlib import Path

sys_path = Path(__file__).resolve().parent.parent
if str(sys_path) not in _sys.path:
    _sys.path.insert(0, str(sys_path))

from loadgen import prom  # noqa: E402
from loadgen.usl import fit, model  # noqa: E402

ROOT = sys_path
K8S = ROOT / "results" / "lab3"
COMPOSE = ROOT / "results" / "lab2"
REPLICAS = (1, 2, 3, 4)


def load(d, n):
    return json.load(io.open(d / f"N{n}.json", encoding="utf-8"))


def ms(q):
    """Значення квантиля з оцінки за гістограмою, мс."""
    if not q:
        return None
    return q["value_s"] * 1000


def fmt(v, digits=1, dash="—"):
    return dash if v is None else f"{v:.{digits}f}"


def step_at_x(run):
    return next((p for p in run["prom"] if p.get("step") == run["X_step"]), {})


def pods_by_node(prom_row):
    """Розподіл подів по вузлах на момент вимірювання, за kube_pod_info з Prometheus.

    Надійніше за знімок kubectl під час прогону: там ще видно поди, які завершуються
    після зменшення кількості реплік.
    """
    pods = set(prom_row.get("rps_per_pod", {}))
    if not pods:
        return {}
    at = prom_row["window_unix"][1]
    out = {}
    try:
        rows = prom.query(prom.default_url(),
                          'kube_pod_info{namespace="donors"}', at)
    except Exception:
        return {}
    for lbl, _ in rows:
        if lbl.get("pod") in pods:
            out.setdefault(lbl.get("node", "?"), []).append(lbl["pod"])
    return out


def main():
    runs = {n: load(K8S, n) for n in REPLICAS}
    prom_x = {n: step_at_x(runs[n]) for n in REPLICAS}
    x_k8s = {n: runs[n]["X"] for n in REPLICAS}
    f_k8s = fit(x_k8s)

    compose = {}
    f_comp = None
    if (COMPOSE / "N1.json").exists():
        compose = {n: load(COMPOSE, n)["X"] for n in REPLICAS}
        f_comp = fit(compose)

    out = []
    w = out.append

    w("# Таблиці кроків 7-9 (варіант 19, кластер kind)\n")
    w("Сходинка 30 с, середні за останні 20 с, 32 постійних зʼєднання з закриттям після")
    w("100 запитів. X(N) береться з найбільшої сходинки, яку стенд витримав: досягнуто не")
    w("менше 0,9 заданої і менше 1 % відповідей не 2xx.\n")

    # ---------------------------------------------------------------- крок 7
    w("## Крок 7. Пропускна здатність X(N)\n")
    w("| N | X(N), rps | Розкид, rps | Сходинка (задана, rps) | p99 клієнта, мс "
      "| p99 сервера, мс | Не-2xx, % | Дроселювання сер. | Дроселювання макс. |")
    w("|---|---|---|---|---|---|---|---|---|")
    for n in REPLICAS:
        r, p = runs[n], prom_x[n]
        w(f"| {n} | {r['X']:.1f} | {r['X_sd']:.1f} | {r['X_target_rps']:.0f} "
          f"| {fmt(r['X_p99_ms'], 0)} | {fmt(ms(p.get('server_p99')), 1)} "
          f"| {fmt((r['X_non2xx_share'] or 0) * 100, 2)} "
          f"| {fmt(p.get('throttle_avg'), 4)} | {fmt(p.get('throttle_max'), 4)} |")
    w("")
    livez = K8S / "ladder_livez.json"
    g = json.load(io.open(livez, encoding="utf-8"))["X"] if livez.exists() else 700.0
    w(f"Межа стенду без бази (ступінчастий прогін на `/livez`): **{g:.0f} rps**, тож жодна")
    w("точка серії не обмежена генератором: найбільша X(N) це "
      f"{max(x_k8s.values()) / g * 100:.0f} % цієї межі.\n")

    # ---------------------------------------------------------------- крок 8
    w("## Крок 8. Коефіцієнт дисбалансу навантаження\n")
    w("I = X_max / X_avg на сходинці, де досягнуто X(N).\n")
    w("| N | I | Запити за секунду по подах | Найзавантаженіший: rps / p99, мс / дроселювання "
      "| Найменш завантажений: rps / p99, мс / дроселювання |")
    w("|---|---|---|---|---|")
    for n in REPLICAS:
        p = prom_x[n]
        pods = ", ".join(f"{v:.0f}" for v in sorted(p.get("rps_per_pod", {}).values(),
                                                    reverse=True))
        if n == 1:
            w(f"| 1 | 1,00 | {pods} | — | — |")
            continue
        w(f"| {n} | {fmt(p.get('imbalance_I'), 3)} | {pods} "
          f"| {fmt(p.get('busiest_rps'), 0)} / {fmt(ms(p.get('busiest_p99')), 1)} "
          f"/ {fmt(p.get('busiest_throttle'), 4)} "
          f"| {fmt(p.get('idlest_rps'), 0)} / {fmt(ms(p.get('idlest_p99')), 1)} "
          f"/ {fmt(p.get('idlest_throttle'), 4)} |")
    w("")

    # ---------------------------------------------------------------- Д1
    w("## Додаткова вимога Д1. Розподіл подів по робочих вузлах\n")
    w("| N | Поди по вузлах | Запити за секунду на вузол |")
    w("|---|---|---|")
    for n in REPLICAS:
        by_node = pods_by_node(prom_x[n]) or runs[n].get("pods_by_node", {})
        pods_s = ", ".join(f"{k} = {len(v)}" for k, v in sorted(by_node.items()))
        rps_s = ", ".join(f"{k} = {v:.0f}" for k, v in
                          sorted(prom_x[n].get("rps_per_node", {}).items()))
        w(f"| {n} | {pods_s} | {rps_s} |")
    w("")
    w("`topologySpreadConstraints` з `maxSkew: 1` і `nodeTaintsPolicy: Honor` тримає поди")
    w("рівномірно: 1+0, 1+1, 2+1, 2+2. Запити за секунду на вузол повторюють розподіл подів")
    w("з точністю до дисбалансу зʼєднань.\n")

    # ---------------------------------------------------------------- крок 9
    w("## Крок 9. Закон масштабованості\n")
    w("Лінеаризована форма N / C(N) − 1 = σ(N − 1) + κN(N − 1), метод найменших квадратів")
    w("без вільного члена.\n")
    w("| N | X(N), rps | C(N) | y = N/C(N) − 1 | u = N − 1 | v = N(N − 1) |")
    w("|---|---|---|---|---|---|")
    w(f"| 1 | {x_k8s[1]:.1f} | 1,000 | — | — | — |")
    for r in f_k8s["rows"]:
        w(f"| {r['N']} | {x_k8s[r['N']]:.1f} | {r['C']:.3f} | {r['y']:.4f} "
          f"| {r['u']} | {r['v']} |")
    w("")
    w(f"λ = X(1) = {f_k8s['lambda']:.1f} rps  ")
    w(f"σ = {f_k8s['sigma']:.4f}  ")
    w(f"κ = {f_k8s['kappa']:.4f}  ")
    if f_k8s.get("N_star"):
        w(f"N* = √((1 − σ) / κ) = {f_k8s['N_star']:.2f},  "
          f"X(N*) ≈ {f_k8s['X_at_N_star']:.0f} rps")
    if f_k8s.get("note"):
        w(f"\nПримітка: {f_k8s['note']}")
    w("")
    w("Значення закону проти виміряних:\n")
    w("| N | Виміряно, rps | Закон, rps | Різниця, % |")
    w("|---|---|---|---|")
    for n in REPLICAS:
        mv = model(n, f_k8s["lambda"], f_k8s["sigma"], f_k8s["kappa"])
        w(f"| {n} | {x_k8s[n]:.1f} | {mv:.1f} | {(mv - x_k8s[n]) / x_k8s[n] * 100:+.1f} |")
    w("")

    # ---------------------------------------------------------------- порівняння стендів
    if f_comp:
        w("## Порівняння стендів: Compose (ЛР2) і Kubernetes (ЛР3)\n")
        w("| N | X(N) Compose, rps | C(N) Compose | X(N) Kubernetes, rps | C(N) Kubernetes |")
        w("|---|---|---|---|---|")
        for n in REPLICAS:
            w(f"| {n} | {compose[n]:.1f} | {compose[n] / compose[1]:.3f} "
              f"| {x_k8s[n]:.1f} | {x_k8s[n] / x_k8s[1]:.3f} |")
        w("")
        w("| Параметр | Compose | Kubernetes |")
        w("|---|---|---|")
        w(f"| λ = X(1), rps | {f_comp['lambda']:.1f} | {f_k8s['lambda']:.1f} |")
        w(f"| σ | {f_comp['sigma']:.4f} | {f_k8s['sigma']:.4f} |")
        w(f"| κ | {f_comp['kappa']:.4f} | {f_k8s['kappa']:.4f} |")
        w(f"| N* | {fmt(f_comp.get('N_star'), 2)} | {fmt(f_k8s.get('N_star'), 2)} |")
        w(f"| X(N*), rps | {fmt(f_comp.get('X_at_N_star'), 0)} "
          f"| {fmt(f_k8s.get('X_at_N_star'), 0)} |")
        w("")

    path = K8S / "tables.md"
    path.write_text("\n".join(out), encoding="utf-8")
    print(f"{path}")
    (K8S / "usl.json").write_text(
        json.dumps({"k8s": {"X": x_k8s, "fit": f_k8s},
                    "compose": {"X": compose, "fit": f_comp}}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    print(f"{K8S / 'usl.json'}")


if __name__ == "__main__":
    main()
