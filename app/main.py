import os
import re
import socket
import time
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Literal

from fastapi import FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from psycopg import errors
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

HOSTNAME = socket.gethostname()
POD_NAME = os.environ.get("POD_NAME") or HOSTNAME
CONNINFO = (
    f"host={os.environ.get('DB_HOST', 'db')} port={os.environ.get('DB_PORT', '5432')} "
    f"dbname={os.environ['POSTGRES_DB']} user={os.environ['POSTGRES_USER']} "
    f"password={os.environ['POSTGRES_PASSWORD']} connect_timeout=3"
)
# Варіант 19: розмір пулу 10. Понад пул зʼєднань немає: max_size це жорстка межа psycopg_pool.
DB_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "10"))
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MIN_AGE, MAX_AGE = 18, 55
COLS = "id, donor_code, birth_year, blood_group, hla_typing, registered_on, available"
# Керівні символи (зокрема нульовий байт) PostgreSQL у TEXT не приймає: без перевірки запит дійшов би
# до бази і впав помилкою кодування, тобто 500 замість 400.
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

pool = AsyncConnectionPool(
    CONNINFO, min_size=DB_POOL_SIZE, max_size=DB_POOL_SIZE, open=False,
    kwargs={"row_factory": dict_row},
)

# Набір бакетів Г1 варіанта 19: 5, 10, 25, 50, 100, 250, 500, 1000 мс — у секундах.
BUCKETS_G1 = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0)

REQUESTS = Counter(
    "http_requests_total", "Кількість запитів", ["method", "path", "code"]
)
DURATION = Histogram(
    "http_request_duration_seconds", "Тривалість обробки запиту, с",
    ["method", "path"], buckets=BUCKETS_G1,
)
IN_FLIGHT = Gauge("http_requests_in_flight", "Кількість запитів в обробці")

# Додаткова метрика М1 варіанта 19.
POOL_BUSY = Gauge("db_pool_connections_busy", "Зайняті зʼєднання пулу")
POOL_SIZE = Gauge("db_pool_size", "Розмір пулу зʼєднань")
POOL_WAIT = Histogram(
    "db_pool_wait_seconds", "Час очікування зʼєднання з пулу, с", buckets=BUCKETS_G1
)
POOL_SIZE.set(DB_POOL_SIZE)


@asynccontextmanager
async def acquire(timeout: float | None = None):
    """Зʼєднання з пулу з обліком часу очікування і зайнятості пулу (метрика М1)."""
    t0 = time.perf_counter()
    async with pool.connection(timeout=timeout) as conn:
        POOL_WAIT.observe(time.perf_counter() - t0)
        POOL_BUSY.inc()
        try:
            yield conn
        finally:
            POOL_BUSY.dec()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await pool.open()
    try:
        await pool.wait(timeout=60)  # поки база стартує, /healthz віддає 503
    except PoolTimeout:
        pass
    yield
    await pool.close()


app = FastAPI(
    lifespan=lifespan,
    title="Реєстр донорів",
    version="1.0.0",
    description=(
        "CRUD-сервіс лабораторної роботи 1, варіант 20.\n\n"
        "Валідація: рік народження має давати вік від 18 до 55 років. "
        "Фільтр у переліку — за доступністю. "
        "`limit` за замовчуванням 20, максимум 100."
    ),
)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/docs")


class DonorIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    donor_code: str = Field(min_length=1, max_length=64)
    birth_year: StrictInt
    blood_group: Literal["O+", "O-", "A+", "A-", "B+", "B-", "AB+", "AB-"]
    hla_typing: list[Annotated[str, Field(min_length=1, max_length=32)]] = Field(
        min_length=1, max_length=32
    )
    registered_on: date
    available: StrictBool

    @field_validator("donor_code")
    @classmethod
    def donor_code_printable(cls, v: str) -> str:
        if CONTROL_CHARS.search(v):
            raise ValueError("donor_code must not contain control characters")
        return v

    @field_validator("hla_typing")
    @classmethod
    def hla_printable(cls, v: list[str]) -> list[str]:
        for item in v:
            if CONTROL_CHARS.search(item):
                raise ValueError("hla_typing items must not contain control characters")
        return v

    @field_validator("birth_year")
    @classmethod
    def age_in_range(cls, v: int) -> int:
        age = date.today().year - v
        if not MIN_AGE <= age <= MAX_AGE:
            raise ValueError(
                f"birth_year {v} gives age {age}; age must be between {MIN_AGE} and {MAX_AGE}"
            )
        return v


