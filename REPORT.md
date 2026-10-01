# Звіт: лабораторна робота 1. Проєктування і розгортання CRUD-сервісу

**Варіант 20.** Сутність Запис реєстру донорів, ресурс `/donor-registry`.

| Параметр варіанта | Значення |
|---|---|
| Фільтр у переліку | за доступністю (`available`) |
| Правило валідації | рік народження дає вік від 18 до 55 років |
| Базовий образ фінальної стадії | `python:3.12-slim` |
| Ліміти контейнера сервісу | 1 CPU, 512 МБ |
| Додаткова вимога | healthcheck сервісу працює без `curl` і `wget` в образі |

Стек: Python 3.12, FastAPI, psycopg 3 (пул з'єднань), PostgreSQL 16, nginx 1.27, Docker Compose.
Репозиторій: код у `app/`, схема в `db/schema.sql`, генератор у `loadgen/`, сирі результати в `results/`.
Інструкція запуску: [README.md](README.md). Одна команда: `docker compose up -d --build` (після `cp .env.example .env`).

## Умови вимірювання

- Усе (генератор, nginx, Postgres, вебінстанси і VM Docker Desktop) працює **на одній машині**: Windows 11, 8 логічних ядер,
  15,8 ГБ ОЗП, Docker Desktop (WSL2, 8 CPU, 8 ГБ). На верхніх рівнях вони **конкурують за ядра**.
- Генератор запускався з 4 процесами (половина логічних ядер) по 64 потоки.
- «Досягнута інтенсивність» це кількість отриманих відповідей за секунду всередині 30-секундного вікна вимірювання.
- Затримка міряється від **запланованого** моменту відправки, тож черга, що накопичилась, видна в перцентилях.
- Після кінця прогону генератор чекає ще 15 с; запити, що не завершились, записуються як невдалі (статус `-1`),
  їхня затримка це час до дедлайну. Тому колонка «не-2xx» на рівнях 800 і 1000 це **незавершені запити, а не відповіді 5xx**
  (відповідей з кодом 5xx не було), а p95/p99 там слід читати як нижню межу.
- У режимі перевантаження результат залежить від фонового навантаження хоста. Перший повний прогін (з відкритим браузером)
  дав 563 rps (1 інстанс) і 789 rps (2 інстанси) на рівні 1000; повторний, на тихішій машині, дав 540 і 591.
  Повторення 2×1000 також різняться між собою (664, 591, 432). Тому далі наводяться всі три значення, а не лише медіана.

## 1. Етап 1. Проєктування: схема таблиці

Таблиця `donor_registry` (файл `db/schema.sql`, накочується автоматично при першому старті бази):

| Поле | Тип | Обов'язкове | Обмеження |
|---|---|---|---|
| `id` | `BIGINT GENERATED ALWAYS AS IDENTITY` | так | PRIMARY KEY, генерує база |
| `donor_code` | `TEXT` | так | UNIQUE |
| `birth_year` | `SMALLINT` | так | CHECK 1900..2100 |
| `blood_group` | `TEXT` | так | CHECK у (O+, O-, A+, A-, B+, B-, AB+, AB-) |
| `hla_typing` | `TEXT[]` | так | CHECK кількість елементів ≥ 1 |
| `registered_on` | `DATE` | так | |
| `available` | `BOOLEAN` | так | |

**Індекси.** PRIMARY KEY на `id`; `UNIQUE (donor_code)`; складений `idx_donor_registry_available_id (available, id)`.
Останній обслуговує запит переліку `WHERE available = $1 ORDER BY id LIMIT … OFFSET …`: фільтр і стабільний порядок
для пагінації виконуються по одному індексу.

**Чому такі типи.**
- `id` як `BIGINT IDENTITY`: клієнт його не передає, генерує база; `GENERATED ALWAYS` забороняє явне вставлення.
- `birth_year` як `SMALLINT`: потрібен лише рік, вік рахується від нього; `DATE` додав би небажаний день і місяць.
- `blood_group` як `TEXT` + `CHECK`: закритий перелік; `ENUM` складніше змінювати міграцією.
- `hla_typing` як `TEXT[]`: набір типування це впорядкований список маркерів, який читається і замінюється цілком;
  окрема таблиця для лабораторної зайва.
- `registered_on` як `DATE`: час внесення не потрібен.
- `available` як `BOOLEAN`: два стани.
- Правило віку перевіряє сервіс (400 з поясненням); `CHECK` на роках 1900..2100 і на перелік групи крові це другий рубіж,
  щоб сміття не потрапило в таблицю повз сервіс.

## Проєктування API

Формат помилки валідації: `{"errors":[{"field":"birth_year","message":"…"}]}`.
Формат переліку: `{"items":[…],"total":N,"limit":L,"offset":O}`.
`limit` за замовчуванням **20**, верхня межа **100**; значення понад межу дає **400** (не мовчазне обрізання), `offset ≥ 0`.
Ідентифікатор не передається в тілі: зайві поля (`id` теж) відхиляються з 400.

| Ендпоінт | Тіло запиту | Тіло відповіді | Успіх | Помилки |
|---|---|---|---|---|
| `POST /donor-registry` | `donor_code`, `birth_year`, `blood_group`, `hla_typing`, `registered_on`, `available` | створений запис з `id` | 201 | 400 невалідні дані (поле і причина); 409 дубль `donor_code` |
| `GET /donor-registry?limit&offset&available` | немає | `items`, `total`, `limit`, `offset` | 200 | 400 некоректні параметри |
| `GET /donor-registry/{id}` | немає | запис | 200 | 404 не знайдено; 400 `id` не число |
| `PUT /donor-registry/{id}` | усі 6 полів (повна заміна) | замінений запис | 200 | 400 невалідні дані; 404; 409 дубль `donor_code` |
| `DELETE /donor-registry/{id}` | немає | немає тіла | 204 | 404 (також при повторному видаленні) |
| `GET /healthz` | немає | `{"status":"ok"}` | 200 | 503 база недоступна |

Валідація віку: `поточний рік − birth_year` має бути від 18 до 55 включно, інакше 400 з полем `birth_year` і поясненням.
`/healthz` виконує `SELECT 1` у базі із таймаутом 2 с, тому перевіряє саме доступність бази.
Сервіс не тримає стану в пам'яті процесу (пул з'єднань стану даних не містить), усе зберігається в базі.

