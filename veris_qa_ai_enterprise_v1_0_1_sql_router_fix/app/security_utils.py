import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

BASE_DIR = Path(__file__).resolve().parent.parent
AUDIT_PATH = Path(os.getenv('AUDIT_LOG_PATH', str(BASE_DIR / 'data' / 'audit.jsonl')))
_AUDIT_LOCK = Lock()

_REDACT_PATTERNS = [
    (re.compile(r'(?i)(authorization\s*[:=]\s*bearer\s+)[A-Za-z0-9._~+/=-]+'), r'\1[REDACTED]'),
    (re.compile(r'(?i)(api[_-]?key|token|password|passwd|secret)(\s*[=:]\s*)[^\s,;\]\}\"]+'), r'\1\2[REDACTED]'),
    (re.compile(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b'), '[EMAIL_REDACTED]'),
    (re.compile(r'(?<!\d)(?:\+?593[-\s]?)?0?9\d{8}(?!\d)'), '[PHONE_REDACTED]'),
    (re.compile(r'(?<!\d)\d{10}(?!\d)'), '[ID_REDACTED]'),
]


def redact_text(value: str, max_chars: int | None = None) -> str:
    text = str(value or '')
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars] + '…[TRUNCATED]'
    return text


def redact_obj(value, depth: int = 0):
    if depth > 8:
        return '[MAX_DEPTH]'
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = str(k)
            if re.search(r'(?i)(password|passwd|secret|token|api[_-]?key|cookie|authorization)', key):
                out[key] = '[REDACTED]'
            else:
                out[key] = redact_obj(v, depth + 1)
        return out
    if isinstance(value, list):
        return [redact_obj(v, depth + 1) for v in value[:500]]
    if isinstance(value, tuple):
        return [redact_obj(v, depth + 1) for v in value[:500]]
    if isinstance(value, str):
        return redact_text(value, 12000)
    return value


def audit(event: str, details: dict | None = None, actor: str | None = None) -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'event': event,
        'actor': actor or 'system',
        'details': redact_obj(details or {}),
    }
    line = json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n'
    with _AUDIT_LOCK:
        with AUDIT_PATH.open('a', encoding='utf-8') as f:
            f.write(line)