def error(status: int, field: str, message: str) -> JSONResponse:
    return JSONResponse({"errors": [{"field": field, "message": message}]}, status_code=status)


@app.middleware("http")
async def observe_request(request: Request, call_next):
    """Метрики запиту і заголовки з іменем екземпляра."""
    IN_FLIGHT.inc()
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
        code = str(response.status_code)
    except Exception:
        code = "500"
        raise
    finally:
        elapsed = time.perf_counter() - t0
        IN_FLIGHT.dec()
        # шаблон шляху, а не конкретний ідентифікатор: /donor-registry/{id}
        route = request.scope.get("route")
        path = getattr(route, "path", None) or "<unmatched>"
        DURATION.labels(request.method, path).observe(elapsed)
        REQUESTS.labels(request.method, path, code).inc()
    response.headers["X-Instance"] = HOSTNAME
    response.headers["X-Pod"] = POD_NAME
    return response


@app.get("/metrics", include_in_schema=False)
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/livez", summary="Живість процесу: 200 без звернення до бази")
async def livez():
    return PlainTextResponse("ok")


@app.exception_handler(RequestValidationError)
async def on_validation_error(_: Request, exc: RequestValidationError):
    errors_out = []
    for e in exc.errors():
        loc = [str(p) for p in e["loc"] if p not in ("body", "query", "path")]
        errors_out.append(
            {"field": ".".join(loc) or "body", "message": e["msg"].removeprefix("Value error, ")}
        )
    return JSONResponse({"errors": errors_out}, status_code=400)


@app.get("/healthz", summary="Готовність сервісу: 200, або 503 поки база недоступна")
async def healthz():
    try:
        async with acquire(timeout=2) as conn:
            await conn.execute("SELECT 1")
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ok"}


@app.post("/donor-registry", status_code=201, summary="Створити запис")
async def create(body: DonorIn):
    try:
        async with acquire() as conn:
            cur = await conn.execute(
                f"INSERT INTO donor_registry (donor_code, birth_year, blood_group, hla_typing, "
                f"registered_on, available) VALUES (%s, %s, %s, %s, %s, %s) RETURNING {COLS}",
                [body.donor_code, body.birth_year, body.blood_group, body.hla_typing,
                 body.registered_on, body.available],
            )
            return await cur.fetchone()
    except errors.UniqueViolation:
        return error(409, "donor_code", "donor_code already exists")


@app.get("/donor-registry", summary="Перелік із пагінацією і фільтром за доступністю")
async def list_donors(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0, le=2**63 - 1),   # як для id: понад це межа BIGINT
    available: bool | None = None,
):
    where, params = ("WHERE available = %s", [available]) if available is not None else ("", [])
    async with acquire() as conn:
        cur = await conn.execute(
            f"SELECT {COLS} FROM donor_registry {where} ORDER BY id LIMIT %s OFFSET %s",
            [*params, limit, offset],
        )
        items = await cur.fetchall()
        cur = await conn.execute(f"SELECT count(*) AS n FROM donor_registry {where}", params)
        total = (await cur.fetchone())["n"]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


ID = Path(ge=1, le=2**63 - 1)


@app.get("/donor-registry/{id}", summary="Один запис за ідентифікатором")
async def get_one(id: int = ID):
    async with acquire() as conn:
        cur = await conn.execute(f"SELECT {COLS} FROM donor_registry WHERE id = %s", [id])
        row = await cur.fetchone()
    return row if row else error(404, "id", "donor not found")


@app.put("/donor-registry/{id}", summary="Повна заміна запису")
async def replace(body: DonorIn, id: int = ID):
    try:
        async with acquire() as conn:
            cur = await conn.execute(
                f"UPDATE donor_registry SET donor_code = %s, birth_year = %s, blood_group = %s, "
                f"hla_typing = %s, registered_on = %s, available = %s WHERE id = %s "
                f"RETURNING {COLS}",
                [body.donor_code, body.birth_year, body.blood_group, body.hla_typing,
                 body.registered_on, body.available, id],
            )
            row = await cur.fetchone()
    except errors.UniqueViolation:
        return error(409, "donor_code", "donor_code already exists")
    return row if row else error(404, "id", "donor not found")


@app.delete("/donor-registry/{id}", status_code=204, summary="Видалити запис")
async def delete(id: int = ID):
    async with acquire() as conn:
        cur = await conn.execute("DELETE FROM donor_registry WHERE id = %s RETURNING id", [id])
        row = await cur.fetchone()
    if row is None:
        return error(404, "id", "donor not found")
