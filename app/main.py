import os
import socket
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Literal

from fastapi import FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from psycopg import errors
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

HOSTNAME = socket.gethostname()
CONNINFO = (
    f"host={os.environ.get('DB_HOST', 'db')} port={os.environ.get('DB_PORT', '5432')} "
    f"dbname={os.environ['POSTGRES_DB']} user={os.environ['POSTGRES_USER']} "
    f"password={os.environ['POSTGRES_PASSWORD']} connect_timeout=3"
)
POOL_MAX = int(os.environ.get("POOL_MAX", "10"))
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MIN_AGE, MAX_AGE = 18, 55
COLS = "id, donor_code, birth_year, blood_group, hla_typing, registered_on, available"

pool = AsyncConnectionPool(
    CONNINFO, min_size=2, max_size=POOL_MAX, open=False, kwargs={"row_factory": dict_row}
)


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
async def add_instance_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Instance"] = HOSTNAME
    return response


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
        async with pool.connection(timeout=2) as conn:
            await conn.execute("SELECT 1")
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ok"}


@app.post("/donor-registry", status_code=201, summary="Створити запис")
async def create(body: DonorIn):
    try:
        async with pool.connection() as conn:
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
    offset: int = Query(0, ge=0),
    available: bool | None = None,
):
    where, params = ("WHERE available = %s", [available]) if available is not None else ("", [])
    async with pool.connection() as conn:
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
    async with pool.connection() as conn:
        cur = await conn.execute(f"SELECT {COLS} FROM donor_registry WHERE id = %s", [id])
        row = await cur.fetchone()
    return row if row else error(404, "id", "donor not found")


@app.put("/donor-registry/{id}", summary="Повна заміна запису")
async def replace(body: DonorIn, id: int = ID):
    try:
        async with pool.connection() as conn:
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
    async with pool.connection() as conn:
        cur = await conn.execute("DELETE FROM donor_registry WHERE id = %s RETURNING id", [id])
        row = await cur.fetchone()
    if row is None:
        return error(404, "id", "donor not found")
