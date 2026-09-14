import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
AUTH_DB_PATH = Path(os.getenv("AUTH_DB_PATH", str(DATA_DIR / "veris_qa_ai.db")))
SESSION_KEY_PATH = DATA_DIR / ".session_secret.key"
SESSION_COOKIE = "veris_qa_session"
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "43200"))
PBKDF2_ITERATIONS = int(os.getenv("PBKDF2_ITERATIONS", "390000"))


def _connect() -> sqlite3.Connection:
    AUTH_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(AUTH_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_auth() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                role TEXT NOT NULL DEFAULT 'admin',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def has_users() -> bool:
    init_auth()
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return bool(row["n"])


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)


def create_admin(username: str, password: str) -> dict:
    init_auth()
    username = (username or "").strip()
    if has_users():
        raise ValueError("El administrador inicial ya fue creado.")
    if len(username) < 3:
        raise ValueError("El usuario debe tener al menos 3 caracteres.")
    if len(password or "") < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres.")
    salt = secrets.token_bytes(16)
    pw_hash = _hash_password(password, salt)
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO users(username, salt, password_hash, role) VALUES (?, ?, ?, 'admin')",
            (username, salt, pw_hash),
        )
    return {"id": int(cur.lastrowid), "username": username, "role": "admin"}


def authenticate(username: str, password: str) -> dict | None:
    init_auth()
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, username, salt, password_hash, role FROM users WHERE username = ? COLLATE NOCASE",
            ((username or "").strip(),),
        ).fetchone()
    if not row:
        return None
    candidate = _hash_password(password or "", row["salt"])
    if not hmac.compare_digest(candidate, row["password_hash"]):
        return None
    return {"id": int(row["id"]), "username": row["username"], "role": row["role"]}


def _session_key() -> bytes:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SESSION_KEY_PATH.exists():
        SESSION_KEY_PATH.write_bytes(secrets.token_bytes(32))
        try:
            os.chmod(SESSION_KEY_PATH, 0o600)
        except OSError:
            pass
    return SESSION_KEY_PATH.read_bytes()


def create_session(user: dict) -> str:
    exp = int(time.time()) + SESSION_TTL_SECONDS
    payload = f"{user['id']}|{user['username']}|{user['role']}|{exp}".encode("utf-8")
    sig = hmac.new(_session_key(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + b"." + sig).decode("ascii")


def parse_session(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        payload, sig = raw.rsplit(b".", 1)
        expected = hmac.new(_session_key(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        user_id, username, role, exp = payload.decode("utf-8").split("|", 3)
        if int(exp) < int(time.time()):
            return None
        return {"id": int(user_id), "username": username, "role": role, "exp": int(exp)}
    except Exception:
        return None
