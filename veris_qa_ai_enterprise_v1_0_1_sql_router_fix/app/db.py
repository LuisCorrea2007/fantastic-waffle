import hashlib
import os
import threading
import time
from urllib.parse import quote_plus

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from .models import DbConfig
from .safety import apply_preview_limit

_ENGINE_CACHE: dict[str, Engine] = {}
_ENGINE_LOCK = threading.RLock()
_SCHEMA_CACHE: dict[str, tuple[float, dict]] = {}
_SCHEMA_LOCK = threading.RLock()

SCHEMA_CACHE_TTL_SECONDS = int(os.getenv("SCHEMA_CACHE_TTL_SECONDS", "600"))
POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "5"))
POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE_SECONDS", "900"))
POOL_PRE_PING = os.getenv("DB_POOL_PRE_PING", "false").strip().lower() in {"1", "true", "yes", "on"}


def _engine_key(cfg: DbConfig) -> str:
    # Incluye hash de password para no reutilizar un pool si cambian credenciales.
    raw = "|".join([
        cfg.host.strip().lower(), str(cfg.port), cfg.database.strip().lower(),
        cfg.user.strip(), cfg.password, cfg.sslmode.strip().lower(),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _schema_key(cfg: DbConfig) -> str:
    return f"{_engine_key(cfg)}|{(cfg.schema_name or 'public').strip().lower()}"


def make_url(cfg: DbConfig) -> str:
    user = quote_plus(cfg.user)
    password = quote_plus(cfg.password)
    host = cfg.host.strip()
    db = quote_plus(cfg.database)
    ssl = quote_plus(cfg.sslmode)
    return f"postgresql+psycopg://{user}:{password}@{host}:{cfg.port}/{db}?sslmode={ssl}"


def make_engine(cfg: DbConfig) -> Engine:
    """Devuelve un Engine reutilizable con pool de conexiones persistente."""
    key = _engine_key(cfg)
    with _ENGINE_LOCK:
        engine = _ENGINE_CACHE.get(key)
        if engine is not None:
            return engine

        engine = create_engine(
            make_url(cfg),
            pool_size=POOL_SIZE,
            max_overflow=MAX_OVERFLOW,
            pool_timeout=10,
            pool_recycle=POOL_RECYCLE,
            pool_pre_ping=POOL_PRE_PING,
            connect_args={
                "connect_timeout": 6,
                "application_name": "veris_qa_ai",
            },
        )
        _ENGINE_CACHE[key] = engine
        return engine


def clear_engine_cache() -> None:
    with _ENGINE_LOCK:
        engines = list(_ENGINE_CACHE.values())
        _ENGINE_CACHE.clear()
    for engine in engines:
        try:
            engine.dispose()
        except Exception:
            pass


def test_connection(cfg: DbConfig) -> dict:
    started = time.perf_counter()
    engine = make_engine(cfg)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT current_database(), current_user, version()")
        ).one()
    return {
        "ok": True,
        "database": row[0],
        "user": row[1],
        "version": row[2],
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "pooled": True,
    }


def load_schema(cfg: DbConfig, force_refresh: bool = False) -> dict:
    """
    Carga el esquema de forma masiva y lo mantiene en caché.
    Evita hacer 3-4 consultas de metadatos por cada tabla en cada petición.
    """
    key = _schema_key(cfg)
    now = time.monotonic()

    if not force_refresh:
        with _SCHEMA_LOCK:
            cached = _SCHEMA_CACHE.get(key)
            if cached and now - cached[0] < SCHEMA_CACHE_TTL_SECONDS:
                data = dict(cached[1])
                data["cached"] = True
                data["load_ms"] = 0.0
                return data

    started = time.perf_counter()
    engine = make_engine(cfg)
    inspector = inspect(engine)
    schema = (cfg.schema_name or "public").strip()

    # SQLAlchemy 2.x puede introspectar todas las tablas de una sola vez.
    # Esto es muchísimo más rápido que get_columns/get_pk/get_fk por tabla.
    try:
        multi_columns = inspector.get_multi_columns(schema=schema)
        multi_pks = inspector.get_multi_pk_constraint(schema=schema)
        multi_fks = inspector.get_multi_foreign_keys(schema=schema)
        table_names = sorted({name for (sch, name) in multi_columns.keys() if sch == schema})

        tables = []
        for table_name in table_names:
            k = (schema, table_name)
            cols_raw = multi_columns.get(k, [])
            pk_raw = multi_pks.get(k, {}) or {}
            fks_raw = multi_fks.get(k, []) or []
            tables.append({
                "name": table_name,
                "columns": [
                    {
                        "name": c["name"],
                        "type": str(c["type"]),
                        "nullable": bool(c.get("nullable", True)),
                    }
                    for c in cols_raw
                ],
                "primary_key": pk_raw.get("constrained_columns", []) or [],
                "foreign_keys": [
                    {
                        "columns": fk.get("constrained_columns", []) or [],
                        "referred_schema": fk.get("referred_schema"),
                        "referred_table": fk.get("referred_table"),
                        "referred_columns": fk.get("referred_columns", []) or [],
                    }
                    for fk in fks_raw
                ],
            })
    except Exception:
        # Fallback para dialectos/versiones donde get_multi_* no esté disponible.
        tables = []
        for table_name in inspector.get_table_names(schema=schema):
            columns = [
                {
                    "name": c["name"],
                    "type": str(c["type"]),
                    "nullable": bool(c.get("nullable", True)),
                }
                for c in inspector.get_columns(table_name, schema=schema)
            ]
            pk = inspector.get_pk_constraint(table_name, schema=schema).get("constrained_columns", [])
            fks = [
                {
                    "columns": fk.get("constrained_columns", []),
                    "referred_schema": fk.get("referred_schema"),
                    "referred_table": fk.get("referred_table"),
                    "referred_columns": fk.get("referred_columns", []),
                }
                for fk in inspector.get_foreign_keys(table_name, schema=schema)
            ]
            tables.append({
                "name": table_name,
                "columns": columns,
                "primary_key": pk,
                "foreign_keys": fks,
            })

    data = {
        "schema": schema,
        "tables": tables,
        "cached": False,
        "load_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    with _SCHEMA_LOCK:
        _SCHEMA_CACHE[key] = (time.monotonic(), data)
    return dict(data)


def invalidate_schema_cache(cfg: DbConfig) -> None:
    with _SCHEMA_LOCK:
        _SCHEMA_CACHE.pop(_schema_key(cfg), None)


def execute_readonly(cfg: DbConfig, sql: str, max_rows: int, timeout_ms: int) -> dict:
    """
    Ejecuta en READ ONLY usando pool persistente y cursor streaming.
    Además aplica un LIMIT de vista previa si la consulta no trae uno,
    para evitar transferir/ordenar resultados enormes innecesariamente.
    """
    started = time.perf_counter()
    engine = make_engine(cfg)
    safe_max_rows = max(1, min(int(max_rows), 5000))
    executed_sql, limit_applied = apply_preview_limit(sql, safe_max_rows + 1)

    with engine.connect() as conn:
        tx = conn.begin()
        try:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            safe_timeout_ms = max(1, int(timeout_ms))
            conn.execute(
                text("SELECT set_config('statement_timeout', :timeout, true)"),
                {"timeout": f"{safe_timeout_ms}ms"},
            )

            # stream_results evita que SQLAlchemy/Psycopg materialicen todo el resultado
            # antes de que podamos mostrar las primeras filas.
            streaming_conn = conn.execution_options(stream_results=True, max_row_buffer=safe_max_rows + 1)
            result = streaming_conn.execute(text(executed_sql))
            cols = list(result.keys())
            rows = result.fetchmany(safe_max_rows + 1)
            truncated = len(rows) > safe_max_rows
            rows = rows[:safe_max_rows]
            normalized = [[_json_safe(v) for v in row] for row in rows]
            tx.rollback()

            return {
                "columns": cols,
                "rows": normalized,
                "row_count": len(normalized),
                "truncated": truncated,
                "limit_applied": limit_applied,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        except Exception:
            tx.rollback()
            raise


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
