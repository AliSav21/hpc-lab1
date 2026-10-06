# Як цим користуватися

Детальна інструкція до стенду лабораторної роботи 1 (варіант 20, ресурс `/donor-registry`).
Коротка інструкція запуску: [README.md](README.md). Сам звіт: [REPORT.md](REPORT.md).

Усі команди виконуються **з кореня репозиторію**. У PyCharm це вкладка Terminal внизу;
перед командами має стояти префікс `(.venv)`.

---

## 1. Що де лежить

| Шлях | Що це |
|---|---|
| `app/main.py` | сам сервіс: 6 ендпоінтів, валідація, пул з'єднань до бази |
| `db/schema.sql` | таблиця `donor_registry`; накочується автоматично при першому старті бази |
| `Dockerfile` | багатоетапна збірка образу сервісу |
| `docker-compose.yml` | стенд: база `db`, вебінстанси `web`, балансувальник `lb` |
| `lb/nginx.conf` | конфіг балансувальника, лог із полем `upstream=` |
| `.env` | **локальний**, не в git: пароль бази, порт, кількість реплік |
| `.env.example` | шаблон `.env` для чужої машини |
| `loadgen/gen.py` | генератор навантаження (відкритий і закритий контур) |
| `loadgen/seed.py` | заносить 100 записів і зберігає їх id у `loadgen/ids.json` |
| `loadgen/run_series.py` | оркестрація: піднімає стенд і проганяє серії |
| `loadgen/aggregate.py` | медіани за повторами, пише `results/table.md` |
| `loadgen/calc.py` | три розрахунки, пише `results/calculations.md` |
| `scripts/startup_time.py` | час від `up` до першої відповіді 200 на `/healthz` |
| `scripts/verify_flow.py` | журнал перевірки (етап 9) |
| `scripts/collect_evidence.py` | збирає докази виконання вимог у `results/evidence.md` |
| `results/` | усі сирі й зведені результати |

---

## 2. Разова підготовка

Робиться один раз. Якщо все вже працює, пропусти цей розділ.

**1. Docker Desktop** має бути запущений (зелений статус `Engine running`). Перевірка:

```bash
docker compose version
```

**2. Залежності Python.** Потрібні лише для генератора і скриптів; сам сервіс працює в Docker:

```bash
pip install -r requirements.txt
```

**3. Файл `.env`.** Його немає в git, бо там пароль:

```bash
cp .env.example .env
```

Потім відкрий `.env` і заміни `change-me` на будь-який пароль. Має вийти приблизно так:

```
POSTGRES_USER=donors
POSTGRES_PASSWORD=твій-пароль
POSTGRES_DB=donors
WEB_REPLICAS=1
LB_PORT=18080
```

> **Чому порт 18080, а не 8080.** На цій машині 8080 займає стороння програма `ApplicationWebServer`.
> Якщо повернути 8080, частина запитів піде до неї і повертатиме 404. Детальніше в розділі 0 звіту.

---

## 3. Стенд: підняти, перевірити, зупинити

### Підняти з одним інстансом

```bash
docker compose up -d --build
```

`--build` потрібен лише після зміни коду сервісу, далі можна без нього.

### Підняти з двома інстансами

```bash
WEB_REPLICAS=2 docker compose up -d
```

Жодних правок у файлах не потрібно: кількість задається змінною.

> Якщо змінюєш кількість реплік на **вже запущеному** стенді, додай `docker compose restart lb`.
> nginx запам'ятовує адреси вебінстансів під час свого старту. При підйомі з нуля це не потрібно.

### Перевірити, що живий

```bash
docker compose ps
```

```bash
curl http://127.0.0.1:18080/healthz
```

Має бути `{"status":"ok"}`, а всі контейнери в стані `healthy`.

### Зупинити

```bash
docker compose down
```

Дані бази **залишаються**: вони лежать у томі `pgdata`.

```bash
docker compose down -v
```

Видаляє і том. База стане порожньою, схема накотиться наново при наступному `up`.

### Подивитись логи

```bash
docker compose logs web --tail 50
```

