import json
import time
from urllib.parse import urljoin

import httpx

from .control_store import get_api_profile


def _redact_headers(headers: dict) -> dict:
    redacted = {}
    for k, v in headers.items():
        if k.lower() in {"authorization", "cookie", "set-cookie", "x-api-key", "api-key"}:
            redacted[k] = "***REDACTED***"
        else:
            redacted[k] = v
    return redacted


def _join_url(base_url: str, path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


async def execute_api_request(
    *,
    profile_id: int,
    method: str,
    path: str,
    headers: dict | None = None,
    body: object | None = None,
    params: dict | None = None,
) -> dict:
    profile = get_api_profile(profile_id, include_secret=True)
    if not profile:
        raise ValueError("Perfil API no encontrado.")

    request_headers = dict(profile.get("default_headers") or {})
    request_headers.update(headers or {})
    token = profile.get("token") or ""
    if token:
        header_name = profile.get("auth_header") or "Authorization"
        if header_name.lower() == "authorization" and not token.lower().startswith(("bearer ", "basic ")):
            token = "Bearer " + token
        request_headers[header_name] = token

    url = _join_url(profile["base_url"], path)
    timeout_seconds = max(0.5, int(profile.get("timeout_ms") or 30000) / 1000)
    started = time.perf_counter()
    async with httpx.AsyncClient(verify=bool(profile.get("verify_tls", True)), timeout=timeout_seconds, follow_redirects=True) as client:
        response = await client.request(
            method.upper(),
            url,
            headers=request_headers,
            params=params or None,
            json=body if body is not None else None,
        )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)

    content_type = response.headers.get("content-type", "")
    parsed_body: object
    if "application/json" in content_type.lower():
        try:
            parsed_body = response.json()
        except Exception:
            parsed_body = response.text
    else:
        parsed_body = response.text
        if isinstance(parsed_body, str) and len(parsed_body) > 200000:
            parsed_body = parsed_body[:200000] + "\n...[truncado]"

    return {
        "method": method.upper(),
        "url": str(response.url),
        "status_code": response.status_code,
        "elapsed_ms": elapsed_ms,
        "request_headers": _redact_headers(request_headers),
        "response_headers": _redact_headers(dict(response.headers)),
        "body": parsed_body,
    }


def json_path_get(data: object, path: str) -> object:
    current = data
    for part in (path or "").strip(".").split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current[part]
        elif isinstance(current, list):
            current = current[int(part)]
        else:
            raise KeyError(path)
    return current
