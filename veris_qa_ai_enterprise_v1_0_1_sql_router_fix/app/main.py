import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .ai import generate_sql, provider_info
from .ai_router import router_status, probe_providers
from .agent_memory import init_agent_memory, list_agent_memory, add_knowledge as add_agent_knowledge, save_case_source
from .case_importer import parse_uploaded_case_file
from .qa_planner import plan_cases
from .api_lab import execute_api_request
from .auth import (
    SESSION_COOKIE,
    authenticate,
    create_admin,
    create_session,
    has_users,
    init_auth,
    parse_session,
)
from .browser_manager import browser_manager
from .mobile_manager import mobile_manager
from .connection_store import (
    delete_connection,
    init_connection_store,
    list_connections,
    load_connection,
    save_connection,
)
from .control_store import (
    delete_api_profile,
    delete_environment,
    delete_test_case,
    delete_web_profile,
    delete_mobile_profile,
    get_api_profile,
    get_execution,
    get_test_case,
    get_web_profile,
    get_mobile_profile,
    init_control_store,
    list_api_profiles,
    list_environments,
    list_executions,
    list_qa_experiences,
    list_test_cases,
    list_web_profiles,
    list_mobile_profiles,
    save_api_profile,
    save_environment,
    save_test_case,
    save_web_profile,
    save_mobile_profile,
)
from .db import execute_readonly, invalidate_schema_cache, load_schema, test_connection
from .memory import (
    add_knowledge,
    delete_knowledge,
    find_exact_verified_query,
    init_memory,
    latest_schema_snapshot,
    list_memory,
    record_query,
    retrieve_context,
    save_schema,
    update_query_result,
    verify_query,
)
from .models import (
    ApiLabRequest,
    ApiProfileRequest,
    AskRequest,
    AuthSetupRequest,
    BrowserActionRequest,
    BrowserStartRequest,
    DbConfig,
    DeleteKnowledgeRequest,
    EnvironmentRequest,
    GenerateRequest,
    KnowledgeRequest,
    LoginRequest,
    MemoryListRequest,
    QueryRequest,
    SaveConnectionRequest,
    SavedAskRequest, SavedGenerateRequest, SavedKnowledgeRequest, SavedMemoryListRequest,
    SavedQueryRequest, SavedSchemaRequest, SavedVerifyMemoryRequest,
    TestCaseRequest,
    VerifyMemoryRequest,
    WebProfileRequest,
    MobileProfileRequest, MobileStartRequest, MobileActionRequest,
)
from .qa_runner import environment_check, run_test_case
from .safety import validate_readonly_sql

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
MAX_ROWS = int(os.getenv("MAX_ROWS", "200"))
TIMEOUT_MS = int(os.getenv("STATEMENT_TIMEOUT_MS", "120000"))

init_auth()
init_memory()
init_connection_store()
init_control_store()
init_agent_memory()

