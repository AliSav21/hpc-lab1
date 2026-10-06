"""Генератор навантаження: відкритий і закритий контур. Лише стандартна бібліотека."""
import sys as _sys
_sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows: cp1252
import argparse
import asyncio
import gzip
import http.client
import json
import multiprocessing as mp
import os
import random
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

TIMEOUT = 5.0   # таймаут одного запиту, с
DRAIN = 15.0    # дочікування після прогону, с
START_DELAY = 3.0

_local = threading.local()


def http_get(host, port, path):
    """GET по keep-alive. Повертає (status, X-Instance); 0 — помилка."""
    for attempt in (0, 1):
        conn = getattr(_local, "conn", None)
        reused = conn is not None
        if conn is None:
            conn = _local.conn = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            resp.read()
            return resp.status, resp.getheader("X-Instance", "")
        except Exception:
            conn.close()
            _local.conn = None
            if not (reused and attempt == 0):  # повтор, якщо впало старе зʼєднання
                return 0, ""
    return 0, ""


def _target(base):
    u = urlparse(base)
    return u.hostname, u.port or 80


def open_worker(a):
    base_url, path_t, ids, rate, warmup, duration, start, threads, k, procs, seed = a
    host, port = _target(base_url)
    rng = random.Random(seed)
    total = warmup + duration
    step = procs / rate                       # інтервал між запитами одного процесу
    n = int(rate * total / procs)
    t0 = time.perf_counter() + (start - time.time())  # початок прогону в perf_counter
    records = []                              # (i, t_rel, latency, status, instance)

    def task(i, t_rel, path):
        status, inst = http_get(host, port, path)
        records.append((i, t_rel, time.perf_counter() - t0 - t_rel, status, inst))

    ex = ThreadPoolExecutor(threads)
    futs = []
    for i in range(n):
        t_rel = k / rate + i * step
        path = path_t.format(id=rng.choice(ids)) if ids else path_t
        delay = t0 + t_rel - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        futs.append((t_rel, ex.submit(task, i, t_rel, path)))
    deadline = t0 + total + DRAIN
    for _, f in futs:
        left = deadline - time.perf_counter()
        if left <= 0:
            break
        try:
            f.result(timeout=left)
        except Exception:
            pass
    ex.shutdown(wait=False, cancel_futures=True)
    done = list(records)
    seen = {r[0] for r in done}
    for i, (t_rel, _) in enumerate(futs):
        if i not in seen:  # не встигло за DRAIN
            done.append((i, t_rel, deadline - t0 - t_rel, -1, ""))
    for i in range(len(futs), n):  # не відправлено взагалі
        done.append((i, k / rate + i * step, deadline - t0 - (k / rate + i * step), -1, ""))
    return done


def closed_worker(a):
    base_url, path_t, ids, conc, warmup, duration, start, seed = a
    host, port = _target(base_url)
    end_rel = warmup + duration
    t0 = time.perf_counter() + (start - time.time())
    records = []

    def loop(tid):
        rng = random.Random(seed * 1000 + tid)
        while True:
            t_rel = time.perf_counter() - t0
            if t_rel < 0:
                time.sleep(-t_rel)
                continue
            if t_rel >= end_rel:
                return
            path = path_t.format(id=rng.choice(ids)) if ids else path_t
            status, inst = http_get(host, port, path)
            records.append((0, t_rel, time.perf_counter() - t0 - t_rel, status, inst))

    with ThreadPoolExecutor(conc) as ex:
        list(ex.map(loop, range(conc)))
    return records


# Закритий контур на тисячах користувачів: потоки не тягнуть, тому корутини asyncio.
CRLF = chr(13) + chr(10)
CRLFB = CRLF.encode()


async def _au_read(reader):
    """Читає відповідь HTTP/1.1, повертає код статусу."""
    status = int((await reader.readuntil(CRLFB)).split(b" ")[1])
    clen = 0
    while True:
        h = await reader.readuntil(CRLFB)
        if h == CRLFB:
            break
        k, _, v = h.partition(b":")
        if k.lower() == b"content-length":
            clen = int(v.strip())
    if clen:
        await reader.readexactly(clen)
    return status


async def _au_user(host, port, path_t, ids, seed, t0, end_rel, records, timeout):
    """Віртуальний користувач: наступний запит після відповіді на попередній."""
    rng = random.Random(seed)
    loop = asyncio.get_running_loop()
    reader = writer = None
    while True:
        t_rel = loop.time() - t0
        if t_rel < 0:
            await asyncio.sleep(-t_rel)
            continue
        if t_rel >= end_rel:
            break
        path = path_t.format(id=rng.choice(ids)) if ids else path_t
        try:
            if writer is None:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
            writer.write(f"GET {path} HTTP/1.1{CRLF}Host: {host}{CRLF}{CRLF}".encode())
            await writer.drain()
            status = await asyncio.wait_for(_au_read(reader), timeout)
        except Exception:
            status = 0
            if writer is not None:
                writer.close()
                writer = None
        records.append((0, t_rel, loop.time() - t0 - t_rel, status, ""))
    if writer is not None:
        writer.close()