## Етапи 2–4. Реалізація, образ, стенд

- **Сервіс** (`app/main.py`): FastAPI, `psycopg_pool.AsyncConnectionPool`. Помилки валідації pydantic перетворюються на 400
  (за замовчуванням FastAPI віддає 422). Під час старту пул чекає на базу до 60 с; `/healthz` у цей час віддає 503, тому
  запит, що прийшов у перші секунди після `up`, отримує відповідь.
- **Образ**: багатоетапна збірка, залежності ставляться у venv у builder-стадії; у фінальній стадії лише venv і код, без
  компіляторів, `pip`-кешу, `curl` і `wget`. Процес працює від користувача з uid 10001.
- **Стенд**: три сервіси `db`, `web`, `lb`. Порт на хост публікує лише `lb`. Кількість вебінстансів задається змінною
  `WEB_REPLICAS` (`deploy.replicas`), без правок файлів. Схема накочується монтуванням `db/schema.sql` у
  `/docker-entrypoint-initdb.d`. `web` залежить від `db` за станом `service_healthy`, `lb` від `web`.
  Пароль бази лише в `.env` (у репозиторії `.env.example`, `.env` у `.gitignore`).

### Dockerfile

```dockerfile
# syntax=docker/dockerfile:1

# ---- builder: тут ставляться залежності, у фінал іде лише готовий venv ----
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY requirements.txt .
RUN /opt/venv/bin/pip install -r requirements.txt

# ---- final: без компіляторів, pip-кешу і curl/wget ----
FROM python:3.12-slim
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /srv
COPY app ./app
USER 10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
```

### .dockerignore

```
.git
.gitignore
.idea
.venv
**/__pycache__
*.pyc
.env
.env.*
results
loadgen
scripts
lb
db
docker-compose.yml
Dockerfile
*.md
```

### docker-compose.yml

```yaml
name: donors

services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER:?set POSTGRES_USER in .env}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
      POSTGRES_DB: ${POSTGRES_DB:?set POSTGRES_DB in .env}
    volumes:
      - pgdata:/var/lib/postgresql/data
      # виконується лише на порожньому томі, тобто при першому старті
      - ./db/schema.sql:/docker-entrypoint-initdb.d/01-schema.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 2s
      timeout: 3s
      retries: 30
    networks: [backend]

  web:
    build: .
    image: donor-registry:latest
    environment:
      DB_HOST: db
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    expose:
      - "8000"
    depends_on:
      db:
        condition: service_healthy
    healthcheck:
      # без curl і wget: образ їх не містить, перевірка виконується інтерпретатором
      test:
        - CMD
        - python
        - -c
        - "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
      interval: 2s
      timeout: 3s
      retries: 15
      start_period: 3s
    deploy:
      replicas: ${WEB_REPLICAS:-1}
      resources:
        limits:
          cpus: "1"
          memory: 512M
    networks: [backend]

  lb:
    image: nginx:1.27-alpine
    ports:
      - "${LB_PORT:-8080}:80"
    volumes:
      - ./lb/nginx.conf:/etc/nginx/nginx.conf:ro
    depends_on:
      web:
        condition: service_healthy
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "3"
    networks: [backend]

networks:
  backend:

volumes:
  pgdata:
```

