"""Збирає доказові виводи команд у results/evidence.md (стенд має бути піднятий).

    python scripts/collect_evidence.py
"""
import socket
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
ENV = dict(l.split("=", 1) for l in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
           if "=" in l and not l.startswith("#"))
PASSWORD = ENV["POSTGRES_PASSWORD"].strip()
PORT = ENV.get("LB_PORT", "8080").strip()
OUT = []


def sh(title, cmd, note=""):
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = (r.stdout + r.stderr).strip() or "(порожній вивід)"
    text = text.replace(PASSWORD, "***")  # пароль не потрапляє у звіт
    OUT.append(f"## {title}\n`{' '.join(cmd)}`\n" + (f"\n{note}\n" if note else "") + f"\n```\n{text}\n```\n")


def web():
    names = subprocess.run(["docker", "compose", "ps", "--format", "{{.Name}}", "web"], cwd=ROOT,
                           capture_output=True, text=True).stdout.split()
    return names[0] if names else "donors-web-1"


w = web()
sh("Контейнери, порти і стан здоровʼя (публікує порт лише lb)",
   ["docker", "compose", "ps", "--format", "table {{.Name}}\t{{.Status}}\t{{.Ports}}"])
sh("Розмір фінального образу", ["docker", "image", "inspect", "donor-registry:latest", "--format",
                                "{{.Size}} байт"])
sh("Шари образу", ["docker", "image", "history", "donor-registry:latest", "--format",
                   "{{.Size}}\t{{.CreatedBy}}"])
sh("Процес у контейнері: uid і користувач", ["docker", "exec", w, "id"])
sh("curl, wget, gcc в образі відсутні (додаткова вимога: healthcheck без curl/wget)",
   ["docker", "exec", w, "sh", "-c", "for c in curl wget gcc; do command -v $c || echo \"$c: немає\"; done"])
sh("Healthcheck сервісу (команда і стан)", ["docker", "inspect", w, "--format",
                                             "{{json .Config.Healthcheck.Test}} => {{.State.Health.Status}}"])
sh("Обмеження ресурсів web", ["docker", "inspect", w, "--format",
                              "NanoCpus={{.HostConfig.NanoCpus}} Memory={{.HostConfig.Memory}}"])

# пароль не в образі, історії, репозиторії
hist = subprocess.run(["docker", "image", "history", "donor-registry:latest", "--no-trunc"],
                      capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
found_in = [f for f in tracked if (ROOT / f).is_file()
            and PASSWORD in (ROOT / f).read_text(encoding="utf-8", errors="ignore")]
env_in_img = subprocess.run(["docker", "image", "inspect", "donor-registry:latest", "--format", "{{.Config.Env}}"],
                            capture_output=True, text=True).stdout
OUT.append("## Пароль не в образі, не в історії збірки, не в репозиторії\n```\n"
           f"у history образу: {'ЗНАЙДЕНО' if PASSWORD in hist else 'не знайдено'}\n"
           f"у змінних образу: {'ЗНАЙДЕНО' if PASSWORD in env_in_img else 'не знайдено'}\n"
           f"у файлах репозиторію (git ls-files): {found_in or 'не знайдено'}\n```\n")
sh(".env не комітиться", ["git", "check-ignore", "-v", ".env"])

# база не публікується на хост
def reachable(port):
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


OUT.append("## Порти бази і вебінстансів з хоста недоступні, балансувальник доступний\n```\n"
           f"127.0.0.1:5432 (Postgres): {'ДОСТУПНО' if reachable(5432) else 'підключення немає'}\n"
           f"127.0.0.1:8000 (web):      {'ДОСТУПНО' if reachable(8000) else 'підключення немає'}\n"
           f"127.0.0.1:{PORT} (lb):       {'доступно' if reachable(int(PORT)) else 'НЕДОСТУПНО'}\n```\n")
sh("База доступна з контейнера сервісу за іменем db", ["docker", "exec", w, "python", "-c",
   "import socket;s=socket.create_connection(('db',5432),2);print('db:5432 підключення є')"])
sh("Репліки: скільки вебінстансів зараз", ["docker", "compose", "ps", "web", "--format", "{{.Name}}"])

(ROOT / "results" / "evidence.md").write_text("# Докази\n\n" + "\n".join(OUT), encoding="utf-8")
print("\n".join(OUT))
