import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = BASE_DIR / "data" / "memory.sqlite3"
MEMORY_DB_PATH = Path(os.getenv("MEMORY_DB_PATH", str(DEFAULT_DB)))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    MEMORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(MEMORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_memory() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS query_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_key TEXT NOT NULL,
                question TEXT,
                sql TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('generated','success','error')),
                verified INTEGER NOT NULL DEFAULT 0,
                row_count INTEGER,
                error TEXT,
                provider TEXT,
                model TEXT,
                schema_fingerprint TEXT,
                source TEXT NOT NULL DEFAULT 'ai',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_used_at TEXT,
                use_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_query_memory_workspace
            ON query_memory(workspace_key, verified, status, updated_at DESC);

            CREATE TABLE IF NOT EXISTS knowledge_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_key TEXT NOT NULL,
                content TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'business_rule',
                verified INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_knowledge_workspace
            ON knowledge_memory(workspace_key, updated_at DESC);

            CREATE TABLE IF NOT EXISTS schema_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                schema_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(workspace_key, fingerprint)
            );
            """
        )


def workspace_key(cfg: Any) -> str:
    # No se persisten contraseña ni credenciales. Solo un hash estable de la identidad lógica.
    raw = f"{cfg.host.strip().lower()}|{cfg.port}|{cfg.database.strip().lower()}|{(cfg.schema_name or 'public').strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _schema_payload(schema: dict) -> dict:
    # No incluye metadatos volátiles (load_ms/cached/etc.) en fingerprints o snapshots.
    return {
        "schema": schema.get("schema", "public"),
        "tables": schema.get("tables", []),
    }


def schema_fingerprint(schema: dict) -> str:
    normalized = json.dumps(_schema_payload(schema), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def save_schema(cfg: Any, schema: dict) -> str:
    init_memory()
    wk = workspace_key(cfg)
    clean_schema = _schema_payload(schema)
    fp = schema_fingerprint(clean_schema)
    payload = json.dumps(clean_schema, ensure_ascii=False)
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO schema_snapshots(workspace_key, fingerprint, schema_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (wk, fp, payload, _now()),
        )
    return fp


def record_query(
    cfg: Any,
    *,
    question: str | None,
    sql: str,
    status: str,
    provider: str | None = None,
    model: str | None = None,
    schema_fp: str | None = None,
    source: str = "ai",
    row_count: int | None = None,
    error: str | None = None,
) -> int:
    init_memory()
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO query_memory(
                workspace_key, question, sql, status, verified, row_count, error,
                provider, model, schema_fingerprint, source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_key(cfg),
                (question or "").strip() or None,
                sql.strip(),
                status,
                row_count,
                error,
                provider,
                model,
                schema_fp,
                source,
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


def update_query_result(memory_id: int, *, status: str, row_count: int | None = None, error: str | None = None) -> None:
    init_memory()
    with _connect() as conn:
        conn.execute(
            "UPDATE query_memory SET status=?, row_count=?, error=?, updated_at=? WHERE id=?",
            (status, row_count, error, _now(), memory_id),
        )


def verify_query(cfg: Any, memory_id: int, verified: bool = True) -> bool:
    init_memory()
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE query_memory
            SET verified=?, updated_at=?
            WHERE id=? AND workspace_key=?
            """,
            (1 if verified else 0, _now(), memory_id, workspace_key(cfg)),
        )
        return cur.rowcount > 0


def add_knowledge(cfg: Any, content: str, kind: str = "business_rule") -> int:
    init_memory()
    clean = content.strip()
    if not clean:
        raise ValueError("La memoria no puede estar vacía.")
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO knowledge_memory(workspace_key, content, kind, verified, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?)
            """,
            (workspace_key(cfg), clean, kind, now, now),
        )
        return int(cur.lastrowid)


def delete_knowledge(cfg: Any, memory_id: int) -> bool:
    init_memory()
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM knowledge_memory WHERE id=? AND workspace_key=?",
            (memory_id, workspace_key(cfg)),
        )
        return cur.rowcount > 0


def list_memory(cfg: Any, limit: int = 50) -> dict:
    init_memory()
    wk = workspace_key(cfg)
    with _connect() as conn:
        queries = conn.execute(
            """
            SELECT id, question, sql, status, verified, row_count, error, provider, model,
                   source, created_at, updated_at, use_count
            FROM query_memory
            WHERE workspace_key=?
            ORDER BY verified DESC, updated_at DESC
            LIMIT ?
            """,
            (wk, limit),
        ).fetchall()
        knowledge = conn.execute(
            """
            SELECT id, content, kind, verified, created_at, updated_at
            FROM knowledge_memory
            WHERE workspace_key=?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (wk, limit),
        ).fetchall()
        schema_count = conn.execute(
            "SELECT COUNT(*) FROM schema_snapshots WHERE workspace_key=?",
            (wk,),
        ).fetchone()[0]

    return {
        "queries": [dict(r) for r in queries],
        "knowledge": [dict(r) for r in knowledge],
        "schema_snapshots": schema_count,
        "memory_db": str(MEMORY_DB_PATH),
    }


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-zA-ZáéíóúñÁÉÍÓÚÑ0-9_]{3,}", (text or "").lower())
    stop = {
        "que", "con", "los", "las", "para", "por", "una", "uno", "del", "como", "dame",
        "muestre", "mostrar", "quiero", "desde", "hasta", "tabla", "datos", "base", "sql",
    }
    return {w for w in words if w not in stop}