### lb/nginx.conf

```nginx
worker_processes auto;

events {
    worker_connections 4096;
}

http {
    # $upstream_addr показує, на який інстанс пішов запит
    log_format lb '$time_iso8601 "$request" $status upstream=$upstream_addr '
                  'urt=$upstream_response_time rt=$request_time';
    access_log /dev/stdout lb;

    # web резолвиться під час старту у всі IP реплік (DNS Compose), далі round-robin.
    # Після зміни кількості реплік lb треба перезапустити (див. README).
    upstream web {
        server web:8000;
        keepalive 64;
    }

    server {
        listen 80;

        # ендпоінт, який нічого не робить: для перевірки самого генератора навантаження
        location = /_null {
            access_log off;
            default_type text/plain;
            return 200 "ok\n";
        }

        location / {
            proxy_pass http://web;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host $host;
        }
    }
}
```

### .env.example

```
# Скопіюй у .env і задай свій пароль: cp .env.example .env
POSTGRES_USER=donors
POSTGRES_PASSWORD=change-me
POSTGRES_DB=donors

# Кількість вебінстансів і порт балансувальника на хості
WEB_REPLICAS=1
LB_PORT=8080
```

## Розмір образу і час до першої відповіді `/healthz`

- **Розмір образу.** `docker image inspect donor-registry:latest --format '{{.Size}}'` повертає **65 604 397 байт (≈ 65,6 МБ)**.
  Docker Desktop з containerd-сховищем повертає тут розмір стисненого вмісту. Сума розмірів шарів у `docker image history`
  (≈ 87,6 + 4,95 + 41,4 + 79,8 МБ) дає **≈ 214 МБ** у розпакованому вигляді. Обидва значення наведено свідомо, щоб було
  видно, яка саме метрика використана.
- **Час від `docker compose up` до першого 200 на `/healthz`** (образи зібрані, томи порожні, `down -v` перед кожним
  повтором, `scripts/startup_time.py`): **12,07 с; 9,73 с; 12,45 с. Медіана 12,07 с.**
  Час складається з ініціалізації PostgreSQL на порожньому томі, проходження healthcheck-ів бази і сервісу та старту nginx (розбивку за складовими не знімали).

## Етап 5. Вимірювання під навантаженням

### Генератор
`loadgen/gen.py`. Відкритий контур: задана інтенсивність ділиться між процесами; кожен процес наперед обчислює
моменти відправки `t = k/rate + i·procs/rate` від початку прогону і віддає запит пулу потоків, тож цикл планування
не чекає на відповідь. Закритий контур: фіксована кількість потоків, кожен шле наступний запит після відповіді.

### Сценарій і команди
Вимірюється `GET /donor-registry/{id}`. Перед серією `loadgen/seed.py` заносить 100 записів, ідентифікатори беруться з них.
Рівні 10, 100 і 500 rps (додатково 800 і 1000, щоб побачити межу), прогрів 10 с + вимірювання 30 с, 3 повтори, у таблицю
медіана. Команди (з кореня репозиторію):

```bash
python loadgen/run_series.py selfcheck               # перевірка генератора проти /_null
python loadgen/run_series.py series --instances 1    # 10, 100, 500 rps x 3 повтори
python loadgen/run_series.py series --instances 2
python loadgen/run_series.py series --instances 1 --rates 800 1000
python loadgen/run_series.py series --instances 2 --rates 800 1000 --stats
python loadgen/run_series.py distribution            # розподіл між двома інстансами
python loadgen/run_series.py closed --rate 500       # закритий контур
python loadgen/aggregate.py && python loadgen/calc.py
```

### Перевірка самого генератора
Проти ендпоінта, який нічого не робить (`/_null` в nginx, без звернення до сервісу):

| задано, rps | досягнуто, rps | p95, мс | не-2xx |
|---|---|---|---|
| 500 | 499,7 | 17,3 | 0 |
| 1000 | 1000,1 | 19,4 | 0 |

Генератор тримає задану інтенсивність до 1000 rps, тож межі в таблиці нижче задає сервіс, а не генератор.
(Але зауважте, що при одночасній роботі зі стендом генератор конкурує з ним за ядра, див. розділ 0.)

