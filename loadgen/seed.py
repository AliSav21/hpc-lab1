"""Заносить N записів у реєстр через API і зберігає їх ідентифікатори в loadgen/ids.json."""
import sys as _sys
_sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows за замовчуванням cp1252
import argparse
import http.client
import json
import random
import uuid
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

IDS_FILE = Path(__file__).with_name("ids.json")


def call(base, method, path, body=None):
    u = urlparse(base)
    conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=10)
    conn.request(method, path, json.dumps(body) if body is not None else None,
                 {"Content-Type": "application/json"})
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, json.loads(data) if data else None


def fake_donor(i, tag):
    year = date.today().year - random.randint(18, 55)
    return {
        "donor_code": f"SEED-{tag}-{i:04d}",
        "birth_year": year,
        "blood_group": random.choice(["O+", "O-", "A+", "A-", "B+", "B-", "AB+", "AB-"]),
        "hla_typing": ["A*02:01", "B*07:02", "DRB1*15:01"],
        "registered_on": (date.today() - timedelta(days=random.randint(0, 1000))).isoformat(),
        "available": random.random() < 0.7,
    }


def ensure_ids(base, n=100):
    """Гарантує щонайменше n записів, повертає їхні ідентифікатори й пише loadgen/ids.json."""
    _, page = call(base, "GET", f"/donor-registry?limit={n}")
    ids = [x["id"] for x in page["items"]]
    tag = uuid.uuid4().hex[:6]
    for i in range(n - len(ids)):
        status, body = call(base, "POST", "/donor-registry", fake_donor(i, tag))
        assert status == 201, (status, body)
        ids.append(body["id"])
    IDS_FILE.write_text(json.dumps(ids))
    return ids


def default_base():
    """Адреса стенду з .env (LB_PORT); явний 127.0.0.1, щоб не було неоднозначності IPv4/IPv6."""
    env = Path(__file__).resolve().parent.parent / ".env"
    port = "18080"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("LB_PORT="):
                port = line.split("=", 1)[1].strip()
    return "http://127.0.0.1:" + port


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=default_base())
    ap.add_argument("--n", type=int, default=100)
    a = ap.parse_args()
    print(f"{len(ensure_ids(a.base, a.n))} ids -> {IDS_FILE}")
