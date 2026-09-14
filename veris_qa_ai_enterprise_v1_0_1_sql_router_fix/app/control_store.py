import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.getenv("CONTROL_DB_PATH", str(DATA_DIR / "veris_qa_ai.db")))
MASTER_KEY_PATH = Path(os.getenv("MASTER_KEY_PATH", str(DATA_DIR / ".master.key")))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _cipher() -> Fernet:
    MASTER_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not MASTER_KEY_PATH.exists():
        MASTER_KEY_PATH.write_bytes(Fernet.generate_key())
        try:
            os.chmod(MASTER_KEY_PATH, 0o600)
        except OSError:
            pass
    return Fernet(MASTER_KEY_PATH.read_bytes())


def _enc(value: str | None) -> bytes | None:
    if value is None or value == "":
        return None
    return _cipher().encrypt(value.encode("utf-8"))


def _dec(value: bytes | None) -> str:
    if not value:
        return ""
    return _cipher().decrypt(value).decode("utf-8")


def init_control_store() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS environments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                description TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS web_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                environment_id INTEGER,
                base_url TEXT NOT NULL,
                login_url TEXT,
                username TEXT,
                password_enc BLOB,
                browser TEXT NOT NULL DEFAULT 'chromium',
                headless INTEGER NOT NULL DEFAULT 0,
                timeout_ms INTEGER NOT NULL DEFAULT 30000,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(environment_id) REFERENCES environments(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS api_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                environment_id INTEGER,
                base_url TEXT NOT NULL,
                auth_header TEXT,
                token_enc BLOB,
                verify_tls INTEGER NOT NULL DEFAULT 1,
                timeout_ms INTEGER NOT NULL DEFAULT 30000,
                default_headers_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(environment_id) REFERENCES environments(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS mobile_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                environment_id INTEGER,
                platform TEXT NOT NULL,
                appium_url TEXT NOT NULL,
                device_name TEXT,
                username TEXT,
                password_enc BLOB,
                udid TEXT,
                platform_version TEXT,
                automation_name TEXT,
                app_package TEXT,
                app_activity TEXT,
                bundle_id TEXT,
                app_path TEXT,
                timeout_ms INTEGER NOT NULL DEFAULT 30000,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(environment_id) REFERENCES environments(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS test_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_key TEXT NOT NULL UNIQUE COLLATE NOCASE,
                name TEXT NOT NULL,
                description TEXT,
                environment_id INTEGER,
                plan_json TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(environment_id) REFERENCES environments(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_case_id INTEGER,
                case_key TEXT,
                case_name TEXT,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                duration_ms REAL,
                result_json TEXT,
                report_json_path TEXT,
                report_html_path TEXT,
                FOREIGN KEY(test_case_id) REFERENCES test_cases(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS qa_experiences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id INTEGER,
                case_key TEXT,
                status TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                verified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(execution_id) REFERENCES executions(id) ON DELETE SET NULL
            );
            """
        )
        # Migraciones aditivas para instalaciones existentes.
        mobile_cols = {r[1] for r in conn.execute("PRAGMA table_info(mobile_profiles)").fetchall()}
        if "username" not in mobile_cols:
            conn.execute("ALTER TABLE mobile_profiles ADD COLUMN username TEXT")
        if "password_enc" not in mobile_cols:
            conn.execute("ALTER TABLE mobile_profiles ADD COLUMN password_enc BLOB")


def list_environments() -> list[dict]:
    init_control_store()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM environments ORDER BY name COLLATE NOCASE").fetchall()
    return [dict(r) for r in rows]


def save_environment(data: dict) -> int:
    init_control_store()
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("El ambiente necesita un nombre.")
    now = _now()
    with _connect() as conn:
        existing = conn.execute("SELECT id FROM environments WHERE name=? COLLATE NOCASE", (name,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE environments SET description=?, is_active=?, updated_at=? WHERE id=?",
                (data.get("description", ""), int(bool(data.get("is_active", True))), now, existing["id"]),
            )
            return int(existing["id"])
        cur = conn.execute(
            "INSERT INTO environments(name, description, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (name, data.get("description", ""), int(bool(data.get("is_active", True))), now, now),
        )
        return int(cur.lastrowid)


def delete_environment(item_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM environments WHERE id=?", (item_id,))
        return cur.rowcount > 0


def list_web_profiles() -> list[dict]:
    init_control_store()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id,name,environment_id,base_url,login_url,username,browser,headless,timeout_ms,updated_at FROM web_profiles ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return [dict(r) for r in rows]


def get_web_profile(item_id: int, include_secret: bool = True) -> dict | None:
    with _connect() as conn:
        r = conn.execute("SELECT * FROM web_profiles WHERE id=?", (item_id,)).fetchone()
    if not r:
        return None
    out = dict(r)
    out["headless"] = bool(out["headless"])
    out["password"] = _dec(out.pop("password_enc")) if include_secret else ""
    return out


def save_web_profile(data: dict) -> int:
    init_control_store()
    name = (data.get("name") or "").strip()
    base_url = (data.get("base_url") or "").strip()
    if not name or not base_url:
        raise ValueError("Nombre y Base URL son obligatorios.")
    now = _now()
    with _connect() as conn:
        existing = conn.execute("SELECT id,password_enc FROM web_profiles WHERE name=? COLLATE NOCASE", (name,)).fetchone()
        password_enc = _enc(data.get("password")) if data.get("password") else (existing["password_enc"] if existing else None)
        params = (
            data.get("environment_id"), base_url, data.get("login_url", ""), data.get("username", ""), password_enc,
            data.get("browser", "chromium"), int(bool(data.get("headless", False))), int(data.get("timeout_ms", 30000)), now,
        )
        if existing:
            conn.execute(
                """UPDATE web_profiles SET environment_id=?,base_url=?,login_url=?,username=?,password_enc=?,browser=?,headless=?,timeout_ms=?,updated_at=? WHERE id=?""",
                params + (existing["id"],),
            )
            return int(existing["id"])
        cur = conn.execute(
            """INSERT INTO web_profiles(name,environment_id,base_url,login_url,username,password_enc,browser,headless,timeout_ms,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (name,) + params[:-1] + (now, now),
        )
        return int(cur.lastrowid)


def delete_web_profile(item_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM web_profiles WHERE id=?", (item_id,))
        return cur.rowcount > 0


def list_api_profiles() -> list[dict]:
    init_control_store()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id,name,environment_id,base_url,auth_header,verify_tls,timeout_ms,default_headers_json,updated_at FROM api_profiles ORDER BY name COLLATE NOCASE"
        ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["verify_tls"] = bool(item["verify_tls"])
        try:
            item["default_headers"] = json.loads(item.pop("default_headers_json") or "{}")
        except Exception:
            item["default_headers"] = {}
        out.append(item)
    return out


def get_api_profile(item_id: int, include_secret: bool = True) -> dict | None:
    with _connect() as conn:
        r = conn.execute("SELECT * FROM api_profiles WHERE id=?", (item_id,)).fetchone()
    if not r:
        return None
    out = dict(r)
    out["verify_tls"] = bool(out["verify_tls"])
    out["token"] = _dec(out.pop("token_enc")) if include_secret else ""
    try:
        out["default_headers"] = json.loads(out.pop("default_headers_json") or "{}")
    except Exception:
        out["default_headers"] = {}
    return out


def save_api_profile(data: dict) -> int:
    init_control_store()
    name = (data.get("name") or "").strip()
    base_url = (data.get("base_url") or "").strip()
    if not name or not base_url:
        raise ValueError("Nombre y Base URL son obligatorios.")
    now = _now()
    headers = data.get("default_headers") or {}
    if not isinstance(headers, dict):
        raise ValueError("default_headers debe ser un objeto JSON.")
    with _connect() as conn:
        existing = conn.execute("SELECT id,token_enc FROM api_profiles WHERE name=? COLLATE NOCASE", (name,)).fetchone()
        token_enc = _enc(data.get("token")) if data.get("token") else (existing["token_enc"] if existing else None)
        params = (
            data.get("environment_id"), base_url, data.get("auth_header", "Authorization"), token_enc,
            int(bool(data.get("verify_tls", True))), int(data.get("timeout_ms", 30000)), json.dumps(headers, ensure_ascii=False), now,
        )
        if existing:
            conn.execute(
                """UPDATE api_profiles SET environment_id=?,base_url=?,auth_header=?,token_enc=?,verify_tls=?,timeout_ms=?,default_headers_json=?,updated_at=? WHERE id=?""",
                params + (existing["id"],),
            )
            return int(existing["id"])
        cur = conn.execute(
            """INSERT INTO api_profiles(name,environment_id,base_url,auth_header,token_enc,verify_tls,timeout_ms,default_headers_json,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (name,) + params[:-1] + (now, now),
        )
        return int(cur.lastrowid)


def delete_api_profile(item_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM api_profiles WHERE id=?", (item_id,))
        return cur.rowcount > 0


def _mobile_row(row, include_secret: bool = False) -> dict:
    out = dict(row)
    encrypted = out.pop("password_enc", None)
    if include_secret:
        out["password"] = _dec(encrypted)
    out.setdefault("username", "")
    return out


def list_mobile_profiles() -> list[dict]:
    init_control_store()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM mobile_profiles ORDER BY name COLLATE NOCASE").fetchall()
    return [_mobile_row(r, include_secret=False) for r in rows]


def get_mobile_profile(item_id: int, include_secret: bool = False) -> dict | None:
    init_control_store()
    with _connect() as conn:
        r = conn.execute("SELECT * FROM mobile_profiles WHERE id=?", (item_id,)).fetchone()
    return _mobile_row(r, include_secret=include_secret) if r else None


def save_mobile_profile(data: dict) -> int:
    init_control_store()
    name = (data.get("name") or "").strip()
    platform = (data.get("platform") or "android").strip().lower()
    appium_url = (data.get("appium_url") or "http://127.0.0.1:4723").strip().rstrip("/")
    if not name or platform not in {"android", "ios"}:
        raise ValueError("Mobile requiere nombre y platform android/ios.")
    now = _now()
    automation = (data.get("automation_name") or ("UiAutomator2" if platform == "android" else "XCUITest")).strip()
    with _connect() as conn:
        existing = conn.execute("SELECT id,password_enc FROM mobile_profiles WHERE name=? COLLATE NOCASE", (name,)).fetchone()
        password_enc = _enc(data.get("password")) if data.get("password") else (existing["password_enc"] if existing else None)
        values = (
            data.get("environment_id"), platform, appium_url, data.get("device_name", ""), data.get("username", ""), password_enc,
            data.get("udid", ""), data.get("platform_version", ""), automation, data.get("app_package", ""), data.get("app_activity", ""),
            data.get("bundle_id", ""), data.get("app_path", ""), int(data.get("timeout_ms", 30000)), now,
        )
        if existing:
            conn.execute(
                """UPDATE mobile_profiles SET environment_id=?,platform=?,appium_url=?,device_name=?,username=?,password_enc=?,udid=?,platform_version=?,automation_name=?,app_package=?,app_activity=?,bundle_id=?,app_path=?,timeout_ms=?,updated_at=? WHERE id=?""",
                values + (existing["id"],),
            )
            return int(existing["id"])
        cur = conn.execute(
            """INSERT INTO mobile_profiles(name,environment_id,platform,appium_url,device_name,username,password_enc,udid,platform_version,automation_name,app_package,app_activity,bundle_id,app_path,timeout_ms,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name,) + values[:-1] + (now, now),
        )
        return int(cur.lastrowid)


def delete_mobile_profile(item_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM mobile_profiles WHERE id=?", (item_id,))
        return cur.rowcount > 0


def list_test_cases() -> list[dict]:
    init_control_store()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id,case_key,name,description,environment_id,is_active,created_at,updated_at FROM test_cases ORDER BY case_key COLLATE NOCASE"
        ).fetchall()
    return [dict(r) for r in rows]


def get_test_case(item_id: int) -> dict | None:
    with _connect() as conn:
        r = conn.execute("SELECT * FROM test_cases WHERE id=?", (item_id,)).fetchone()
    if not r:
        return None
    out = dict(r)
    out["plan"] = json.loads(out.pop("plan_json"))
    out["is_active"] = bool(out["is_active"])
    return out


def save_test_case(data: dict) -> int:
    init_control_store()
    key = (data.get("case_key") or "").strip()
    name = (data.get("name") or "").strip()
    plan = data.get("plan") or {}
    if not key or not name:
        raise ValueError("ID y nombre del caso son obligatorios.")
    if not isinstance(plan, dict) or not isinstance(plan.get("steps", []), list):
        raise ValueError("El plan debe ser JSON y contener steps como lista.")
    now = _now()
    payload = json.dumps(plan, ensure_ascii=False)
    with _connect() as conn:
        existing = conn.execute("SELECT id FROM test_cases WHERE case_key=? COLLATE NOCASE", (key,)).fetchone()
        if existing:
            conn.execute(
                """UPDATE test_cases SET name=?,description=?,environment_id=?,plan_json=?,is_active=?,updated_at=? WHERE id=?""",
                (name, data.get("description", ""), data.get("environment_id"), payload, int(bool(data.get("is_active", True))), now, existing["id"]),
            )
            return int(existing["id"])
        cur = conn.execute(
            """INSERT INTO test_cases(case_key,name,description,environment_id,plan_json,is_active,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (key, name, data.get("description", ""), data.get("environment_id"), payload, int(bool(data.get("is_active", True))), now, now),
        )
        return int(cur.lastrowid)


def delete_test_case(item_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM test_cases WHERE id=?", (item_id,))
        return cur.rowcount > 0


def create_execution(test_case_id: int | None, case_key: str, case_name: str) -> int:
    init_control_store()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO executions(test_case_id,case_key,case_name,status,started_at) VALUES (?,?,?,?,?)",
            (test_case_id, case_key, case_name, "RUNNING", _now()),
        )
        return int(cur.lastrowid)


def finish_execution(execution_id: int, status: str, duration_ms: float, result: dict, report_json_path: str, report_html_path: str) -> None:
    with _connect() as conn:
        conn.execute(
            """UPDATE executions SET status=?,finished_at=?,duration_ms=?,result_json=?,report_json_path=?,report_html_path=? WHERE id=?""",
            (status, _now(), duration_ms, json.dumps(result, ensure_ascii=False), report_json_path, report_html_path, execution_id),
        )


def list_executions(limit: int = 100) -> list[dict]:
    init_control_store()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id,test_case_id,case_key,case_name,status,started_at,finished_at,duration_ms,report_json_path,report_html_path FROM executions ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    return [dict(r) for r in rows]


def get_execution(item_id: int) -> dict | None:
    with _connect() as conn:
        r = conn.execute("SELECT * FROM executions WHERE id=?", (item_id,)).fetchone()
    if not r:
        return None
    out = dict(r)
    if out.get("result_json"):
        out["result"] = json.loads(out.pop("result_json"))
    return out


def save_qa_experience(execution_id: int, report: dict) -> int:
    init_control_store()
    failed = [
        {"index": s.get("index"), "name": s.get("name"), "type": s.get("type"), "error": s.get("error", "")}
        for s in report.get("steps", []) if s.get("status") == "FAIL"
    ]
    summary = {
        "case_name": report.get("case_name"),
        "duration_ms": report.get("duration_ms"),
        "step_count": len(report.get("steps", [])),
        "passed_types": sorted({s.get("type") for s in report.get("steps", []) if s.get("status") == "PASS" and s.get("type")}),
        "failed_steps": failed,
    }
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO qa_experiences(execution_id,case_key,status,summary_json,created_at) VALUES (?,?,?,?,?)",
            (execution_id, report.get("case_key"), report.get("status", "UNKNOWN"), json.dumps(summary, ensure_ascii=False), _now()),
        )
        return int(cur.lastrowid)


def list_qa_experiences(limit: int = 50) -> list[dict]:
    init_control_store()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id,execution_id,case_key,status,summary_json,verified,created_at FROM qa_experiences ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 200)),),
        ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["summary"] = json.loads(item.pop("summary_json"))
        item["verified"] = bool(item["verified"])
        out.append(item)
    return out