```bash
docker compose logs lb --tail 50
```

У лозі `lb` поле `upstream=` показує, на який саме інстанс пішов запит.

---

## 4. Ручна перевірка API

Порт береться з `.env`, тобто `18080`.

**Створити запис.** Збережи тіло в файл `sample.json`:

```json
{
  "donor_code": "D-001",
  "birth_year": 1996,
  "blood_group": "O+",
  "hla_typing": ["A*02:01", "B*07:02"],
  "registered_on": "2026-01-15",
  "available": true
}
```

```bash
curl -X POST http://127.0.0.1:18080/donor-registry -H "Content-Type: application/json" -d @sample.json
```

**Інші операції** (підстав свій `id` замість `1`):

```bash
curl "http://127.0.0.1:18080/donor-registry?limit=5"
```

```bash
curl "http://127.0.0.1:18080/donor-registry?available=true&limit=5"
```

```bash
curl http://127.0.0.1:18080/donor-registry/1
```

```bash
curl -X DELETE -i http://127.0.0.1:18080/donor-registry/1
```

**Автоматично весь цикл** (створення, перелік, за ідентифікатором, заміна, видалення, повторне
видалення, перевірка перезапусків) із записом журналу у `results/verify_log.md`:

```bash
python scripts/verify_flow.py lifecycle
```

> Слово `lifecycle` наприкінці означає ще й перевірку `down` / `down -v`, тобто база буде **стерта**.
> Без цього слова перевіряється лише цикл запитів, дані лишаються цілими.

---

## 5. Вимірювання

### Порядок і приблизний час

| Крок | Команда | Триває |
|---|---|---|
| 1. Перевірка генератора | `python loadgen/run_series.py selfcheck` | 1 хв |
| 2. Серія, 1 інстанс | `python loadgen/run_series.py series --instances 1` | 7 хв |
| 3. Серія, 2 інстанси | `python loadgen/run_series.py series --instances 2` | 7 хв |
| 4. Верхні рівні, 1 інстанс | `python loadgen/run_series.py series --instances 1 --rates 700 800 1000 --stats` | 7 хв |
| 5. Верхні рівні, 2 інстанси | `python loadgen/run_series.py series --instances 2 --rates 700 800 1000 --stats` | 7 хв |
| 6. Розподіл по інстансах | `python loadgen/run_series.py distribution` | 1 хв |
| 7. Закритий контур | `python loadgen/run_series.py closed --rate 700` | 4 хв |
| 8. Зведення | `python loadgen/aggregate.py` | миттєво |
| 9. Розрахунки | `python loadgen/calc.py` | миттєво |

Скрипт сам піднімає потрібну кількість інстансів і сам засіває 100 записів. Вручну нічого робити не треба.

### Перед серією

Закрий усе зайве: браузер, Discord, Telegram. Генератор і стенд працюють на одній машині і
конкурують за ядра, тому фонові програми псують результат. На верхніх рівнях різниця помітна.

### Що означають прапорці

- `--instances 1` або `--instances 2` — скільки вебінстансів підняти.
- `--rates 10 100 500` — рівні інтенсивності в запитах за секунду.
- `--stats` — паралельно писати `docker stats` у `results/stats/`; потрібно, щоб показати, що саме впирається.
- `--warmup 10 --duration 30` — прогрів і вимірювання в секундах (це значення за замовчуванням).

### Окремий прогін без оркестрації

```bash
python loadgen/seed.py
```

```bash
python loadgen/gen.py --rate 100 --duration 30 --out results/proba
```

```bash
python loadgen/gen.py --concurrency 110 --duration 30
```

Перший це відкритий контур із заданою інтенсивністю, другий це закритий контур із фіксованою
кількістю одночасних запитів.

---

## 6. Інші вимірювання

**Розмір образу:**

```bash
docker image inspect donor-registry:latest --format "{{.Size}}"
```

**Час старту** (3 повтори, у звіт іде медіана):

```bash
python scripts/startup_time.py
```

> Цей скрипт робить `down -v` перед кожним повтором, тобто **стирає базу**. Так і задумано:
> час міряється на порожніх томах.