### Таблиця вимірювань (медіани з 3 повторів)

| інстансів | задано, rps | досягнуто, rps | p50, мс | p95, мс | p99, мс | не-2xx, % |
|---|---|---|---|---|---|---|
| 1 | 10 | 10.0 | 3.8 | 6.4 | 8.0 | 0.00 |
| 1 | 100 | 100.0 | 3.4 | 6.7 | 14.2 | 0.00 |
| 1 | 500 | 501.7 | 59.8 | 948.4 | 1114.9 | 0.00 |
| 1 | 800 | 470.5 | 16771.9 | 22745.1 | 23318.6 | 28.16 |
| 1 | 1000 | 539.6 | 18977.7 | 25255.0 | 25731.0 | 36.10 |
| 2 | 10 | 10.0 | 3.8 | 6.3 | 9.7 | 0.00 |
| 2 | 100 | 100.0 | 3.5 | 7.0 | 14.3 | 0.00 |
| 2 | 500 | 499.9 | 3.7 | 637.7 | 1175.7 | 0.00 |
| 2 | 800 | 784.0 | 668.9 | 1691.7 | 2040.3 | 0.00 |
| 2 | 1000 | 590.6 | 15852.0 | 21574.4 | 22318.3 | 24.57 |

Досягнуті значення по повторах:

| інстансів | задано | досягнуто по повторах, rps | L (середнє) по повторах | L_max по повторах |
|---|---|---|---|---|
| 1 | 500 | 500 / 502 / 502 | 119 / 104 / 110 | 601 / 591 / 585 |
| 1 | 800 | 496 / 470 / 439 | 6337 / 8213 / 9816 | 11309 / 13417 / 14760 |
| 1 | 1000 | 548 / 540 / 518 | 12739 / 11246 / 11968 | 19543 / 18311 / 18980 |
| 2 | 500 | 500 / 500 / 500 | 73 / 58 / 33 | 438 / 421 / 419 |
| 2 | 800 | 640 / 785 / 784 | 4100 / 576 / 367 | 6656 / 1572 / 885 |
| 2 | 1000 | 664 / 591 / 432 | 7932 / 9273 / 16580 | 12588 / 15869 / 24171 |

## Етап 6. Розподіл запитів між двома інстансами

Подано 300 запитів через балансувальник при `WEB_REPLICAS=2` (`results/distribution.json`):

| Спосіб підтвердження | Інстанс 1 | Інстанс 2 |
|---|---|---|
| Заголовок відповіді `X-Instance` (hostname контейнера) | 153 | 147 |
| Лог nginx, поле `upstream=` (адреса `172.22.0.5:8000` і `172.22.0.3:8000`) | 154 | 147 |

Лог nginx має на один запис більше за рахунок опитування `/healthz` на старті. Розподіл рівномірний (round-robin).

## Висновок про межу пропускної здатності і ефект другого інстанса

**Один інстанс.**
- 10 і 100 rps: сервіс тримає ціль, p95 ≈ 6–7 мс, у системі в середньому менше одного запиту (L = 0,4).
- **500 rps: інтенсивність ще тримається** (501,7 із 500), **але затримка вже зламана**: p50 59,8 мс, p95 948 мс, p99 1115 мс
  (на 100 rps p95 було 6,7 мс); середня кількість запитів у системі виросла з 0,4 до 110, максимум до 591.
  Закритий контур підтвердив, що межа вища: при 110 одночасних запитах сервіс видав 642 rps.
- **800 rps: рівень, на якому один інстанс перестає тримати задану інтенсивність.** Досягнуто 470 із 800, p50 16,8 с,
  L (середнє) 6337–9816, 28 % запитів не завершились за вікно дочікування. На 1000 rps те саме: 540 rps, 36 % незавершених.
- Тобто пропускна здатність одного інстанса ≈ 520–640 rps. За даними `docker stats` процес `web` у цей час на 91–96 % свого
  ліміту в 1 CPU, база (16 %) і nginx (13 %) далекі від насичення, а хост завантажений у середньому на 59 %.
  Отже, обмежувала сама обчислювальна потужність одного вебінстанса (ліміт 1 CPU).

**Чому час відповіді зростає швидше за інтенсивність.** Поки завантаження пристрою нижче одиниці, запит майже не чекає (p50 ≈ 3 мс
від 10 до 100 rps). Біля межі середній час очікування у черзі зростає приблизно як 1/(μ − λ), де μ це пропускна здатність,
λ це інтенсивність; тому приріст інтенсивності з 100 до 500 (у 5 разів) збільшив p95 приблизно у 140 разів. Коли λ перевищує μ, черга
росте весь час прогону, і затримка обмежена лише тривалістю прогону й таймаутом.

