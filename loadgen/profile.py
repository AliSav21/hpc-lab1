"""Прогін за профілем навантаження (крок 10 ЛР3): список відрізків тривалість, інтенсивність.
"""
import sys as _sys
_sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows: cp1252
import argparse
import asyncio
import json
import multiprocessing as mp
import os
import random
import statistics
import time
from pathlib import Path
from urllib.parse import urlparse

from loadgen.step import (CONNS, FLUSH_LAG, REQ_PER_CONN, START_DELAY, STATUS_CONNERR,
                          STATUS_TIMEOUT, TIMEOUT, Buckets, Collector, Connections,
                          default_base, fetch, fmt, pct, sharpen_timer)

SPIKE_FACTOR = 1.5      # варіант 19: рівень стрибка 1,5·X(1)
BASE_FACTOR = 0.3       # рівні до і після стрибка: 0,3·X(1)
SPIKE_CAP = 0.9         # стрибок не вище за 0,9·X(R)
SEG_S = (30.0, 60.0, 90.0)


# профіль

def build_profile(x1, xr, spike_factor=SPIKE_FACTOR):
    """Відрізки профілю за X(1) і X(R): [(тривалість, інтенсивність), ...]."""
    base = BASE_FACTOR * x1
    spike = min(spike_factor * x1, SPIKE_CAP * xr)
    return [(SEG_S[0], base), (SEG_S[1], spike), (SEG_S[2], base)]


def parse_profile(text):
    """Профіль із рядка 30:100,60:500,90:100 (секунди:rps)."""
    segs = []
    for part in text.split(","):
        d, _, r = part.partition(":")
        segs.append((float(d), float(r)))
    return segs


def seg_windows(segments):
    """Початок і кінець кожного відрізка в секундах від початку прогону."""
    out, t = [], 0.0
    for d, r in segments:
        out.append((t, t + d, r))
        t += d
    return out


def rate_at(windows, t):
    for begin, end, rate in windows:
        if begin <= t < end:
            return rate
    return 0.0


# робочий процес

async def worker_main(a, queue, stop):
    (base, path_t, ids, start_wall, widx, nworkers, seed, segments,
     conns_share, req_per_conn) = a
    u = urlparse(base)
    host, port = u.hostname, u.port or 80
    conns = Connections(host, port, conns_share, req_per_conn)
    rng = random.Random(seed)
    # perf_counter, а не годинник циклу подій: на Windows time.monotonic має крок 15,6 мс
    t0 = time.perf_counter() + (start_wall - time.time())
    buckets = Buckets()
    pending = set()

    def done_cb(task, sched):
        pending.discard(task)
        if task.cancelled():
            return
        try:
            status = task.result()
        except BaseException:
            status = STATUS_CONNERR
        finish = time.perf_counter() - t0
        buckets.add_done(int(finish), status, finish - sched)

    async def flusher():
        while True:
            await asyncio.sleep(0.2)
            now = time.perf_counter() - t0
            for row in buckets.drain(int(now - FLUSH_LAG)):
                queue.put((widx, row))

    flush_task = asyncio.create_task(flusher())
    for begin, end, rate in seg_windows(segments):
        if stop.is_set():
            break
        n = int(round(rate * (end - begin)))
        j = widx
        while j < n:
            if stop.is_set():
                break
            now = time.perf_counter() - t0
            if begin + j / rate > now:
                await asyncio.sleep(min(begin + j / rate - now, 0.002))
                continue
            # пачкою: на високих інтенсивностях інтервал менший за крок таймера Windows
            while j < n:
                sched = begin + j / rate
                if sched > now:
                    break
                j += nworkers
                buckets.add_sent(int(now))
                path = path_t.format(id=rng.choice(ids)) if ids and "{id}" in path_t else path_t
                budget = TIMEOUT - (now - sched)
                if budget <= 0:
                    buckets.add_done(int(sched + TIMEOUT), STATUS_TIMEOUT, TIMEOUT)
                    continue
                task = asyncio.create_task(fetch(conns, host, path, budget))
                pending.add(task)
                task.add_done_callback(lambda t, sc=sched: done_cb(t, sc))

    if pending:
        await asyncio.wait(pending, timeout=TIMEOUT + 1.0)
    flush_task.cancel()
    for row in buckets.drain(10 ** 9):
        queue.put((widx, row))
    queue.put(("conns", widx, {"limit": conns.limit, "opened": conns.opened_total,
                               "recycled": conns.recycled}))
    conns.close_all()


def worker(a, queue, stop):
    sharpen_timer()
    asyncio.run(worker_main(a, queue, stop))


# батьківський процес

def second_rows(col, windows, total_s):
    """Посекундний ряд: задана і досягнута інтенсивність, коди, p99 клієнта."""
    rows = []
    for sec in range(int(total_s)):
        r = col.rows[sec]
        rows.append({
            "t_s": sec,
            "target_rps": rate_at(windows, sec + 0.5),
            "sent": r["sent"],
            "achieved_rps": r["c2xx"],
            "non2xx": r["non2xx"],
            "connerr": r["connerr"],
            "p50_ms": pct(r["lat"], 50),
            "p95_ms": pct(r["lat"], 95),
            "p99_ms": pct(r["lat"], 99),
        })
    return rows