def _score(question: str, row_question: str | None, sql: str) -> float:
    q = _tokens(question)
    if not q:
        return 0.0
    candidate = _tokens((row_question or "") + " " + sql)
    if not candidate:
        return 0.0
    overlap = len(q & candidate)
    return overlap / max(1, len(q))


def retrieve_context(cfg: Any, question: str, max_examples: int = 5, max_knowledge: int = 12) -> dict:
    """Recuperación local, determinista y agnóstica al proveedor de IA."""
    init_memory()
    wk = workspace_key(cfg)
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, question, sql, status, verified, row_count, error, provider, model, updated_at
            FROM query_memory
            WHERE workspace_key=? AND question IS NOT NULL
            ORDER BY verified DESC, updated_at DESC
            LIMIT 250
            """,
            (wk,),
        ).fetchall()
        knowledge = conn.execute(
            """
            SELECT id, content, kind, verified, updated_at
            FROM knowledge_memory
            WHERE workspace_key=? AND verified=1
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (wk, max_knowledge),
        ).fetchall()

    scored = []
    for r in rows:
        d = dict(r)
        d["similarity"] = _score(question, d.get("question"), d.get("sql", ""))
        # Consultas verificadas tienen prioridad aunque el solapamiento sea modesto.
        d["rank"] = d["similarity"] + (1.0 if d.get("verified") else 0.0) + (0.15 if d.get("status") == "success" else 0.0)
        scored.append(d)
    scored.sort(key=lambda x: x["rank"], reverse=True)

    selected = [r for r in scored if r["similarity"] > 0 or r.get("verified")][:max_examples]
    ids = [r["id"] for r in selected]
    if ids:
        placeholders = ",".join("?" for _ in ids)
        with _connect() as conn:
            conn.execute(
                f"UPDATE query_memory SET use_count=use_count+1, last_used_at=? WHERE id IN ({placeholders})",
                (_now(), *ids),
            )

    for r in selected:
        r.pop("rank", None)
    return {
        "examples": selected,
        "knowledge": [dict(r) for r in knowledge],
    }


def memory_to_prompt(context: dict) -> str:
    parts: list[str] = []
    knowledge = context.get("knowledge", [])
    if knowledge:
        parts.append("MEMORIA DE NEGOCIO CONFIRMADA:")
        for item in knowledge:
            parts.append(f"- {item['content']}")

    examples = context.get("examples", [])
    if examples:
        parts.append("\nCONSULTAS PREVIAS DEL MISMO WORKSPACE:")
        for ex in examples:
            marker = "VERIFICADA" if ex.get("verified") else ex.get("status", "historica").upper()
            parts.append(f"- [{marker}] Pregunta: {ex.get('question')}")
            parts.append(f"  SQL: {ex.get('sql')}")
            if ex.get("status") == "error" and ex.get("error"):
                parts.append(f"  ERROR PREVIO (no repetir): {ex.get('error')}")

    return "\n".join(parts).strip()


def latest_schema_snapshot(cfg: Any) -> dict | None:
    """Devuelve el último snapshot persistido sin consultar PostgreSQL."""
    init_memory()
    wk = workspace_key(cfg)
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT fingerprint, schema_json, created_at
            FROM schema_snapshots
            WHERE workspace_key=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (wk,),
        ).fetchone()
    if not row:
        return None
    try:
        schema = json.loads(row["schema_json"])
    except Exception:
        return None
    schema["fingerprint"] = row["fingerprint"]
    schema["snapshot_created_at"] = row["created_at"]
    schema["persisted_snapshot"] = True
    schema["cached"] = True
    schema["load_ms"] = 0.0
    return schema


def find_exact_verified_query(cfg: Any, question: str) -> dict | None:
    """Reutiliza solo una pregunta exactamente igual y previamente verificada por el usuario."""
    init_memory()
    wk = workspace_key(cfg)
    q = (question or "").strip()
    if not q:
        return None
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id, question, sql, schema_fingerprint, provider, model
            FROM query_memory
            WHERE workspace_key=?
              AND verified=1
              AND status='success'
              AND lower(trim(question))=lower(trim(?))
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (wk, q),
        ).fetchone()
    return dict(row) if row else None