**Два інстанси.**
- 500 rps: p50 впав із 59,8 до 3,7 мс, середній час відповіді з 219 до 116 мс, L (середнє) зі 110 до 58.
  Але **p99 не змінився (≈ 1,1–1,2 с)** і p95 впав лише з 948 до 638 мс.
- 800 rps: два інстанси утримали 784 rps без жодного незавершеного запиту (один інстанс 470 rps). Однак перший повтор
  дав лише 640 rps, у двох інших 785.
- 1000 rps: обидві конфігурації перевантажені; 540 проти 591 rps (повтори 664 / 591 / 432).
- На 500 rps як за один, так і за два інстанси спостерігаються періодичні зупинки: приблизно кожні 9–10 с на 3–4 с затримка
  стрибає до ~1 с, потім повертається до 3 мс. Їхня причина не встановлена; вони однакові для обох конфігурацій.

**Яка складова лишилась спільною.** Пропускна здатність не подвоїлась (максимум 784 rps проти 1080 теоретичних). Найімовірніша спільна складова це ядра хоста. При 2 інстансах на рівні 1000 rps дані `docker stats` і лічильників Windows (`results/stats/`, `results/host_*.csv`):

| | 1 інстанс | 2 інстанси |
|---|---|---|
| CPU кожного `web` | 91–96 % | 90–102 % |
| CPU бази | 16 % | 30 % |
| CPU nginx | 13 % | 30 % |
| CPU хоста, медіана під навантаженням | 59 % | **85 %** (39 із 105 замірів вище 90 %) |

Обидва `web` упираються у свій ліміт, а база й nginx ні, тож спільним обмежником є **ядра хоста**, на яких конкурують
генератор (4 процеси), два вебінстанси, nginx, Postgres і VM Docker Desktop. Можливий додатковий чинник падіння пропускної здатності в
перевантаженні (це припущення, окремо не вимірювалось): сервер витрачає CPU на запити, які клієнт уже кинув за таймаутом. Базу як вузьке місце
дані не підтверджують. Припущення: якби кожен інстанс мав окремі ядра, приріст був би ближчим до подвоєння (на одній машині це не перевірялось).

## Етап 7. Розрахунки

### Прискорення і метрика Карпа-Флатта (N = 2)

`S = X₂ / X₁` на тому самому рівні інтенсивності; `e = (1/S − 1/N) / (1 − 1/N)`, для N = 2: `e = (1/S − 0,5) / 0,5`.

| Рівень, rps | X₁ | X₂ | S | e |
|---|---|---|---|---|
| 500 | 501,7 | 499,9 | 0,997 | 1,007 |
| 800 | 470,5 | 784,0 | 1,666 | 0,200 |
| 1000 | 539,6 | 590,6 | 1,095 | 0,827 |

Підстановка для 800: `S = 784,0 / 470,5 = 1,666`; `1/S = 0,600`; `e = (0,600 − 0,5) / 0,5 = 0,200`.
Для 1000: `S = 590,6 / 539,6 = 1,095`; `1/S = 0,913`; `e = (0,913 − 0,5) / 0,5 = 0,827`.

Що це означає:
- На 500 rps обидві конфігурації досягли заданих 500 rps, тому S ≈ 1 це відношення заданих значень, а не потужностей.
  Це не вимірювання прискорення, і воно в підсумок не береться.
- Змістовні рівні це 800 і 1000. **Прискорення лежить у діапазоні S ≈ 1,1–1,7, частка нерозпаралеленої роботи e ≈ 0,2–0,8.**
  Розкид великий, бо в режимі перевантаження результат нестабільний (див. розділ 0).
- Значення e близьке до 1 означає, що друга репліка майже не додала. Якщо інстанс має власні ядра і власну базу,
  то e мало б прямувати до 0. Спільною для обох інстансів лишились **ядра хоста** (генератор, nginx, Postgres, VM Docker).
  Якби база була не одна на два інстанси, e змінилось би мало, бо база не насичена (16–30 % CPU); суттєвіше e знизилось би,
  якби кожен інстанс отримав окремі ядра.

### Закон Літтла (100 rps, 1 інстанс)

`L = λ · W`. Підстановка: λ = 100,00 rps; W = 3,87 мс = 0,00387 с; `λ·W = 100 · 0,00387 = 0,3875`;
виміряне L (середнє за вікно різниці відправлених і завершених) = 0,3875; **розбіжність 0,00 %**.