def segment_stats(rows, windows, transient_s=10.0):
    """Середні за відрізком без перехідної частини на його початку."""
    out = []
    for i, (begin, end, rate) in enumerate(windows):
        secs = [r for r in rows if begin + transient_s <= r["t_s"] < end]
        ach = [r["achieved_rps"] for r in secs]
        lat = [r["p99_ms"] for r in secs if r["p99_ms"] is not None]
        resp = sum(r["achieved_rps"] + r["non2xx"] for r in secs)
        out.append({
            "segment": i,
            "window_s": [begin, end],
            "target_rps": rate,
            "achieved_rps": statistics.fmean(ach) if ach else 0.0,
            "achieved_sd": statistics.stdev(ach) if len(ach) > 1 else 0.0,
            "p99_ms_mean": statistics.fmean(lat) if lat else None,
            "p99_ms_max": max(lat) if lat else None,
            "non2xx": sum(r["non2xx"] for r in secs),
            "non2xx_share": (sum(r["non2xx"] for r in secs) / resp) if resp else None,
        })
    return out


def run(base, path_t, ids, segments, out=None, procs=None, label=None,
        conns=CONNS, req_per_conn=REQ_PER_CONN):
    procs = procs or max(1, (os.cpu_count() or 2) // 2)
    procs = min(procs, conns)
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    stop = ctx.Event()
    start_wall = time.time() + START_DELAY + 0.3 * procs
    shares = [conns // procs + (1 if k < conns % procs else 0) for k in range(procs)]
    args = [(base, path_t, ids, start_wall, k, procs, 7000 + k, segments,
             shares[k], req_per_conn) for k in range(procs)]
    workers = [ctx.Process(target=worker, args=(a, queue, stop), daemon=True) for a in args]
    for w in workers:
        w.start()

    windows = seg_windows(segments)
    total_s = windows[-1][1]
    col = Collector()
    deadline = start_wall + total_s + FLUSH_LAG + 1.0
    next_print = start_wall + 10.0
    while time.time() < deadline:
        col.absorb(queue)
        if time.time() >= next_print:
            now = int(time.time() - start_wall)
            print(f"  {now} с із {int(total_s)}", flush=True)
            next_print += 10.0
        time.sleep(0.05)

    stop.set()
    until = time.time() + TIMEOUT + 3.0
    while time.time() < until:
        col.absorb(queue)
        time.sleep(0.05)
    for w in workers:
        w.join(timeout=5)
        if w.is_alive():
            w.terminate()
    col.absorb(queue)

    rows = second_rows(col, windows, total_s)
    segs = segment_stats(rows, windows)
    summary = {
        "label": label,
        "base": base, "path": path_t, "procs": procs,
        "segments": [{"duration_s": d, "target_rps": r} for d, r in segments],
        "connections": {"total": conns, "per_proc": shares, "req_per_conn": req_per_conn,
                        "opened": sum(c["opened"] for c in col.conns),
                        "recycled": sum(c["recycled"] for c in col.conns)},
        "timeout_s": TIMEOUT,
        "segment_stats": segs,
        "non2xx_total": sum(r["non2xx"] for r in rows),
        "connerr_total": sum(r["connerr"] for r in rows),
        "start_wall": start_wall,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(start_wall)),
    }
    if out:
        save(out, rows, summary)
    return summary


def save(out, rows, summary):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    with out.with_suffix(".csv").open("w", encoding="utf-8", newline="") as f:
        f.write("t_s,target_rps,sent,achieved_rps,non2xx,connerr,p50_ms,p95_ms,p99_ms\n")
        for r in rows:
            f.write(f"{r['t_s']},{r['target_rps']:.1f},{r['sent']},{r['achieved_rps']},"
                    f"{r['non2xx']},{r['connerr']},"
                    f"{fmt(r['p50_ms'])},{fmt(r['p95_ms'])},{fmt(r['p99_ms'])}\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--base", default=default_base())
    ap.add_argument("--path", default="/donor-registry/{id}")
    ap.add_argument("--ids", default=str(Path(__file__).with_name("ids.json")))
    ap.add_argument("--out", help="префікс файлів результату без розширення")
    ap.add_argument("--procs", type=int, default=None)
    ap.add_argument("--x1", type=float, help="X(1) з кроку 7, rps")
    ap.add_argument("--xr", type=float, help="X(R) з кроку 7, rps")
    ap.add_argument("--spike-factor", type=float, default=SPIKE_FACTOR,
                    help="рівень стрибка за варіантом, у частках X(1) (варіант 19: 1,5)")
    ap.add_argument("--profile", help="явні відрізки «30:100,60:500,90:100» замість X(1) і X(R)")
    ap.add_argument("--conns", type=int, default=CONNS)
    ap.add_argument("--req-per-conn", type=int, default=REQ_PER_CONN)
    ap.add_argument("--label")
    a = ap.parse_args()

    if a.profile:
        segments = parse_profile(a.profile)
    elif a.x1 and a.xr:
        segments = build_profile(a.x1, a.xr, a.spike_factor)
    else:
        ap.error("потрібно або --profile, або пара --x1 і --xr")

    ids_file = Path(a.ids)
    ids = json.loads(ids_file.read_text()) if ("{id}" in a.path and ids_file.exists()) else []
    print("профіль: " + ", ".join(f"{d:.0f} с на {r:.0f} rps" for d, r in segments), flush=True)
    s = run(a.base, a.path, ids, segments, a.out, a.procs, a.label, a.conns, a.req_per_conn)
    print(json.dumps({k: v for k, v in s.items() if k != "segment_stats"},
                     indent=2, ensure_ascii=False))
    for m in s["segment_stats"]:
        print(f"відрізок {m['segment']}: задано {m['target_rps']:.0f}, досягнуто "
              f"{m['achieved_rps']:.1f} ±{m['achieved_sd']:.1f}, "
              f"p99 {m['p99_ms_mean'] and round(m['p99_ms_mean'])} мс, "
              f"не-2xx {m['non2xx']}")
