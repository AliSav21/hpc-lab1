# Реєстр донорів: CRUD-сервіс (варіант 20)

Ресурс `/donor-registry`, FastAPI + PostgreSQL 16 + nginx, Docker Compose.
Звіт: [REPORT.md](REPORT.md).

## Запуск (одна команда)

```bash
cp .env.example .env        # один раз; задай POSTGRES_PASSWORD у .env
docker compose up -d --build
```

Сервіс доступний на `http://localhost:8080` (порт `LB_PORT`). Тільки балансувальник публікує порт.

Кількість вебінстансів задається змінною, без правок файлів:

```bash
WEB_REPLICAS=2 docker compose up -d
docker compose restart lb     # nginx резолвить web лише на старті, після зміни реплік потрібен перезапуск
```

Зупинка: `docker compose down` (дані лишаються), `docker compose down -v` (разом з томом).

## Вимірювання

Лише стандартна бібліотека Python 3.10+, генератор у `loadgen/`. Запускати з кореня репозиторію.

```bash
python scripts/startup_time.py                      # розмір/час старту: 3 повтори, медіана (робить down -v!)
python scripts/verify_flow.py lifecycle             # етап 9, журнал у results/verify_log.md

python loadgen/run_series.py selfcheck              # перевірка самого генератора (/_null)
python loadgen/run_series.py series --instances 1   # 10/100/500 rps x 3 повтори, прогрів 10 с + 30 с
python loadgen/run_series.py series --instances 2
python loadgen/run_series.py distribution           # розподіл запитів між двома інстансами
python loadgen/run_series.py closed --rate 500      # закритий контур (рівень, де 1 інстанс не тримає ціль)
python loadgen/aggregate.py                         # медіани -> results/table.md
python loadgen/calc.py                              # три розрахунки -> results/calculations.md
```

Сирі результати кожного прогону лежать у `results/` (`*.json` підсумок, `*.csv.gz` кожен запит).
Генератор і стенд на одній машині: перед серією закрий важкі програми, на 500 rps вони конкурують за ядра.

## Структура

| Шлях | Призначення |
|---|---|
| `app/main.py` | сервіс: 6 ендпоінтів, валідація |
| `db/schema.sql` | схема, накочується з `/docker-entrypoint-initdb.d` |
| `Dockerfile`, `.dockerignore` | багатоетапна збірка на `python:3.12-slim` |
| `docker-compose.yml`, `lb/nginx.conf` | стенд: db, web (N реплік), lb |
| `loadgen/` | генератор (відкритий і закритий контур), seed, оркестрація, розрахунки |
| `scripts/` | вимірювання часу старту, журнал перевірки |