Це майже тавтологічна збіжність: L і W рахуються з одних і тих самих записів, а на низькому навантаженні немає крайових ефектів.
Змістовніша перевірка на рівні 500 rps (1 інстанс, медіани): λ = 501,7 rps; W = 219,1 мс; `λ·W = 109,9`;
виміряне L = 109,7; розбіжність ≈ 0,2 %.

### Відкритий і закритий контур

Рівень 500 rps, 1 інстанс. Кількість одночасних запитів у закритому контурі дорівнює медіані L з відкритого контуру (110).
На рівні 800 rps L у відкритому контурі це тисячі (6337–9816) і складається переважно з запитів, що чекають до кінця
вікна дочікування, тож реалістичне порівняння виконано на 500.

| Контур | Одночасних запитів | Досягнуто, rps | p95, мс |
|---|---|---|---|
| Відкритий (задано 500 rps) | L ≈ 110 | 501,7 | 948,4 |
| Закритий | 110 | 642,1 (повтори 642 / 601 / 656) | 354,4 (346 / 372 / 354) |

Чому закритий контур показує меншу затримку на тому самому сервісі. У закритому контурі клієнт не шле новий запит, доки
не отримав відповідь, тому кількість запитів у системі жорстко обмежена 110, а черга не може рости. Сервіс сам
регулює навантаження: чим повільніше відповідає, тим рідше надходять нові запити. У відкритому контурі запити надходять за
розкладом незалежно від відповідей, і в моменти уповільнення черга накопичується (L_max 591), що видно в p95. За законом
Літтла середній час відповіді закритого контуру `W = N / X = 110 / 642 ≈ 171 мс`, тобто він визначається самою кількістю
запитів у системі. Закритий контур приховує перевантаження, бо кількість запитів у польоті не росте.

## Етап 9. Журнал перевірки

Послідовність запитів і відповіді сервісу (`scripts/verify_flow.py`, повний вивід у `results/verify_log.md`).
У кінці журналу перевірки перезапусків: після `docker compose down` + `up` запис на місці (200), після `down -v` + `up`
база порожня (`total = 0`) і стенд піднімається без помилок.

### готовність
`GET /healthz`
→ **200**
```json
{"status":"ok"}
```

