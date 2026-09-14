import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .security_utils import redact_obj, redact_text

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
DB_PATH = Path(os.getenv('AGENT_MEMORY_DB_PATH', str(DATA_DIR / 'agent_memory.sqlite3')))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn


def init_agent_memory() -> None:
    with _connect() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS knowledge (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          kind TEXT NOT NULL,
          scope TEXT NOT NULL DEFAULT 'global',
          title TEXT,
          content TEXT NOT NULL,
          verified INTEGER NOT NULL DEFAULT 0,
          source TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS case_sources (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          source_name TEXT NOT NULL,
          source_type TEXT NOT NULL,
          sha256 TEXT NOT NULL,
          summary_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_case_sources_sha ON case_sources(sha256);
        CREATE TABLE IF NOT EXISTS agent_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          purpose TEXT NOT NULL,
          provider TEXT,
          model TEXT,
          success INTEGER NOT NULL,
          input_summary TEXT,
          output_summary TEXT,
          error TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS playbooks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          case_key TEXT,
          title TEXT NOT NULL,
          status TEXT NOT NULL,
          plan_json TEXT NOT NULL,
          lessons_json TEXT NOT NULL DEFAULT '{}',
          verified INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        ''')


def add_knowledge(kind: str, content: str, title: str = '', scope: str = 'global', source: str = '', verified: bool = False) -> int:
    init_agent_memory()
    clean = redact_text(content, 20000).strip()
    if not clean:
        raise ValueError('El conocimiento no puede estar vacío.')
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            'INSERT INTO knowledge(kind,scope,title,content,verified,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',
            (kind[:80], scope[:120], title[:300], clean, int(verified), source[:500], now, now),
        )
        return int(cur.lastrowid)


def _tokens(text: str) -> set[str]:
    words = re.findall(r'[a-zA-Z0-9_áéíóúñÁÉÍÓÚÑ]{3,}', (text or '').lower())
    stop = {'para','como','esto','esta','este','que','los','las','una','uno','por','con','del','desde','hasta','caso','prueba','qa'}
    return {w for w in words if w not in stop}


def retrieve_context(query: str, limit: int = 12) -> dict:
    init_agent_memory()
    q_tokens = _tokens(query)
    with _connect() as conn:
        knowledge = [dict(r) for r in conn.execute('SELECT * FROM knowledge ORDER BY verified DESC,id DESC LIMIT 300').fetchall()]
        playbooks = [dict(r) for r in conn.execute('SELECT * FROM playbooks ORDER BY verified DESC,id DESC LIMIT 150').fetchall()]
    def score(text: str) -> int:
        t = _tokens(text)
        return len(q_tokens & t) * 10 + (3 if q_tokens and q_tokens <= t else 0)
    knowledge.sort(key=lambda r: (score((r.get('title') or '')+' '+(r.get('content') or '')), r.get('verified',0), r.get('id',0)), reverse=True)
    playbooks.sort(key=lambda r: (score((r.get('case_key') or '')+' '+(r.get('title') or '')+' '+(r.get('plan_json') or '')), r.get('verified',0), r.get('id',0)), reverse=True)
    out_p = []
    for p in playbooks[:max(4, limit//2)]:
        try: plan = json.loads(p.get('plan_json') or '{}')
        except Exception: plan = {}
        try: lessons = json.loads(p.get('lessons_json') or '{}')
        except Exception: lessons = {}
        out_p.append({k:p.get(k) for k in ('id','case_key','title','status','verified','created_at')} | {'plan': plan, 'lessons': lessons})
    return {'knowledge': knowledge[:limit], 'playbooks': out_p}


def record_agent_run(purpose: str, provider: str | None, model: str | None, success: bool, input_summary: str = '', output_summary: str = '', error: str = '', metadata: dict | None = None) -> int:
    init_agent_memory()
    with _connect() as conn:
        cur = conn.execute(
            'INSERT INTO agent_runs(purpose,provider,model,success,input_summary,output_summary,error,metadata_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
            (purpose[:80], provider, model, int(success), redact_text(input_summary,4000), redact_text(output_summary,6000), redact_text(error,3000), json.dumps(redact_obj(metadata or {}),ensure_ascii=False), _now()),
        )
        return int(cur.lastrowid)


def save_case_source(source_name: str, source_type: str, sha256: str, summary: dict) -> int:
    init_agent_memory()
    with _connect() as conn:
        existing = conn.execute('SELECT id FROM case_sources WHERE sha256=?',(sha256,)).fetchone()
        if existing:
            return int(existing['id'])
        cur = conn.execute('INSERT INTO case_sources(source_name,source_type,sha256,summary_json,created_at) VALUES (?,?,?,?,?)',
            (source_name[:500], source_type[:50], sha256, json.dumps(redact_obj(summary),ensure_ascii=False), _now()))
        return int(cur.lastrowid)


def save_playbook(case_key: str, title: str, status: str, plan: dict, lessons: dict | None = None, verified: bool = False) -> int:
    init_agent_memory(); now = _now()
    with _connect() as conn:
        cur = conn.execute('INSERT INTO playbooks(case_key,title,status,plan_json,lessons_json,verified,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',
            (case_key[:120], title[:300], status[:40], json.dumps(redact_obj(plan),ensure_ascii=False), json.dumps(redact_obj(lessons or {}),ensure_ascii=False), int(verified), now, now))
        return int(cur.lastrowid)


def list_agent_memory(limit: int = 50) -> dict:
    init_agent_memory(); n=max(1,min(int(limit),200))
    with _connect() as conn:
        ks=[dict(r) for r in conn.execute('SELECT id,kind,scope,title,content,verified,source,created_at FROM knowledge ORDER BY id DESC LIMIT ?',(n,)).fetchall()]
        rs=[dict(r) for r in conn.execute('SELECT id,purpose,provider,model,success,error,created_at FROM agent_runs ORDER BY id DESC LIMIT ?',(n,)).fetchall()]
        ps=[dict(r) for r in conn.execute('SELECT id,case_key,title,status,verified,created_at FROM playbooks ORDER BY id DESC LIMIT ?',(n,)).fetchall()]
    return {'knowledge':ks,'agent_runs':rs,'playbooks':ps}