**Докази виконання вимог** (uid процесу, відсутність curl і wget, ліміти, пароль, порти):

```bash
python scripts/collect_evidence.py
```

Пише `results/evidence.md`. Стенд при цьому має бути піднятий.

---

## 7. Що лежить у `results/`

| Файл або тека | Що в ньому |
|---|---|
| `1x/`, `2x/` | кожен прогін серії: `.json` зі зведенням, `.csv.gz` з кожним запитом |
| `closed/` | прогони закритого контуру |
| `selfcheck/` | перевірка генератора проти порожнього ендпоінта |
| `stats/` | `docker stats` під час прогонів |
| `host_1x_rate1000.csv`, `host_2x_rate1000.csv` | CPU і пам'ять хоста під час прогонів |
| `table.md` | підсумкова таблиця вимірювань (з `aggregate.py`) |
| `aggregate.json` | ті самі медіани у вигляді даних |
| `calculations.md` | три розрахунки (з `calc.py`) |
| `distribution.json` | розподіл запитів між інстансами |
| `verify_log.md` | журнал перевірки |
| `evidence.md` | докази виконання вимог |
| `startup_time.json` | час до першої відповіді |

У файлах `.csv.gz` колонки такі: запланований момент відправки, затримка, код статусу, інстанс.
Статус `0` означає помилку з'єднання, `-1` означає запит, який не завершився до кінця вікна дочікування.

---

## 8. Перезбірка звіту

Якщо перезняла вимірювання, спершу онови зведені файли:

```bash
python loadgen/aggregate.py
```

```bash
python loadgen/calc.py
```

```bash
python scripts/collect_evidence.py
```

Далі числа у звіті треба оновити вручну: таблиці в розділах 5, 7 і 8 беруться з `results/table.md`
і `results/calculations.md`. Текстові висновки написані під конкретні числа, тому самі вони не
перепишуться.

---

## 9. Git

```bash
git status
```

```bash
git add -A
```

```bash
git commit -m "опис змін"
```

```bash
git push
```

Репозиторій: https://github.com/AliSav21/hpc-lab1

Перед комітом переконайся, що `.env` не потрапив у git:

```bash
git check-ignore -v .env
```

Команда має показати правило з `.gitignore`. Якщо вона нічого не виводить, файл не ігнорується і
коміт робити не можна.

---

## 10. Якщо щось не працює

**`docker: error during connect` або `cannot find the file specified`**
Docker Desktop не запущений. Відкрий його і дочекайся статусу `Engine running`.

**Запити повертають 404 на всі id, хоча записи є**
Стенд слухає не той порт, або порт перехопила стороння програма:

```bash
curl http://127.0.0.1:18080/healthz
```

```bash
netstat -ano | findstr :18080
```

**`стенд не став готовим`**
Балансувальник ще піднімається або порт зайнятий. Подивись `docker compose ps` і повтори команду.

**Половина запитів у таймаут після зміни кількості реплік**
nginx тримає старі адреси:

```bash
docker compose restart lb
```

**`UnicodeEncodeError` у консолі**
Консоль Windows за замовчуванням не UTF-8. Запускай так:

```bash
PYTHONUTF8=1 python loadgen/aggregate.py
```

**Результати гірші, ніж раніше**
Перевір, що закриті браузер і важкі програми і що не йде інший прогін. У режимі перевантаження
розкид між повторами великий, це нормально і описано в розділі 0 звіту.

**Червоні підкреслення у PyCharm на `fastapi` і `psycopg`**
Залежності не стоять у `.venv`. Виконай `pip install -r requirements.txt`, потім
File -> Invalidate Caches -> Just Restart.

---

## 11. Чого краще не робити

- `docker compose down -v` і `python scripts/startup_time.py` **стирають базу**. Записи доведеться
  засівати наново через `python loadgen/seed.py`, але на результати вимірювань це не впливає.
- Не запускай дві серії одночасно: вони заважатимуть одна одній і числа будуть неправдиві.
- Не повертай `LB_PORT=8080` на цій машині.
- Не комить `.env`: там пароль до бази.
