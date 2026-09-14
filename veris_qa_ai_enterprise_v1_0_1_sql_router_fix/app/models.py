from typing import Any, Optional

from pydantic import BaseModel, Field


class DbConfig(BaseModel):
    host: str
    port: int = 5432
    database: str
    user: str
    password: str
    sslmode: str = "prefer"
    schema_name: str = "public"


class QueryRequest(BaseModel):
    db: DbConfig
    sql: str
    max_rows: Optional[int] = Field(default=None, ge=1, le=5000)


class AskRequest(BaseModel):
    db: DbConfig
    question: str = Field(min_length=2, max_length=4000)
    max_rows: Optional[int] = Field(default=None, ge=1, le=5000)


class GenerateRequest(BaseModel):
    db: DbConfig
    question: str = Field(min_length=2, max_length=4000)


class VerifyMemoryRequest(BaseModel):
    db: DbConfig
    memory_id: int = Field(ge=1)
    verified: bool = True


class KnowledgeRequest(BaseModel):
    db: DbConfig
    content: str = Field(min_length=2, max_length=4000)
    kind: str = Field(default="business_rule", min_length=2, max_length=80)


class DeleteKnowledgeRequest(BaseModel):
    db: DbConfig
    memory_id: int = Field(ge=1)


class MemoryListRequest(BaseModel):
    db: DbConfig
    limit: int = Field(default=50, ge=1, le=200)


class SaveConnectionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    db: DbConfig


class AuthSetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8, max_length=200)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class EnvironmentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    is_active: bool = True


class WebProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    environment_id: Optional[int] = None
    base_url: str = Field(min_length=4, max_length=2000)
    login_url: str = Field(default="", max_length=2000)
    username: str = Field(default="", max_length=300)
    password: str = Field(default="", max_length=1000)
    browser: str = "chromium"
    headless: bool = False
    timeout_ms: int = Field(default=30000, ge=1000, le=300000)


class ApiProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    environment_id: Optional[int] = None
    base_url: str = Field(min_length=4, max_length=2000)
    auth_header: str = Field(default="Authorization", max_length=200)
    token: str = Field(default="", max_length=5000)
    verify_tls: bool = True
    timeout_ms: int = Field(default=30000, ge=1000, le=300000)
    default_headers: dict[str, str] = Field(default_factory=dict)


class ApiLabRequest(BaseModel):
    profile_id: int = Field(ge=1)
    method: str = Field(default="GET", max_length=10)
    path: str = Field(default="", max_length=4000)
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    body: Any = None


class BrowserStartRequest(BaseModel):
    profile_id: int = Field(ge=1)


class BrowserActionRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=100)
    action: str = Field(min_length=2, max_length=60)
    config: dict[str, Any] = Field(default_factory=dict)


class TestCaseRequest(BaseModel):
    case_key: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=4000)
    environment_id: Optional[int] = None
    plan: dict[str, Any]
    is_active: bool = True

class SavedSchemaRequest(BaseModel):
    connection_id: int = Field(ge=1)
    force_refresh: bool = False


class SavedQueryRequest(BaseModel):
    connection_id: int = Field(ge=1)
    sql: str
    max_rows: Optional[int] = Field(default=None, ge=1, le=5000)


class SavedAskRequest(BaseModel):
    connection_id: int = Field(ge=1)
    question: str = Field(min_length=2, max_length=4000)
    max_rows: Optional[int] = Field(default=None, ge=1, le=5000)


class SavedGenerateRequest(BaseModel):
    connection_id: int = Field(ge=1)
    question: str = Field(min_length=2, max_length=4000)


class SavedMemoryListRequest(BaseModel):
    connection_id: int = Field(ge=1)
    limit: int = Field(default=50, ge=1, le=200)


class SavedVerifyMemoryRequest(BaseModel):
    connection_id: int = Field(ge=1)
    memory_id: int = Field(ge=1)
    verified: bool = True


class SavedKnowledgeRequest(BaseModel):
    connection_id: int = Field(ge=1)
    content: str = Field(min_length=2, max_length=4000)
    kind: str = Field(default="business_rule", min_length=2, max_length=80)

class MobileProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    environment_id: Optional[int] = None
    platform: str = Field(default="android", max_length=20)
    appium_url: str = Field(default="http://127.0.0.1:4723", max_length=2000)
    device_name: str = Field(default="", max_length=300)
    username: str = Field(default="", max_length=300)
    password: str = Field(default="", max_length=1000)
    udid: str = Field(default="", max_length=300)
    platform_version: str = Field(default="", max_length=100)
    automation_name: str = Field(default="", max_length=100)
    app_package: str = Field(default="", max_length=500)
    app_activity: str = Field(default="", max_length=500)
    bundle_id: str = Field(default="", max_length=500)
    app_path: str = Field(default="", max_length=2000)
    timeout_ms: int = Field(default=30000, ge=1000, le=300000)


class MobileStartRequest(BaseModel):
    profile_id: int = Field(ge=1)


class MobileActionRequest(BaseModel):
    session_id: str = Field(min_length=4, max_length=200)
    action: str = Field(min_length=2, max_length=60)
    config: dict[str, Any] = Field(default_factory=dict)