### створення
`POST /donor-registry`
```json
{"donor_code": "D-1790868490", "birth_year": 1996, "blood_group": "O+", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **201**
```json
{"id":1,"donor_code":"D-1790868490","birth_year":1996,"blood_group":"O+","hla_typing":["A*02:01","B*07:02"],"registered_on":"2026-01-15","available":true}
```

### 400: вік 10 років
`POST /donor-registry`
```json
{"donor_code": "D-1790868490", "birth_year": 2016, "blood_group": "O+", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **400**
```json
{"errors":[{"field":"birth_year","message":"birth_year 2016 gives age 10; age must be between 18 and 55"}]}
```

### 400: вік 70 років
`POST /donor-registry`
```json
{"donor_code": "D-1790868490", "birth_year": 1956, "blood_group": "O+", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **400**
```json
{"errors":[{"field":"birth_year","message":"birth_year 1956 gives age 70; age must be between 18 and 55"}]}
```

### 400: невалідна група крові
`POST /donor-registry`
```json
{"donor_code": "D-1790868490", "birth_year": 1996, "blood_group": "X", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **400**
```json
{"errors":[{"field":"blood_group","message":"Input should be 'O+', 'O-', 'A+', 'A-', 'B+', 'B-', 'AB+' or 'AB-'"}]}
```

### 409: дубль donor_code
`POST /donor-registry`
```json
{"donor_code": "D-1790868490", "birth_year": 1996, "blood_group": "O+", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **409**
```json
{"errors":[{"field":"donor_code","message":"donor_code already exists"}]}
```

### перелік
`GET /donor-registry?limit=5`
→ **200**
```json
{"items":[{"id":1,"donor_code":"D-1790868490","birth_year":1996,"blood_group":"O+","hla_typing":["A*02:01","B*07:02"],"registered_on":"2026-01-15","available":true}],"total":1,"limit":5,"offset":0}
```

### перелік з фільтром available=false
`GET /donor-registry?available=false&limit=5`
→ **200**
```json
{"items":[],"total":0,"limit":5,"offset":0}
```

### 400: limit понад максимум
`GET /donor-registry?limit=1000`
→ **400**
```json
{"errors":[{"field":"limit","message":"Input should be less than or equal to 100"}]}
```

### за ідентифікатором
`GET /donor-registry/1`
→ **200**
```json
{"id":1,"donor_code":"D-1790868490","birth_year":1996,"blood_group":"O+","hla_typing":["A*02:01","B*07:02"],"registered_on":"2026-01-15","available":true}
```

### повна заміна
`PUT /donor-registry/1`
```json
{"donor_code": "D-1790868490", "birth_year": 1996, "blood_group": "AB-", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": false}
```
→ **200**
```json
{"id":1,"donor_code":"D-1790868490","birth_year":1996,"blood_group":"AB-","hla_typing":["A*02:01","B*07:02"],"registered_on":"2026-01-15","available":false}
```

### 400: PUT з невалідним віком
`PUT /donor-registry/1`
```json
{"donor_code": "D-1790868490", "birth_year": 1900, "blood_group": "O+", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **400**
```json
{"errors":[{"field":"birth_year","message":"birth_year 1900 gives age 126; age must be between 18 and 55"}]}
```

### 404: PUT неіснуючого
`PUT /donor-registry/999999999`
```json
{"donor_code": "D-1790868490", "birth_year": 1996, "blood_group": "O+", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **404**
```json
{"errors":[{"field":"id","message":"donor not found"}]}
```

### видалення
`DELETE /donor-registry/1`
→ **204**
```json
(порожнє тіло)
```

### повторне видалення → 404
`DELETE /donor-registry/1`
→ **404**
```json
{"errors":[{"field":"id","message":"donor not found"}]}
```

### GET видаленого → 404
`GET /donor-registry/1`
→ **404**
```json
{"errors":[{"field":"id","message":"donor not found"}]}
```

### запис перед down
`POST /donor-registry`
```json
{"donor_code": "PERSIST-1", "birth_year": 1996, "blood_group": "O+", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **201**
```json
{"id":3,"donor_code":"PERSIST-1","birth_year":1996,"blood_group":"O+","hla_typing":["A*02:01","B*07:02"],"registered_on":"2026-01-15","available":true}
```

### після down + up запис на місці (очікуємо 200)
`GET /donor-registry/3`
→ **200**
```json
{"id":3,"donor_code":"PERSIST-1","birth_year":1996,"blood_group":"O+","hla_typing":["A*02:01","B*07:02"],"registered_on":"2026-01-15","available":true}
```

### після down -v + up база порожня (total = 0)
`GET /donor-registry`
→ **200**
```json
{"items":[],"total":0,"limit":20,"offset":0}
```

## Додаткова вимога: healthcheck без `curl` і `wget`

Healthcheck у `docker-compose.yml` виконує інтерпретатор Python: `python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"`.
Образ не містить ні `curl`, ні `wget`. Підтвердження (вивід команд, `results/evidence.md`):

### Контейнери, порти і стан здоровʼя (публікує порт лише lb)
`docker compose ps --format table {{.Name}}	{{.Status}}	{{.Ports}}`

```
NAME           STATUS                       PORTS
donors-db-1    Up About an hour (healthy)   5432/tcp
donors-lb-1    Up 7 minutes                 0.0.0.0:8080->80/tcp, [::]:8080->80/tcp
donors-web-1   Up About an hour (healthy)   8000/tcp
donors-web-2   Up 7 minutes (healthy)       8000/tcp
```

### Розмір фінального образу
`docker image inspect donor-registry:latest --format {{.Size}} байт`

```
65604397 байт
```

### Шари образу
`docker image history donor-registry:latest --format {{.Size}}	{{.CreatedBy}}`

```
0B	CMD ["uvicorn" "app.main:app" "--host" "0.0.…
0B	EXPOSE [8000/tcp]
0B	USER 10001
20.5kB	COPY app ./app # buildkit
4.1kB	WORKDIR /srv
79.8MB	COPY /opt/venv /opt/venv # buildkit
41kB	RUN /bin/sh -c useradd --system --uid 10001 …
0B	ENV PATH=/opt/venv/bin:/usr/local/bin:/usr/l…
0B	CMD ["python3"]
16.4kB	RUN /bin/sh -c set -eux;  for src in idle3 p…
41.4MB	RUN /bin/sh -c set -eux;   savedAptMark="$(a…
0B	ENV PYTHON_SHA256=5c8462af5790baf43a321a1559…
0B	ENV PYTHON_VERSION=3.12.14
0B	ENV GPG_KEY=7169605F62C751356D054A26A821E680…
4.95MB	RUN /bin/sh -c set -eux;  apt-get update;  a…
0B	ENV LANG=C.UTF-8
0B	ENV PATH=/usr/local/bin:/usr/local/sbin:/usr…
87.6MB	# debian.sh --arch 'amd64' out/ 'trixie' '@1…
```

### Процес у контейнері: uid і користувач
`docker exec donors-web-1 id`

```
uid=10001(app) gid=999(app) groups=999(app)
```

### curl, wget, gcc в образі відсутні (додаткова вимога: healthcheck без curl/wget)
`docker exec donors-web-1 sh -c for c in curl wget gcc; do command -v $c || echo "$c: немає"; done`

```
curl: немає
wget: немає
gcc: немає
```

### Healthcheck сервісу (команда і стан)
`docker inspect donors-web-1 --format {{json .Config.Healthcheck.Test}} => {{.State.Health.Status}}`

```
["CMD","python","-c","import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"] => healthy
```

### Обмеження ресурсів web
`docker inspect donors-web-1 --format NanoCpus={{.HostConfig.NanoCpus}} Memory={{.HostConfig.Memory}}`

```
NanoCpus=1000000000 Memory=536870912
```

### Пароль не в образі, не в історії збірки, не в репозиторії
```
у history образу: не знайдено
у змінних образу: не знайдено
у файлах репозиторію (git ls-files): не знайдено
```

### .env не комітиться
`git check-ignore -v .env`

```
.gitignore:1:.env	.env
```

### Порти бази і вебінстансів з хоста недоступні, балансувальник доступний
```
127.0.0.1:5432 (Postgres): підключення немає
127.0.0.1:8000 (web):      підключення немає
127.0.0.1:8080 (lb):       доступно
```

### База доступна з контейнера сервісу за іменем db
`docker exec donors-web-1 python -c import socket;s=socket.create_connection(('db',5432),2);print('db:5432 підключення є')`

```
db:5432 підключення є
```

### Репліки: скільки вебінстансів зараз
`docker compose ps web --format {{.Name}}`

```
donors-web-1
donors-web-2
```

## Відкинуті рішення

1. **Кілька процесів `uvicorn --workers N` всередині контейнера.** Ліміт контейнера 1 CPU, а масштабування за завданням має
   робитись окремими інстансами за балансувальником; кілька воркерів ховали б межу одного інстанса і заважали б
   вимірювати прискорення.
2. **`curl` або `wget` у образі для healthcheck.** Порушує додаткову вимогу варіанта і збільшує образ; замість цього перевірку
   виконує інтерпретатор Python.
3. **Схема з коду сервісу під час старту (`CREATE TABLE IF NOT EXISTS` у застосунку).** Два інстанси стартують одночасно і
   конкурують за DDL. Обрано монтування `db/schema.sql` у `/docker-entrypoint-initdb.d`: накочується рівно один раз, на порожньому томі.
4. **`ENUM` у PostgreSQL для групи крові.** Зміну переліку складніше проводити; `TEXT` з `CHECK` простіший і достатній.
5. **DNS-резолвінг nginx на кожен запит (`resolver` + змінна в `proxy_pass`).** Робить `keepalive` до upstream неможливим
   і додає накладні витрати; обрано статичний `upstream` з `keepalive 64` і перезапуск `lb` після зміни кількості реплік.
6. **Обрізання `limit` до максимуму замість 400.** Мовчазна зміна параметра приховувала б помилку клієнта; обрано явний 400.
7. **Генератор на основі готових інструментів (wrk, k6) з фіксованою кількістю користувачів.** Вони за замовчуванням
   працюють у закритому контурі й приховують затримку під перевантаженням; завдання вимагає власного відкритого генератора.

## Використання генеративних моделей

Модель Claude (Anthropic, через Claude Code) використана для: каркаса сервісу (`app/main.py`, `db/schema.sql`),
`Dockerfile`, `docker-compose.yml` і `lb/nginx.conf`, генератора навантаження та скриптів вимірювання (`loadgen/`, `scripts/`),
а також для чернетки цього звіту й інтерпретації результатів.

Що перевірено після моделі (запуском):
- повний цикл запитів із кодами 201/400/404/409/204 і повторним DELETE → 404 (розділ 9), збереження даних після
  `down` і порожня база після `down -v`;
- відсутність `curl`, `wget` і `gcc` в образі, uid процесу, ліміти CPU і пам'яті, відсутність пароля в образі, історії й
  репозиторії, недоступність портів бази і вебінстансів з хоста (розділ 10);
- що генератор тримає ціль 500 і 1000 rps проти порожнього ендпоінта (розділ 5);
- збіжність закону Літтла (розділ 8.2) і розподіл запитів між двома інстансами двома незалежними способами (розділ 6);
- чесність висновків: розділ 0 і 7 описують нестабільність результатів у режимі перевантаження і те, що причину
  періодичних зупинок не встановлено.