app = FastAPI(title="Veris QA AI", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    public = path == "/" or path.startswith("/static/") or path.startswith("/api/auth/") or path == "/api/health"
    if path.startswith("/api/") and not public:
        user = parse_session(request.cookies.get(SESSION_COOKIE))
        if not user:
            return JSONResponse(status_code=401, content={"detail": "Sesión no válida. Inicia sesión."})
        request.state.user = user
    return await call_next(request)


@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    info = provider_info()
    return {
        "ok": True,
        "app": "Veris QA AI",
        "version": "1.0.0",
        "ai_provider": info["provider"],
        "ai_model": info["model"],
        "ai_provider_count": info.get("provider_count", 0),
        "ai_provider_order": info.get("order", []),
        "default_max_rows": MAX_ROWS,
        "statement_timeout_ms": TIMEOUT_MS,
        "playwright_available": browser_manager.available(),
    }


@app.get("/api/ai/status")
def ai_status():
    return router_status()


@app.post("/api/ai/probe")
def ai_probe():
    try:
        return probe_providers()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/agent-memory")
def api_agent_memory(limit: int = 50):
    return list_agent_memory(limit)


@app.post("/api/agent-memory/knowledge")
async def api_agent_knowledge(request: Request):
    try:
        body = await request.json()
        memory_id = add_agent_knowledge(
            str(body.get("kind") or "qa_rule"),
            str(body.get("content") or ""),
            title=str(body.get("title") or ""),
            scope=str(body.get("scope") or "global"),
            source="ui",
            verified=bool(body.get("verified", True)),
        )
        return {"ok": True, "id": memory_id}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------- AUTH ----------------
@app.get("/api/auth/status")
def auth_status(request: Request):
    user = parse_session(request.cookies.get(SESSION_COOKIE))
    return {"setup_required": not has_users(), "authenticated": bool(user), "user": user}


@app.post("/api/auth/setup")
def auth_setup(req: AuthSetupRequest, response: Response):
    try:
        user = create_admin(req.username, req.password)
        response.set_cookie(SESSION_COOKIE, create_session(user), httponly=True, samesite="lax", secure=False, max_age=43200)
        return {"ok": True, "user": user}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/auth/login")
def auth_login(req: LoginRequest, response: Response):
    user = authenticate(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos.")
    response.set_cookie(SESSION_COOKIE, create_session(user), httponly=True, samesite="lax", secure=False, max_age=43200)
    return {"ok": True, "user": user}


@app.post("/api/auth/logout")
def auth_logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


# ---------------- DASHBOARD / SYSTEM ----------------
@app.get("/api/dashboard")
def dashboard():
    executions = list_executions(100)
    return {
        "environments": len(list_environments()),
        "db_connections": len(list_connections()),
        "web_profiles": len(list_web_profiles()),
        "api_profiles": len(list_api_profiles()),
        "mobile_profiles": len(list_mobile_profiles()),
        "test_cases": len(list_test_cases()),
        "executions": len(executions),
        "passed": sum(1 for x in executions if x.get("status") == "PASS"),
        "failed": sum(1 for x in executions if x.get("status") == "FAIL"),
        "blocked": sum(1 for x in executions if x.get("status") == "BLOCKED"),
        "recent_executions": executions[:8],
    }


@app.get("/api/system/check")
def system_check():
    return environment_check()


@app.get("/api/learning/experiences")
def api_learning_experiences(limit: int = 50):
    return {"items": list_qa_experiences(limit)}


# ---------------- POSTGRESQL / SQL LAB ----------------
@app.post("/api/test-connection")
def api_test_connection(cfg: DbConfig):
    try:
        return test_connection(cfg)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/schema")
def api_schema(cfg: DbConfig, force_refresh: bool = False):
    try:
        if not force_refresh:
            snapshot = latest_schema_snapshot(cfg)
            if snapshot:
                return snapshot
        if force_refresh:
            invalidate_schema_cache(cfg)
        schema = load_schema(cfg, force_refresh=force_refresh)
        fp = save_schema(cfg, schema)
        return {**schema, "fingerprint": fp, "persisted_snapshot": False}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


def _schema_for_ai(cfg: DbConfig) -> tuple[dict, str]:
    schema = latest_schema_snapshot(cfg)
    if schema:
        fp = schema.get("fingerprint") or save_schema(cfg, schema)
        return schema, fp
    schema = load_schema(cfg)
    fp = save_schema(cfg, schema)
    return schema, fp


@app.post("/api/query")
def api_query(req: QueryRequest):
    memory_id = None
    try:
        sql = validate_readonly_sql(req.sql)
        memory_id = record_query(req.db, question=None, sql=sql, status="generated", source="manual")
        data = execute_readonly(req.db, sql, req.max_rows or MAX_ROWS, TIMEOUT_MS)
        update_query_result(memory_id, status="success", row_count=data.get("row_count"))
        return {"sql": sql, "memory_id": memory_id, **data}
    except ValueError as e:
        if memory_id:
            update_query_result(memory_id, status="error", error=str(e))
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        if memory_id:
            update_query_result(memory_id, status="error", error=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/generate-sql")
def api_generate(req: GenerateRequest):
    try:
        exact = find_exact_verified_query(req.db, req.question)
        if exact:
            sql = validate_readonly_sql(exact["sql"])
            return {"sql": sql, "memory_id": exact["id"], "schema_table_count": 0, "memory_examples_used": 1, "knowledge_used": 0, "provider": "memory", "model": "verified-query", "reused_verified": True}
        schema, fp = _schema_for_ai(req.db)
        memory_context = retrieve_context(req.db, req.question)
        raw_sql, info = generate_sql(req.question, schema, memory_context)
        memory_id = record_query(req.db, question=req.question, sql=raw_sql, status="generated", provider=info["provider"], model=info["model"], schema_fp=fp, source="ai")
        try:
            sql = validate_readonly_sql(raw_sql)
        except ValueError as e:
            update_query_result(memory_id, status="error", error=str(e))
            raise
        return {"sql": sql, "memory_id": memory_id, "schema_table_count": info.get("schema_tables_sent", len(schema.get("tables", []))), "memory_examples_used": len(memory_context.get("examples", [])), "knowledge_used": len(memory_context.get("knowledge", [])), "provider": info["provider"], "model": info["model"], "reused_verified": False}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/ask")
def api_ask(req: AskRequest):
    memory_id = None
    try:
        exact = find_exact_verified_query(req.db, req.question)
        if exact:
            sql = validate_readonly_sql(exact["sql"])
            data = execute_readonly(req.db, sql, req.max_rows or MAX_ROWS, TIMEOUT_MS)
            return {"question": req.question, "sql": sql, "memory_id": exact["id"], "memory_examples_used": 1, "knowledge_used": 0, "provider": "memory", "model": "verified-query", "reused_verified": True, **data}
        schema, fp = _schema_for_ai(req.db)
        memory_context = retrieve_context(req.db, req.question)
        raw_sql, info = generate_sql(req.question, schema, memory_context)
        memory_id = record_query(req.db, question=req.question, sql=raw_sql, status="generated", provider=info["provider"], model=info["model"], schema_fp=fp, source="ai")
        sql = validate_readonly_sql(raw_sql)
        data = execute_readonly(req.db, sql, req.max_rows or MAX_ROWS, TIMEOUT_MS)
        update_query_result(memory_id, status="success", row_count=data.get("row_count"))
        return {"question": req.question, "sql": sql, "memory_id": memory_id, "memory_examples_used": len(memory_context.get("examples", [])), "knowledge_used": len(memory_context.get("knowledge", [])), "provider": info["provider"], "model": info["model"], "reused_verified": False, **data}
    except ValueError as e:
        if memory_id:
            update_query_result(memory_id, status="error", error=str(e))
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        if memory_id:
            update_query_result(memory_id, status="error", error=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/memory/list")
def api_memory_list(req: MemoryListRequest):
    try:
        return list_memory(req.db, req.limit)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/memory/verify")
def api_memory_verify(req: VerifyMemoryRequest):
    try:
        if not verify_query(req.db, req.memory_id, req.verified):
            raise HTTPException(status_code=404, detail="Memoria no encontrada en este workspace.")
        return {"ok": True, "memory_id": req.memory_id, "verified": req.verified}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/memory/knowledge")
def api_memory_knowledge(req: KnowledgeRequest):
    try:
        return {"ok": True, "memory_id": add_knowledge(req.db, req.content, req.kind)}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/memory/knowledge/delete")
def api_memory_knowledge_delete(req: DeleteKnowledgeRequest):
    try:
        if not delete_knowledge(req.db, req.memory_id):
            raise HTTPException(status_code=404, detail="Memoria no encontrada.")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/connections")
def api_connections_list():
    return {"connections": list_connections()}


@app.get("/api/connections/{connection_id}")
def api_connection_get(connection_id: int):
    item = load_connection(connection_id)
    if not item:
        raise HTTPException(status_code=404, detail="Conexión no encontrada.")
    item = dict(item)
    item["password"] = ""
    return item


@app.post("/api/connections")
def api_connection_save(req: SaveConnectionRequest):
    try:
        connection_id = save_connection(req.name, req.db)
        test = test_connection(req.db)
        return {"ok": True, "id": connection_id, "connection_ms": test.get("elapsed_ms")}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/connections/{connection_id}")
def api_connection_delete(connection_id: int):
    if not delete_connection(connection_id):
        raise HTTPException(status_code=404, detail="Conexión no encontrada.")
    return {"ok": True}


# ---------------- SQL LAB SOBRE CONEXIONES GUARDADAS ----------------
def _saved_cfg(connection_id: int) -> DbConfig:
    item = load_connection(connection_id)
    if not item:
        raise HTTPException(status_code=404, detail="Conexión PostgreSQL guardada no encontrada.")
    return DbConfig(**{k: item[k] for k in ("host","port","database","user","password","sslmode","schema_name")})


@app.post("/api/sql/schema")
def api_saved_schema(req: SavedSchemaRequest):
    cfg = _saved_cfg(req.connection_id)
    return api_schema(cfg, force_refresh=req.force_refresh)


@app.post("/api/sql/query")
def api_saved_query(req: SavedQueryRequest):
    return api_query(QueryRequest(db=_saved_cfg(req.connection_id), sql=req.sql, max_rows=req.max_rows))


@app.post("/api/sql/generate")
def api_saved_generate(req: SavedGenerateRequest):
    return api_generate(GenerateRequest(db=_saved_cfg(req.connection_id), question=req.question))


@app.post("/api/sql/ask")
def api_saved_ask(req: SavedAskRequest):
    return api_ask(AskRequest(db=_saved_cfg(req.connection_id), question=req.question, max_rows=req.max_rows))


@app.post("/api/sql/memory/list")
def api_saved_memory_list(req: SavedMemoryListRequest):
    return api_memory_list(MemoryListRequest(db=_saved_cfg(req.connection_id), limit=req.limit))


@app.post("/api/sql/memory/verify")
def api_saved_memory_verify(req: SavedVerifyMemoryRequest):
    return api_memory_verify(VerifyMemoryRequest(db=_saved_cfg(req.connection_id), memory_id=req.memory_id, verified=req.verified))


@app.post("/api/sql/memory/knowledge")
def api_saved_memory_knowledge(req: SavedKnowledgeRequest):
    return api_memory_knowledge(KnowledgeRequest(db=_saved_cfg(req.connection_id), content=req.content, kind=req.kind))


# ---------------- ENVIRONMENTS ----------------
@app.get("/api/environments")
def api_environments():
    return {"items": list_environments()}


@app.post("/api/environments")
def api_environment_save(req: EnvironmentRequest):
    try:
        return {"ok": True, "id": save_environment(req.model_dump())}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.delete("/api/environments/{item_id}")
def api_environment_delete(item_id: int):
    if not delete_environment(item_id):
        raise HTTPException(status_code=404, detail="Ambiente no encontrado.")
    return {"ok": True}


# ---------------- WEB PROFILES / MANAGED BROWSER ----------------
@app.get("/api/web-profiles")
def api_web_profiles():
    return {"items": list_web_profiles()}


@app.get("/api/web-profiles/{item_id}")
def api_web_profile_get(item_id: int):
    item = get_web_profile(item_id, include_secret=False)
    if not item:
        raise HTTPException(status_code=404, detail="Perfil Web no encontrado.")
    return item


@app.post("/api/web-profiles")
def api_web_profile_save(req: WebProfileRequest):
    try:
        return {"ok": True, "id": save_web_profile(req.model_dump())}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.delete("/api/web-profiles/{item_id}")
def api_web_profile_delete(item_id: int):
    if not delete_web_profile(item_id):
        raise HTTPException(status_code=404, detail="Perfil Web no encontrado.")
    return {"ok": True}


@app.post("/api/browser/start")
async def api_browser_start(req: BrowserStartRequest):
    profile = get_web_profile(req.profile_id, include_secret=True)
    if not profile:
        raise HTTPException(status_code=404, detail="Perfil Web no encontrado.")
    try:
        return await browser_manager.start(profile)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/browser/action")
async def api_browser_action(req: BrowserActionRequest):
    try:
        return await browser_manager.action(req.session_id, req.action, **req.config)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/browser/{session_id}/status")
async def api_browser_status(session_id: str, screenshot: bool = True):
    try:
        return await browser_manager.status(session_id, include_screenshot=screenshot)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/browser/{session_id}")
async def api_browser_stop(session_id: str):
    return await browser_manager.stop(session_id)


# ---------------- MOBILE PROFILES / APPIUM ----------------
@app.get("/api/mobile-profiles")
def api_mobile_profiles():
    return {"items": list_mobile_profiles()}


@app.get("/api/mobile-profiles/{item_id}")
def api_mobile_profile_get(item_id: int):
    item = get_mobile_profile(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Perfil Mobile no encontrado.")
    return item


@app.post("/api/mobile-profiles")
def api_mobile_profile_save(req: MobileProfileRequest):
    try:
        return {"ok": True, "id": save_mobile_profile(req.model_dump())}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/mobile-profiles/{item_id}")
def api_mobile_profile_delete(item_id: int):
    if not delete_mobile_profile(item_id):
        raise HTTPException(status_code=404, detail="Perfil Mobile no encontrado.")
    return {"ok": True}


@app.post("/api/mobile/start")
async def api_mobile_start(req: MobileStartRequest):
    profile = get_mobile_profile(req.profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Perfil Mobile no encontrado.")
    try:
        return await mobile_manager.start(profile)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/mobile/action")
async def api_mobile_action(req: MobileActionRequest):
    try:
        return await mobile_manager.action(req.session_id, req.action, **req.config)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/mobile/{session_id}/status")
async def api_mobile_status(session_id: str, screenshot: bool = True):
    try:
        return await mobile_manager.status(session_id, include_screenshot=screenshot)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/mobile/{session_id}")
async def api_mobile_stop(session_id: str):
    return await mobile_manager.stop(session_id)


# ---------------- API PROFILES / API LAB ----------------
@app.get("/api/api-profiles")
def api_api_profiles():
    return {"items": list_api_profiles()}


@app.get("/api/api-profiles/{item_id}")
def api_api_profile_get(item_id: int):
    item = get_api_profile(item_id, include_secret=False)
    if not item:
        raise HTTPException(status_code=404, detail="Perfil API no encontrado.")
    return item


@app.post("/api/api-profiles")
def api_api_profile_save(req: ApiProfileRequest):
    try:
        return {"ok": True, "id": save_api_profile(req.model_dump())}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.delete("/api/api-profiles/{item_id}")
def api_api_profile_delete(item_id: int):
    if not delete_api_profile(item_id):
        raise HTTPException(status_code=404, detail="Perfil API no encontrado.")
    return {"ok": True}


@app.post("/api/api-lab/request")
async def api_lab_request(req: ApiLabRequest):
    try:
        return await execute_api_request(profile_id=req.profile_id, method=req.method, path=req.path, headers=req.headers, params=req.params, body=req.body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------- CASE IMPORT / AI PLANNER ----------------
@app.post("/api/case-import/interpret")
async def api_case_import_interpret(
    file: UploadFile = File(...),
    environment_id: int | None = Form(default=None),
    web_profile_id: int | None = Form(default=None),
    db_connection_id: int | None = Form(default=None),
    api_profile_id: int | None = Form(default=None),
    mobile_profile_id: int | None = Form(default=None),
    auto_save: bool = Form(default=False),
):
    try:
        data = await file.read()
        parsed = parse_uploaded_case_file(file.filename or "cases", data)
        source_id = save_case_source(parsed["filename"], parsed["source_type"], parsed["sha256"], {
            "row_count": parsed["row_count"], "case_count": len(parsed["cases"])
        })
        defaults = {
            "environment_id": environment_id, "web_profile_id": web_profile_id,
            "db_connection_id": db_connection_id, "api_profile_id": api_profile_id, "mobile_profile_id": mobile_profile_id,
        }
        source_meta = {"source_id": source_id, "filename": parsed["filename"], "sha256": parsed["sha256"], "source_type": parsed["source_type"]}
        planned, meta = await plan_cases(parsed["cases"], defaults, source_meta)
        saved = []
        for item in planned:
            item["plan"].setdefault("source", {}).update(source_meta)
            if auto_save:
                case_id = save_test_case({
                    "case_key": item["case_key"], "name": item["name"], "description": item.get("description", ""),
                    "environment_id": environment_id, "plan": item["plan"], "is_active": True,
                })
                saved.append({"id": case_id, "case_key": item["case_key"]})
        return {
            "source": source_meta, "row_count": parsed["row_count"], "raw_preview": parsed["raw_preview"],
            "cases": planned, "saved": saved, "planner": meta,
        }
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------- TEST CASES / EXECUTIONS ----------------
@app.get("/api/test-cases")
def api_test_cases():
    return {"items": list_test_cases()}


@app.get("/api/test-cases/{item_id}")
def api_test_case_get(item_id: int):
    item = get_test_case(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    return item


@app.post("/api/test-cases")
def api_test_case_save(req: TestCaseRequest):
    try:
        return {"ok": True, "id": save_test_case(req.model_dump())}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.delete("/api/test-cases/{item_id}")
def api_test_case_delete(item_id: int):
    if not delete_test_case(item_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    return {"ok": True}


@app.post("/api/test-cases/{item_id}/run")
async def api_test_case_run(item_id: int):
    try:
        return await run_test_case(item_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/executions")
def api_executions(limit: int = 100):
    return {"items": list_executions(limit)}


@app.get("/api/executions/{item_id}")
def api_execution_get(item_id: int):
    item = get_execution(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Ejecución no encontrada.")
    return item


@app.get("/api/executions/{item_id}/report/{format_name}")
def api_execution_report(item_id: int, format_name: str):
    item = get_execution(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Ejecución no encontrada.")
    key = "report_html_path" if format_name.lower() == "html" else "report_json_path"
    path = item.get(key)
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Reporte no encontrado.")
    return FileResponse(path)
