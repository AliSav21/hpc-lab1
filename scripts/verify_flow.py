"""Етап 9: повний цикл запитів із журналом -> results/verify_log.md.

    python scripts/verify_flow.py            # цикл запитів
    python scripts/verify_flow.py lifecycle  # + перевірка down/up і down -v/up
"""
import sys as _sys
_sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows за замовчуванням cp1252
import json
import subprocess
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = next((l.split("=", 1)[1].strip() for l in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
             if l.startswith("LB_PORT=")), "8080")
BASE = f"http://127.0.0.1:{PORT}"
LOG = []


def call(method, path, body=None, note=""):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=10)
        status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    text = raw.decode() if raw else ""
    LOG.append(f"### {note}\n`{method} {path}`" + (f"\n```json\n{json.dumps(body, ensure_ascii=False)}\n```" if body else "")
               + f"\n→ **{status}**\n```json\n{text or '(порожнє тіло)'}\n```\n")
    print(f"{status}  {method} {path}  {note}")
    return status, json.loads(text) if text else None


def donor(**kw):
    d = {"donor_code": f"D-{int(time.time())}", "birth_year": date.today().year - 30,
         "blood_group": "O+", "hla_typing": ["A*02:01", "B*07:02"],
         "registered_on": "2026-01-15", "available": True}
    d.update(kw)
    return d


def dc(*args):
    subprocess.run(["docker", "compose", *args], cwd=ROOT, check=True, capture_output=True)


def wait():
    while True:
        try:
            if urllib.request.urlopen(BASE + "/healthz", timeout=2).status == 200:
                return
        except Exception:
            time.sleep(0.2)


def flow():
    call("GET", "/healthz", note="готовність")
    st, d = call("POST", "/donor-registry", donor(), "створення")
    i = d["id"]
    call("POST", "/donor-registry", donor(birth_year=date.today().year - 10), "400: вік 10 років")
    call("POST", "/donor-registry", donor(birth_year=date.today().year - 70), "400: вік 70 років")
    call("POST", "/donor-registry", donor(blood_group="X"), "400: невалідна група крові")
    call("POST", "/donor-registry", donor(donor_code=d["donor_code"]), "409: дубль donor_code")
    call("GET", "/donor-registry?limit=5", note="перелік")
    call("GET", "/donor-registry?available=false&limit=5", note="перелік з фільтром available=false")
    call("GET", "/donor-registry?limit=1000", note="400: limit понад максимум")
    call("GET", f"/donor-registry/{i}", note="за ідентифікатором")
    call("PUT", f"/donor-registry/{i}", donor(donor_code=d["donor_code"], available=False, blood_group="AB-"),
         "повна заміна")
    call("PUT", f"/donor-registry/{i}", donor(birth_year=1900), "400: PUT з невалідним віком")
    call("PUT", "/donor-registry/999999999", donor(), "404: PUT неіснуючого")
    call("DELETE", f"/donor-registry/{i}", note="видалення")
    call("DELETE", f"/donor-registry/{i}", note="повторне видалення → 404")
    call("GET", f"/donor-registry/{i}", note="GET видаленого → 404")
    return d


def lifecycle():
    st, d = call("POST", "/donor-registry", donor(donor_code="PERSIST-1"), "запис перед down")
    dc("down"); dc("up", "-d"); wait()
    st, _ = call("GET", f"/donor-registry/{d['id']}", note="після down + up запис на місці (очікуємо 200)")
    dc("down", "-v"); dc("up", "-d"); wait()
    _, page = call("GET", "/donor-registry", note="після down -v + up база порожня (total = 0)")
    print("total після down -v:", page["total"])


if __name__ == "__main__":
    flow()
    if len(sys.argv) > 1 and sys.argv[1] == "lifecycle":
        lifecycle()
    (ROOT / "results" / "verify_log.md").write_text("\n".join(LOG), encoding="utf-8")
