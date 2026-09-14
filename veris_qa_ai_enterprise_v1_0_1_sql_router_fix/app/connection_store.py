import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet

from .models import DbConfig

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONNECTION_DB_PATH = Path(os.getenv("CONNECTION_DB_PATH", str(DATA_DIR / "connections.sqlite3")))
KEY_PATH = Path(os.getenv("CONNECTION_KEY_PATH", str(DATA_DIR / ".connection_secret.key")))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    CONNECTION_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CONNECTION_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _cipher() -> Fernet:
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not KEY_PATH.exists():
        KEY_PATH.write_bytes(Fernet.generate_key())
        try:
            os.chmod(KEY_PATH, 0o600)
        except OSError:
            pass
    return Fernet(KEY_PATH.read_bytes())


def init_connection_store() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS saved_connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                database_name TEXT NOT NULL,
                username TEXT NOT NULL,
                password_enc BLOB NOT NULL,
                sslmode TEXT NOT NULL,
                schema_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_saved_connection_name ON saved_connections(name COLLATE NOCASE)"
        )


def save_connection(name: str, cfg: DbConfig) -> int:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Ponle un nombre a la conexión.")
    init_connection_store()
    enc = _cipher().encrypt(cfg.password.encode("utf-8"))
    now = _now()
    with _connect() as conn:
        existing = conn.execute(
            "SELECT id FROM saved_connections WHERE name = ? COLLATE NOCASE",
            (clean_name,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE saved_connections
                SET host=?, port=?, database_name=?, username=?, password_enc=?, sslmode=?, schema_name=?, updated_at=?
                WHERE id=?
                """,
                (
                    cfg.host.strip(), cfg.port, cfg.database.strip(), cfg.user.strip(), enc,
                    cfg.sslmode, cfg.schema_name or "public", now, existing["id"],
                ),
            )
            return int(existing["id"])

        cur = conn.execute(
            """
            INSERT INTO saved_connections(
                name, host, port, database_name, username, password_enc,
                sslmode, schema_name, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_name, cfg.host.strip(), cfg.port, cfg.database.strip(), cfg.user.strip(), enc,
                cfg.sslmode, cfg.schema_name or "public", now, now,
            ),
        )
        return int(cur.lastrowid)


def list_connections() -> list[dict]:
    init_connection_store()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, name, host, port, database_name, username, sslmode, schema_name, updated_at
            FROM saved_connections
            ORDER BY name COLLATE NOCASE
            """
        ).fetchall()
    return [dict(r) for r in rows]


def load_connection(connection_id: int) -> dict | None:
    init_connection_store()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM saved_connections WHERE id=?",
            (connection_id,),
        ).fetchone()
    if not row:
        return None
    try:
        password = _cipher().decrypt(row["password_enc"]).decode("utf-8")
    except Exception as e:
        raise RuntimeError("No se pudo descifrar la contraseña guardada. Conserva data/.connection_secret.key.") from e
    return {
        "id": row["id"],
        "name": row["name"],
        "host": row["host"],
        "port": row["port"],
        "database": row["database_name"],
        "user": row["username"],
        "password": password,
        "sslmode": row["sslmode"],
        "schema_name": row["schema_name"],
    }


def delete_connection(connection_id: int) -> bool:
    init_connection_store()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM saved_connections WHERE id=?", (connection_id,))
        return cur.rowcount > 0