def closed_worker_async(a):
    base_url, path_t, ids, conc, warmup, duration, start, seed, timeout = a
    host, port = _target(base_url)
    records = []

    async def main():
        t0 = asyncio.get_running_loop().time() + (start - time.time())
        await asyncio.gather(*[
            _au_user(host, port, path_t, ids, seed * 100000 + i, t0, warmup + duration,
                     records, timeout)
            for i in range(conc)
        ])

    asyncio.run(main())
    return records


def pct(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def summarize(records, warmup, duration):
    w0, w1 = warmup, warmup + duration
    win = [r for r in records if w0 <= r[1] < w1]
    lats = sorted(r[2] * 1000 for r in win)
    completed = sum(1 for r in records if r[3] > 0 and w0 <= r[1] + r[2] < w1)
    non2xx = sum(1 for r in win if not 200 <= r[3] < 300)
    # L: інтеграл перекриття інтервалів / тривалість вікна
    area = 0.0
    events = []
    for _, t, lat, *_ in records:
        s, e = t, t + lat
        area += max(0.0, min(e, w1) - max(s, w0))
        if e > w0 and s < w1:
            events += [(max(s, w0), 1), (min(e, w1), -1)]
    events.sort(key=lambda x: (x[0], x[1]))
    cur = peak = 0
    for _, d in events:
        cur += d
        peak = max(peak, cur)
    return {
        "requests_in_window": len(win),
        "achieved_rps": completed / duration,
        "p50_ms": pct(lats, 50), "p95_ms": pct(lats, 95), "p99_ms": pct(lats, 99),
        "mean_ms": sum(lats) / len(lats) if lats else None,
        "non2xx_share": non2xx / len(win) if win else None,
        "L_mean": area / duration,
        "L_max": peak,
        "instances": dict(Counter(r[4] for r in win if r[3] > 0)),
    }


def save(out, records, summary):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    with gzip.open(out.with_suffix(".csv.gz"), "wt") as f:
        f.write("t_sched_s,latency_s,status,instance\n")
        for _, t, lat, st, inst in sorted(records, key=lambda r: r[1]):
            f.write(f"{t:.6f},{lat:.6f},{st},{inst}\n")


def default_procs():
    return max(1, (os.cpu_count() or 2) // 2)


def run_open(base, path, ids, rate, warmup, duration, procs=None, threads=64, out=None):
    procs = procs or default_procs()
    start = time.time() + START_DELAY + 0.3 * procs
    args = [(base, path, ids, rate, warmup, duration, start, threads, k, procs, 1000 + k)
            for k in range(procs)]
    with mp.get_context("spawn").Pool(procs) as pool:
        records = [r for part in pool.map(open_worker, args) for r in part]
    summary = {"mode": "open", "base": base, "path": path, "rate": rate, "warmup_s": warmup,
               "duration_s": duration, "procs": procs, "threads_per_proc": threads,
               **summarize(records, warmup, duration)}
    if out:
        save(out, records, summary)
    return summary


def run_closed(base, path, ids, concurrency, warmup, duration, procs=None, out=None,
               use_async=None, timeout=90.0):
    # до сотень користувачів потоки, далі asyncio
    use_async = (concurrency > 500) if use_async is None else use_async
    procs = min(procs or default_procs(), concurrency)
    start = time.time() + START_DELAY + 0.3 * procs
    shares = [concurrency // procs + (1 if k < concurrency % procs else 0) for k in range(procs)]
    if use_async:
        worker = closed_worker_async
        args = [(base, path, ids, shares[k], warmup, duration, start, 2000 + k, timeout)
                for k in range(procs)]
    else:
        worker = closed_worker
        args = [(base, path, ids, shares[k], warmup, duration, start, 2000 + k) for k in range(procs)]
    with mp.get_context("spawn").Pool(procs) as pool:
        records = [r for part in pool.map(worker, args) for r in part]
    summary = {"mode": "closed", "client": "asyncio" if use_async else "threads",
               "base": base, "path": path, "concurrency": concurrency,
               "warmup_s": warmup, "duration_s": duration, "procs": procs,
               **summarize(records, warmup, duration)}
    if out:
        save(out, records, summary)
    return summary


def load_ids(path):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else []


def default_base():
    """Адреса стенду з .env; 127.0.0.1 замість localhost через IPv4/IPv6."""
    env = Path(__file__).resolve().parent.parent / ".env"
    port = "18080"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("LB_PORT="):
                port = line.split("=", 1)[1].strip()
    return "http://127.0.0.1:" + port


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--base", default=default_base())
    ap.add_argument("--path", default="/donor-registry/{id}")
    ap.add_argument("--ids", default=str(Path(__file__).with_name("ids.json")))
    ap.add_argument("--rate", type=float, help="запитів/с (відкритий контур)")
    ap.add_argument("--concurrency", type=int, help="одночасних запитів (закритий контур)")
    ap.add_argument("--warmup", type=float, default=10)
    ap.add_argument("--duration", type=float, default=30)
    ap.add_argument("--procs", type=int, default=None)
    ap.add_argument("--threads", type=int, default=64)
    ap.add_argument("--out")
    a = ap.parse_args()
    ids = load_ids(a.ids) if "{id}" in a.path else []
    if a.concurrency:
        s = run_closed(a.base, a.path, ids, a.concurrency, a.warmup, a.duration, a.procs, a.out)
    else:
        s = run_open(a.base, a.path, ids, a.rate, a.warmup, a.duration, a.procs, a.threads, a.out)
    print(json.dumps(s, indent=2))
