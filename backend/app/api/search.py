"""FastAPI route: search_documents — wires auth -> filter -> hybrid search.

This is the shape the LangGraph agent's search_documents tool should call
into. summarize_stage and check_gaps should follow the same pattern:
resolve UserContext -> build_access_filter -> query, never bypassing
build_access_filter.
"""

from pathlib import Path
from uuid import uuid4
import hmac
import html
import json
import hashlib
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from docx.shared import Inches, Pt
from pydantic import BaseModel, Field, field_validator
from qdrant_client import QdrantClient, models

from app.config import settings
from app.agent_runtime import create_default_state, handle_cli_message
from app.auth import _hash_password, authenticate, create_oauth_state, create_token, exchange_google_code, google_authorization_url, google_is_configured, oauth_state_cookie_kwargs, user_from_token, validate_oauth_state
from app.telemetry import PerformanceMetrics
from app.database import (
    create_user, delete_user, delete_document, get_user_by_provider_subject, get_user_by_username, get_user_by_email, initialize_database,
    list_all_documents as database_list_all_documents, list_documents as database_list_documents,
    list_projects as database_list_projects, record_document, get_valid_stages, get_user_role,
    set_user_role, remove_user_role, get_document_workflow_state, approve_document, reject_document, audit_log,
    get_audit_log, get_user_notifications, get_project_activity, get_user_by_id, get_user_access_context, get_document, get_project, list_users,
    link_user_provider, set_password, create_password_reset_token, consume_password_reset_token,
    get_valid_organization_invitation, accept_organization_invitation,
    create_email_verification_token, consume_email_verification_token,
    create_organization,
    submit_document, update_user_access, create_project, create_note, list_notes,
    create_document_version, list_document_versions, create_personal_document,
    list_personal_documents, create_chat_session, list_chat_sessions,
    get_chat_session, add_chat_message, list_chat_messages, delete_chat_session
)
from app.authorization import ADMIN, TEAM_LEAD, can_create_project, can_view_document, require_action, require_document_action, require_project, require_tenant, role_for_project
from app.ingestion.chunking import ParsedSection, chunk_document
from app.ingestion.document_text import extract_text
from app.ingestion.indexing import index_chunks, update_document_workflow_state
from app.ingestion.collection_setup import collection_name
from app.models.schema import UserContext
from app.retrieval.access_filter import build_access_filter
from app.retrieval.embeddings import embed_dense, embed_sparse, generate_answer, is_general_question, review_document_lines
from app.retrieval.hybrid_search import hybrid_search
from app.services.document_workflow import (
    extract_document_context,
    generate_draft,
    reform_document,
    save_document,
    load_saved_documents,
    score_document,
)

app = FastAPI(
    title="DocFlow AI — Unified Workspace API",
    version="2.0.0-local",
    summary="Document workflow, grounded AI, and project collaboration API",
    description="""
## DocFlow AI API

The unified local API behind the Neo-style DocFlow workspace. It combines the
reference product flow with the extended implementation in this repository:

- **Identity**: email/password, demo access, and Google OAuth
- **Workspace**: projects, stages, sources, uploads, and document versions
- **AI**: grounded hybrid search, assistant answers, follow-up prompts, and gap analysis
- **Studio**: PRD/BRD/ARD/test-plan drafting, scanning, revision, and saved drafts
- **Governance**: project roles, sensitivity rules, approval workflow, audit log, and policy simulation
- **Personal workspace**: private notes

All data is local by default: SQLite, Qdrant, uploads, and generated drafts are
stored under `backend/`. Bearer tokens are used for authenticated requests.
""",
    openapi_tags=[
        {"name": "Identity", "description": "Sign-in, signup, demo access, and current user context."},
        {"name": "Workspace", "description": "Projects, stages, documents, and upload workflows."},
        {"name": "Assistant", "description": "Grounded search, answers, and follow-up suggestions."},
        {"name": "Studio", "description": "AI drafting, scanning, revision, and saved drafts."},
        {"name": "Governance", "description": "Approvals, RBAC, ABAC, admin access, and audit events."},
        {"name": "Notes", "description": "Private user notes and project-linked notes."},
        {"name": "System", "description": "Health and internal runtime endpoints."},
    ],
)
initialize_database()

# The React SPA (Vite dev server / built bundle) runs on a different origin
# than this API, so it needs an explicit CORS allowance. Bearer tokens are
# sent via the Authorization header (not cookies), so credentials aren't needed.
frontend_origin = "{}://{}".format(urlsplit(settings.frontend_url).scheme, urlsplit(settings.frontend_url).netloc)
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted({frontend_origin, "http://localhost:5173", "http://127.0.0.1:5173"}),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

bearer = HTTPBearer(auto_error=False)
UPLOADS_DIR = Path(settings.uploads_path)
MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


@app.post("/agent/chat", include_in_schema=False)
def agent_chat(payload: dict):
    """Stateful CLI-style /draft, /scan chat loop (used by the terminal `cli.py`;
    kept here too in case a future UI wants the multi-turn console experience)."""
    message = (payload or {}).get("message", "")
    state = (payload or {}).get("state") or create_default_state()
    result = handle_cli_message(message, state)
    return result

_qdrant_client: QdrantClient | None = None


def get_qdrant_client() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        if settings.qdrant_url:
            _qdrant_client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
        else:
            _qdrant_client = QdrantClient(path=settings.qdrant_path)
    return _qdrant_client


def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> UserContext:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        return user_from_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def get_optional_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> UserContext:
    if credentials is not None and credentials.credentials:
        try:
            return user_from_token(credentials.credentials)
        except ValueError:
            pass
    raise HTTPException(status_code=401, detail="Authentication required")


class AccessContext(BaseModel):
    user_id: str
    username: str | None = None
    full_name: str | None = None
    organization: str | None = None
    team_name: str | None = None
    job_title: str | None = None
    tenant_id: str
    is_org_admin: bool
    role: str
    sensitivity_clearance: int
    accessible_projects: list[str]
    project_roles: dict[str, str] = Field(default_factory=dict)
    must_change_password: bool = False


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str


class PasswordChangeRequest(BaseModel):
    current_password: str | None = None
    new_password: str = Field(min_length=8, max_length=256)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20)
    new_password: str = Field(min_length=8, max_length=256)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    is_new_user: bool = False
    must_change_password: bool = False


class SignupRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=256)
    full_name: str = Field(min_length=2, max_length=120)
    invitation_token: str = Field(min_length=20, max_length=200)
    team_name: str | None = Field(default=None, max_length=120)
    job_title: str | None = Field(default=None, max_length=120)
    manager_email: str | None = Field(default=None, max_length=254)


class OrganizationSignupRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=256)
    full_name: str = Field(min_length=2, max_length=120)
    organization_name: str = Field(min_length=2, max_length=160)
    job_title: str | None = Field(default=None, max_length=120)


def _send_verification_email(
    email: str,
    user_id: str,
    *,
    organization_name: str | None = None,
) -> None:
    from app.services.email_notifications import send_notification_email
    if organization_name:
        subject = f"{organization_name} is onboarded on DocFlow"
        body = (
            f"Welcome to DocFlow. {organization_name} has been successfully onboarded.\n\n"
            "You are the first Organization Admin for this workspace. You can sign in, "
            "create projects, and invite your team members immediately.\n\n"
            "Complete onboarding:\n"
            "1. Sign in with the email address and password you registered.\n"
            "2. Open the Admin area to invite your organization members.\n\n"
            "If you did not create this organization, contact your DocFlow administrator."
        )
    else:
        subject = "Welcome to DocFlow"
        body = (
            "Welcome to DocFlow. Your organization administrator invited you to join "
            "the workspace.\n\n"
            "You can sign in immediately with the email address and password you registered."
        )
    send_notification_email(email, subject, body)


@app.post("/organizations/signup", status_code=201)
def signup_organization(request: OrganizationSignupRequest):
    """Create an organization and its first Organization Admin."""
    email = request.email.strip().lower()
    organization_name = request.organization_name.strip()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    if email == settings.auth_username.lower() or get_user_by_email(email):
        raise HTTPException(
            status_code=409,
            detail="This email is already associated with an organization account and cannot create another organization.",
        )

    tenant_id = str(uuid4())
    create_organization(tenant_id, organization_name)
    salt = settings.auth_secret.encode()[:16].ljust(16, b"0")
    user = create_user(
        username=email,
        password_hash=_hash_password(request.password, salt),
        provider="password",
        provider_subject=None,
        tenant_id=tenant_id,
        full_name=request.full_name.strip(),
        organization=organization_name,
        job_title=request.job_title.strip() if request.job_title else None,
        is_org_admin=True,
        email_verified=True,
    )
    _send_verification_email(
        email,
        user["user_id"],
        organization_name=organization_name,
    )
    return {
        "status": "created",
        "message": "Organization created. You can now sign in as the Organization Admin.",
        "organization_id": tenant_id,
    }


@app.post("/signup", status_code=201)
def signup(request: SignupRequest):
    email = request.email.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    invitation = get_valid_organization_invitation(
        hashlib.sha256(request.invitation_token.encode()).hexdigest()
    )
    if not invitation or not hmac.compare_digest(invitation["email"], email):
        raise HTTPException(status_code=400, detail="This invitation is invalid or expired")
    if email == settings.auth_username.lower() or get_user_by_email(email):
        raise HTTPException(status_code=409, detail="An account with that email already exists")
    salt = settings.auth_secret.encode()[:16].ljust(16, b"0")
    user = create_user(
        username=email,
        password_hash=_hash_password(request.password, salt),
        provider="password",
        provider_subject=None,
        tenant_id=invitation["tenant_id"],
        full_name=request.full_name.strip(),
        organization=invitation["tenant_id"],
        team_name=request.team_name.strip() if request.team_name else None,
        job_title=request.job_title.strip() if request.job_title else None,
        manager_email=request.manager_email.strip().lower() if request.manager_email else None,
        email_verified=True,
    )
    accept_organization_invitation(invitation["invitation_id"])
    _send_verification_email(email, user["user_id"])
    return {"status": "created", "message": "Account created. You can now sign in."}


@app.post("/auth/verify-email")
def verify_email(token: str):
    user_id = consume_email_verification_token(hashlib.sha256(token.encode()).hexdigest())
    if not user_id:
        raise HTTPException(status_code=400, detail="This verification link is invalid or expired")
    return {"status": "verified"}


@app.post("/login", response_model=LoginResponse)
def login(request: LoginRequest):
    email = request.email.strip().lower()
    user = get_user_by_email(email)
    token = authenticate(email, request.password)
    if token is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return LoginResponse(access_token=token, must_change_password=bool(user and user.get("must_change_password")))


@app.post("/auth/change-password")
def change_password(
    request: PasswordChangeRequest,
    user: UserContext = Depends(get_current_user),
):
    target = get_user_by_id(user.user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User was not found")
    if request.current_password is not None:
        if authenticate(target["username"], request.current_password) is None:
            raise HTTPException(status_code=401, detail="Current password is incorrect")
    salt = settings.auth_secret.encode()[:16].ljust(16, b"0")
    set_password(user.user_id, _hash_password(request.new_password, salt), False)
    return {"status": "updated"}


@app.post("/auth/forgot-password")
def forgot_password(request: ForgotPasswordRequest):
    user = get_user_by_username(request.email.strip().lower())
    # Avoid revealing whether an account exists.
    if user:
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        create_password_reset_token(token_hash, user["user_id"], expires_at)
        from app.services.email_notifications import send_notification_email
        send_notification_email(
            user["username"],
            "Reset your DocFlow password",
            f"Use this link within 30 minutes to reset your password:\n"
            f"{settings.frontend_url.rstrip('/')}/login?reset_token={raw_token}",
        )
    return {"message": "If an account exists for that email, a reset link has been sent."}


@app.post("/auth/reset-password")
def reset_password(request: ResetPasswordRequest):
    token_hash = hashlib.sha256(request.token.encode()).hexdigest()
    user_id = consume_password_reset_token(token_hash)
    if not user_id:
        raise HTTPException(status_code=400, detail="This reset link is invalid or expired")
    salt = settings.auth_secret.encode()[:16].ljust(16, b"0")
    set_password(user_id, _hash_password(request.new_password, salt), False)
    return {"status": "updated"}


@app.post("/auth/super-admin", response_model=LoginResponse, tags=["Identity"])
def super_admin_login():
    """Local super-admin access with tenant-wide project and agent visibility."""
    demo_email = "demo.user@docflow.ai"
    user = get_user_by_username(demo_email)
    if user is None:
        user = create_user(
            username=demo_email,
            password_hash=None,
            provider="demo",
            provider_subject="demo-user",
            full_name="Demo User",
            organization="DocFlow Community",
            team_name="Engineering",
            job_title="Product Explorer",
            tenant_id=settings.dev_tenant_id,
            email_verified=True,
        )
    # The settings-backed token resolves to an organization admin in auth.py.
    return LoginResponse(access_token=create_token(subject=settings.auth_username, user_id=settings.dev_user_id))


@app.post("/auth/demo", response_model=LoginResponse, tags=["Identity"])
def demo_login():
    """Backward-compatible alias for local super-admin access."""
    return super_admin_login()


@app.get("/auth/google/start", tags=["Identity"])
def google_start(request: Request, response: Response):
    if not google_is_configured():
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")
    state = create_oauth_state()
    response.set_cookie("google_oauth_state", state, **oauth_state_cookie_kwargs(request))
    response.status_code = 307
    response.headers["location"] = google_authorization_url(state)
    return response


@app.get("/auth/google/callback", tags=["Identity"])
def google_callback(code: str, state: str, request: Request, response: Response):
    expected_state = request.cookies.get("google_oauth_state")
    if not expected_state or not validate_oauth_state(state) or not hmac.compare_digest(state, expected_state):
        raise HTTPException(status_code=400, detail="Invalid Google sign-in state")
    try:
        identity = exchange_google_code(code)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    subject = identity["subject"]
    user = get_user_by_provider_subject("google", subject)
    if user is None:
        user = get_user_by_email(identity["email"])
        if user:
            link_user_provider(user["user_id"], "google", subject)
        else:
            raise HTTPException(
                status_code=403,
                detail="Google sign-in is available only for existing members. Ask an organization admin for an invitation.",
            )
    response.delete_cookie("google_oauth_state")
    response.status_code = 303
    token = create_token(subject=user["username"], user_id=user["user_id"])
    response.headers["location"] = f"{settings.frontend_url.rstrip('/')}/?auth_token={token}"
    return response


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    project_id: str | None = None

    @field_validator("query")
    @classmethod
    def query_must_contain_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must contain text")
        return value.strip()


class ChatSessionRequest(BaseModel):
    project_id: str | None = None
    mode: str = Field(default="query", pattern="^(query|rag)$")
    title: str = Field(default="New conversation", min_length=1, max_length=120)


class ChatMessageRequest(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=100_000)
    sources: list[dict] = Field(default_factory=list)


class SearchResult(BaseModel):
    document_id: str
    section_title: str
    chunk_text: str
    score: float


def _canonical_source_name(value: str) -> str:
    return re.sub(r"(?:\s+\(\d+\))+(?=\.[^.]+$)", "", value).strip()


def _unique_source_hits(hits: list[dict]) -> list[dict]:
    """Show one best matching source file instead of repeating chunk hits."""
    unique: dict[str, dict] = {}
    for hit in hits:
        payload = hit.get("payload", {})
        source_name = _canonical_source_name(str(
            payload.get("section_title")
            or payload.get("document_id")
            or "unknown source"
        )).lower()
        if source_name not in unique or hit.get("score", 0) > unique[source_name].get("score", 0):
            unique[source_name] = hit
    return list(unique.values())


class UploadResponse(BaseModel):
    document_id: str
    filename: str
    stored_path: str
    chunk_count: int
    version_id: str | None = None
    version_number: int | None = None


class DocumentVersionSummary(BaseModel):
    version_id: str
    document_id: str
    version_number: int
    filename: str
    stored_path: str
    file_size_bytes: int
    uploaded_by: str | None = None
    status: str
    created_at: str


class ProjectSummary(BaseModel):
    project_id: str
    project_name: str
    description: str | None = None
    document_count: int
    members: list[dict] = []
    assigned_teams: list[str] = []
    created_at: str | None = None
    updated_at: str | None = None


class CreateProjectRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    project_name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)


class NoteRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=20000)
    project_id: str | None = None


class PersonalDocumentSummary(BaseModel):
    document_id: str
    tenant_id: str
    owner_id: str
    filename: str
    stored_path: str
    file_size_bytes: int
    created_at: str


@app.get("/me", response_model=AccessContext)
def current_access_context(user: UserContext = Depends(get_current_user)):
    user_db = get_user_by_id(user.user_id) if user.user_id else None
    return AccessContext(
        user_id=user.user_id,
        username=user_db.get("username") if user_db else (settings.auth_username if user.is_org_admin else user.user_id),
        full_name=user_db.get("full_name") if user_db else ("Super Admin" if user.is_org_admin else None),
        organization=user_db.get("organization") if user_db else "DocFlow Enterprise",
        team_name=user_db.get("team_name") if user_db else ("Administration" if user.is_org_admin else None),
        job_title=user_db.get("job_title") if user_db else ("Platform Administrator" if user.is_org_admin else None),
        tenant_id=user.tenant_id,
        is_org_admin=user.is_org_admin,
        role=user.role,
        sensitivity_clearance=user.sensitivity_clearance,
        accessible_projects=sorted(user.project_roles),
        project_roles=user.project_roles,
        must_change_password=user.must_change_password,
    )


class DocumentSummary(BaseModel):
    document_id: str
    project_id: str
    filename: str
    stage: str
    doc_type: str
    sensitivity_level: int
    chunk_count: int
    created_at: str
    uploaded_by: str | None = None
    workflow_state: str | None = None


class DocumentDetailSummary(BaseModel):
    document_id: str
    project_id: str
    project_name: str | None = None
    filename: str
    stored_path: str | None = None
    stage: str
    doc_type: str
    sensitivity_level: int
    chunk_count: int
    created_at: str
    uploaded_by: str | None = None
    workflow_state: str | None = None


@app.get("/documents/{document_id}/versions", response_model=list[DocumentVersionSummary])
def get_document_versions(
    document_id: str,
    user: UserContext = Depends(get_current_user),
):
    """List preserved versions for an authorized document."""
    document = get_document(user.tenant_id, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        require_document_action(user, document, "view")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return list_document_versions(user.tenant_id, document_id)


@app.post("/documents/{document_id}/versions", response_model=DocumentVersionSummary, status_code=201)
async def upload_document_version(
    document_id: str,
    file: UploadFile = File(...),
    user: UserContext = Depends(get_current_user),
):
    """Upload an explicit new version while keeping the document identity stable."""
    document = get_document(user.tenant_id, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        require_document_action(user, document, "upload")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="The uploaded file is empty")
    if len(contents) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="Files must be 10 MB or smaller")

    safe_filename = Path(file.filename or document["filename"]).name
    try:
        text = extract_text(contents, safe_filename)
    except (UnicodeDecodeError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    if not text:
        raise HTTPException(status_code=400, detail="No readable text found in the uploaded file")

    document_dir = UPLOADS_DIR / user.tenant_id / document["project_id"]
    document_dir.mkdir(parents=True, exist_ok=True)
    stored_file = document_dir / f"{document_id}_v{len(list_document_versions(user.tenant_id, document_id)) + 1}_{safe_filename}"
    stored_file.write_bytes(contents)
    chunks = chunk_document(
        sections=[ParsedSection(title=safe_filename, text=text)],
        document_id=document_id,
        project_id=document["project_id"],
        project_name=document.get("project_name") or document["project_id"],
        stage=document["stage"],
        doc_type=document["doc_type"],
        visible_to_teams=json.loads(document.get("visible_to_teams") or "[]"),
        sensitivity_level=document["sensitivity_level"],
    )
    try:
        index_chunks(get_qdrant_client(), user.tenant_id, chunks)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    version = create_document_version(
        tenant_id=user.tenant_id,
        document_id=document_id,
        filename=safe_filename,
        stored_path=str(stored_file),
        file_size_bytes=len(contents),
        uploaded_by=user.user_id,
    )
    audit_log(user.tenant_id, user.user_id, "UPLOAD_VERSION", "document", document_id, f"version={version['version_number']}")
    return version


@app.get("/projects", response_model=list[ProjectSummary])
def list_projects(
    user: UserContext = Depends(get_current_user),
    client: QdrantClient = Depends(get_qdrant_client),
):
    """List project records visible to the current user."""
    _backfill_database_from_vectors(client, user.tenant_id)
    projects = database_list_projects(user.tenant_id)
    for project in projects:
        assigned_teams = sorted(set(project.get("assigned_teams", [])) | {
            member["team_name"].strip()
            for member in project.get("members", [])
            if member.get("team_name") and member["team_name"].strip()
        })
        project_role = role_for_project(user, project["project_id"])
        if not user.is_org_admin and project_role != ADMIN:
            visible_teams = set(user.team_memberships.get(project["project_id"], []))
            assigned_teams = [team for team in assigned_teams if team in visible_teams]
        project["assigned_teams"] = assigned_teams
    if user.is_org_admin:
        return projects
    return [project for project in projects if project["project_id"] in user.project_roles]




@app.post("/projects", response_model=ProjectSummary, status_code=201)
def create_project_endpoint(
    request: CreateProjectRequest,
    user: UserContext = Depends(get_current_user),
):
    """Create a project for an authorized organization/project role."""
    if not can_create_project(user):
        raise HTTPException(status_code=403, detail="Members cannot create projects")
    if get_project(user.tenant_id, request.project_id):
        raise HTTPException(status_code=409, detail="Project ID already exists")
    project_name = request.project_name.strip()
    if not project_name:
        raise HTTPException(status_code=422, detail="Project name cannot be empty")
    try:
        project = create_project(
            user.tenant_id,
            request.project_id,
            project_name,
            user.user_id,
            description=(request.description or "").strip() or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"Project could not be created: {exc}") from exc
    audit_log(user.tenant_id, user.user_id, "CREATE_PROJECT", "project", request.project_id)
    owner = get_user_by_id(user.user_id) or {}
    project["assigned_teams"] = [owner["team_name"].strip()] if owner.get("team_name") else []
    return project


def _backfill_database_from_vectors(client: QdrantClient, tenant_id: str) -> None:
    """Register pre-database Qdrant documents so existing data remains visible."""
    collection = f"tenant_{tenant_id}"
    if not client.collection_exists(collection):
        return
    points, _ = client.scroll(collection_name=collection, limit=10000, with_payload=True)
    documents: dict[str, dict] = {}
    for point in points:
        payload = point.payload or {}
        document_id = payload.get("document_id")
        if document_id and document_id not in documents:
            documents[document_id] = {
                "project_id": payload.get("project_id", "unknown"),
                "stage": payload.get("stage", "Unknown"),
                "doc_type": payload.get("doc_type", "Unknown"),
                "filename": payload.get("section_title", document_id),
                "sensitivity_level": payload.get("sensitivity_level", 1),
                "chunk_count": 0,
            }
        if document_id:
            documents[document_id]["chunk_count"] += 1
    for document_id, document in documents.items():
        record_document(
            tenant_id=tenant_id,
            project_name=document["project_id"],
            document_id=document_id,
            stored_path="",
            **document,
        )


@app.get("/projects/{project_id}/activity", tags=["Governance"])
def project_activity(
    project_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Return the project overview and actor/timestamp/stage activity for org admins."""
    if not user.is_org_admin:
        raise HTTPException(status_code=403, detail="Only Super Admins can view project activity")
    project = get_project(user.tenant_id, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    documents = database_list_documents(user.tenant_id, project_id)
    return {
        "project": project,
        "documents": documents,
        "activity": get_project_activity(user.tenant_id, project_id),
    }


@app.get("/projects/{project_id}/documents", response_model=list[DocumentSummary])
def list_project_documents(
    project_id: str,
    user: UserContext = Depends(get_current_user),
):
    """List document records belonging to one authorized project."""
    documents = database_list_documents(user.tenant_id, project_id)
    if user.project_roles.get(project_id) is None and not user.is_org_admin:
        documents = [document for document in documents if document.get("uploaded_by") == user.user_id]
        if not documents:
            raise HTTPException(status_code=403, detail="You do not have access to this project")
    return [
        document for document in documents
        if can_view_document(
            user,
            project_id=project_id,
            sensitivity_level=document["sensitivity_level"],
            visible_to_teams=json.loads(document.get("visible_to_teams") or "[]"),
            workflow_state=document.get("workflow_state", "draft"),
            uploaded_by=document.get("uploaded_by"),
        )
    ]


@app.get("/documents", response_model=list[DocumentDetailSummary])
def list_all_documents(
    user: UserContext = Depends(get_current_user),
    client: QdrantClient = Depends(get_qdrant_client),
):
    """List all document records across all projects in the database."""
    _backfill_database_from_vectors(client, user.tenant_id)
    return [
        document for document in database_list_all_documents(user.tenant_id)
        if can_view_document(
            user,
            project_id=document["project_id"],
            sensitivity_level=document["sensitivity_level"],
            visible_to_teams=json.loads(document.get("visible_to_teams") or "[]"),
            workflow_state=document.get("workflow_state", "draft"),
            uploaded_by=document.get("uploaded_by"),
        )
    ]


@app.get("/documents/{document_id}/open", include_in_schema=False)
def open_document(
    document_id: str,
    token: str | None = None,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    client: QdrantClient = Depends(get_qdrant_client),
):
    """Stream an authorized uploaded document for browser viewing.

    Accepts the token either as a normal Bearer header or as a `?token=`
    query param, since this URL is meant to be openable directly in a new
    browser tab (an <a href> can't attach an Authorization header).
    """
    raw_token = (credentials.credentials if credentials else None) or token
    if not raw_token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        user = user_from_token(raw_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    document = get_document(user.tenant_id, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if not can_view_document(
        user,
        project_id=document["project_id"],
        sensitivity_level=document["sensitivity_level"],
        visible_to_teams=json.loads(document.get("visible_to_teams") or "[]"),
        workflow_state=document.get("workflow_state", "draft"),
        uploaded_by=document.get("uploaded_by"),
    ):
        raise HTTPException(status_code=403, detail="You do not have access to this document")
    stored_path = document.get("stored_path")
    project_root = Path(__file__).resolve().parents[2]
    upload_root = (project_root / settings.uploads_path).resolve()
    if stored_path:
        file_path = Path(stored_path)
        if not file_path.is_absolute():
            file_path = project_root / file_path
        file_path = file_path.resolve()
        if upload_root in file_path.parents and file_path.is_file():
            return FileResponse(
                file_path,
                media_type="application/octet-stream",
                headers={"Content-Disposition": f'inline; filename="{Path(document["filename"]).name}"'},
            )

    # Older vector-backfilled records have no local file; expose their indexed text.
    collection = f"tenant_{user.tenant_id}"
    if client.collection_exists(collection):
        points, _ = client.scroll(
            collection_name=collection,
            scroll_filter=models.Filter(must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))]),
            limit=1000,
            with_payload=True,
        )
        chunks = sorted(
            (point.payload or {} for point in points),
            key=lambda payload: payload.get("chunk_index", 0),
        )
        text = "\n\n".join(payload.get("chunk_text", "") for payload in chunks if payload.get("chunk_text"))
        if text:
            return PlainTextResponse(
                text,
                headers={"Content-Disposition": f'inline; filename="{Path(document["filename"]).stem}.txt"'},
            )
    raise HTTPException(status_code=404, detail="Original file or indexed text is not available")


@app.delete("/documents/{document_id}")
def delete_document_endpoint(
    document_id: str,
    user: UserContext = Depends(get_current_user),
    client: QdrantClient = Depends(get_qdrant_client),
):
    """Delete a project document and its indexed chunks; organization admins only."""
    if not user.is_org_admin:
        raise HTTPException(status_code=403, detail="Only organization admins can delete documents")
    document = get_document(user.tenant_id, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    stored_path = document.get("stored_path")
    if stored_path:
        path = Path(stored_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[2] / path
        if path.is_file():
            path.unlink()
    collection = f"tenant_{user.tenant_id}"
    if client.collection_exists(collection):
        client.delete(
            collection_name=collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(must=[
                    models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))
                ])
            ),
        )
    delete_document(user.tenant_id, document_id)
    audit_log(user.tenant_id, user.user_id, "DELETE", "document", document_id, f"filename={document['filename']}")
    return {"status": "deleted", "document_id": document_id}


@app.get("/database/documents/{document_id}")
def get_sqlite_document_record(document_id: str, user: UserContext = Depends(get_current_user)):
    """Return the authorized document metadata stored in SQLite."""
    document = get_document(user.tenant_id, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if not can_view_document(
        user,
        project_id=document["project_id"],
        sensitivity_level=document["sensitivity_level"],
        visible_to_teams=json.loads(document.get("visible_to_teams") or "[]"),
        workflow_state=document.get("workflow_state", "draft"),
        uploaded_by=document.get("uploaded_by"),
    ):
        raise HTTPException(status_code=403, detail="You do not have access to this document")
    document["visible_to_teams"] = json.loads(document.get("visible_to_teams") or "[]")
    return document


class BatchUploadResponse(BaseModel):
    project_id: str
    documents: list[UploadResponse]
    total_chunks: int


class AskRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    project_id: str | None = None

    @field_validator("query")
    @classmethod
    def query_must_contain_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must contain text")
        return value.strip()


class AskResponse(BaseModel):
    answer: str
    sources: list[SearchResult]


@app.get("/notes")
def get_personal_notes(user: UserContext = Depends(get_current_user)):
    """List only notes owned by the current user in this tenant."""
    return list_notes(user.tenant_id, user.user_id)


@app.post("/notes", status_code=201)
def create_personal_note(request: NoteRequest, user: UserContext = Depends(get_current_user)):
    """Create a personal todo or meeting note, optionally attached to a project."""
    if request.project_id:
        try:
            require_action(user, request.project_id, "view")
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    note = create_note(user.tenant_id, user.user_id, request.title.strip(), request.content.strip(), request.project_id)
    audit_log(user.tenant_id, user.user_id, "CREATE_NOTE", "note", note["note_id"], f"project={request.project_id or 'personal'}")
    return note


@app.get("/notes/documents", response_model=list[PersonalDocumentSummary], tags=["Notes"])
def get_personal_documents(user: UserContext = Depends(get_current_user)):
    """List private documents owned by the current user."""
    return list_personal_documents(user.tenant_id, user.user_id)


@app.post("/notes/documents", response_model=PersonalDocumentSummary, status_code=201, tags=["Notes"])
async def upload_personal_document(
    file: UploadFile = File(...),
    user: UserContext = Depends(get_current_user),
):
    """Store a private Notes document without adding it to a project index."""
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="The uploaded file is empty")
    if len(contents) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="Files must be 10 MB or smaller")
    safe_filename = Path(file.filename or "personal-document").name
    try:
        extract_text(contents, safe_filename)
    except (UnicodeDecodeError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    document_id = str(uuid4())
    personal_dir = UPLOADS_DIR / user.tenant_id / "personal"
    personal_dir.mkdir(parents=True, exist_ok=True)
    stored_file = personal_dir / f"{document_id}_{safe_filename}"
    stored_file.write_bytes(contents)
    document = create_personal_document(
        tenant_id=user.tenant_id,
        owner_id=user.user_id,
        filename=safe_filename,
        stored_path=str(stored_file),
        file_size_bytes=len(contents),
    )
    audit_log(user.tenant_id, user.user_id, "UPLOAD_PERSONAL_DOCUMENT", "personal_document", document_id)
    return document


@app.post("/upload", response_model=UploadResponse, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    project_id: str = Form(...),
    project_name: str = Form(...),
    stage: str = Form(...),
    doc_type: str = Form(...),
    visible_to_teams: str = Form(""),
    sensitivity_level: int = Form(1),
    user: UserContext = Depends(get_current_user),
):
    """Store one document and index its access-tagged chunks.
    
    Requires: user must have reviewer or admin role in the project.
    """
    try:
        require_action(user, project_id, "upload")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    
    return await _ingest_document(
        file, project_id, project_name, stage, doc_type, visible_to_teams, sensitivity_level, user
    )


@app.post("/upload/batch", response_model=BatchUploadResponse, status_code=201)
async def upload_documents(
    files: list[UploadFile] = File(...),
    project_id: str = Form(...),
    project_name: str = Form(...),
    stage: str = Form(...),
    doc_type: str = Form(...),
    visible_to_teams: str = Form(""),
    sensitivity_level: int = Form(1),
    user: UserContext = Depends(get_current_user),
):
    """Upload and index multiple documents into one project knowledge space."""
    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")
    if len(files) > 100:
        raise HTTPException(status_code=413, detail="Upload at most 100 files at once")

    try:
        require_action(user, project_id, "upload")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    documents = []
    for file in files:
        documents.append(
            await _ingest_document(
                file, project_id, project_name, stage, doc_type, visible_to_teams, sensitivity_level, user
            )
        )
    return BatchUploadResponse(
        project_id=project_id,
        documents=documents,
        total_chunks=sum(document.chunk_count for document in documents),
    )


async def _ingest_document(
    file: UploadFile,
    project_id: str,
    project_name: str,
    stage: str,
    doc_type: str,
    visible_to_teams: str,
    sensitivity_level: int,
    user: UserContext,
) -> UploadResponse:
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="The uploaded file is empty")
    if len(contents) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="Files must be 10 MB or smaller")

    try:
        text = extract_text(contents, file.filename or "")
    except (UnicodeDecodeError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    if not text:
        raise HTTPException(status_code=400, detail="No readable text found in the uploaded file")

    # Smart duplicate detection: check if this file already exists
    safe_filename = Path(file.filename or "document").name
    existing_docs = database_list_documents(user.tenant_id, project_id)
    for existing_doc in existing_docs:
        if existing_doc.get("filename") == safe_filename:
            # Same filename found - could be updated version
            audit_log(user.tenant_id, user.user_id, "DUPLICATE_DETECTED", "document", existing_doc.get("document_id"), f"new_upload_for={safe_filename}")
            # Flag for user review but continue (they can keep both or replace)

    document_id = str(uuid4())
    document_dir = UPLOADS_DIR / user.tenant_id
    document_dir.mkdir(parents=True, exist_ok=True)
    stored_file = document_dir / f"{document_id}_{safe_filename}"
    stored_file.write_bytes(contents)

    teams = [team.strip() for team in visible_to_teams.split(",") if team.strip()]
    chunks = chunk_document(
        sections=[ParsedSection(title=safe_filename, text=text)],
        document_id=document_id,
        project_id=project_id,
        project_name=project_name,
        stage=stage,
        doc_type=doc_type,
        visible_to_teams=teams,
        sensitivity_level=sensitivity_level,
    )
    try:
        index_chunks(get_qdrant_client(), user.tenant_id, chunks)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    record_document(
        tenant_id=user.tenant_id,
        project_id=project_id,
        project_name=project_name,
        document_id=document_id,
        filename=safe_filename,
        stored_path=str(stored_file),
        stage=stage,
        doc_type=doc_type,
        sensitivity_level=sensitivity_level,
        chunk_count=len(chunks),
        visible_to_teams=teams,
        uploaded_by=user.user_id,
    )
    version = create_document_version(
        tenant_id=user.tenant_id,
        document_id=document_id,
        filename=safe_filename,
        stored_path=str(stored_file),
        file_size_bytes=len(contents),
        uploaded_by=user.user_id,
    )
    
    # Log the upload action
    audit_log(user.tenant_id, user.user_id, "UPLOAD", "document", document_id, f"stage={stage};doc_type={doc_type};sensitivity={sensitivity_level}")

    return UploadResponse(
        document_id=document_id,
        filename=safe_filename,
        stored_path=str(stored_file),
        chunk_count=len(chunks),
        version_id=version["version_id"],
        version_number=version["version_number"],
    )


# ============= APPROVAL WORKFLOW & ADMIN ENDPOINTS =============

class ApprovalRequest(BaseModel):
    approval_reason: str | None = None


class RejectionRequest(BaseModel):
    rejection_reason: str = Field(min_length=5, max_length=500)


class SetRoleRequest(BaseModel):
    role: str = Field(pattern="^(member|reviewer|admin|team_lead)$")


class AuditLogEntry(BaseModel):
    log_id: str
    user_id: str
    action: str
    resource_type: str
    resource_id: str | None
    details: str | None
    timestamp: str


class PendingApprovals(BaseModel):
    document_id: str
    filename: str
    stage: str
    doc_type: str
    created_at: str
    created_by: str


class AccessAssignmentRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    project_id: str | None = None
    role: str | None = Field(default=None, pattern="^(member|reviewer|admin)$")
    team_name: str | None = Field(default=None, min_length=1, max_length=120)
    sensitivity_clearance: int | None = Field(default=None, ge=0, le=3)
    organization_admin: bool | None = None


class ProjectAccessRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    project_id: str = Field(min_length=1, max_length=80)
    role: str = Field(pattern="^(member|reviewer|admin|team_lead)$")
    team_name: str | None = Field(default=None, min_length=1, max_length=120)


class CreateOrganizationUserRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    full_name: str = Field(min_length=2, max_length=120)
    team_name: str | None = Field(default=None, max_length=120)
    job_title: str | None = Field(default=None, max_length=100)


@app.get("/admin/users")
def list_admin_users(user: UserContext = Depends(get_current_user)):
    """List users visible to the caller's project administration scope."""
    if user.is_org_admin:
        return list_users(user.tenant_id)
    managed_projects = [
        project_id for project_id, role in user.project_roles.items()
        if role in {ADMIN, TEAM_LEAD}
    ]
    if not managed_projects:
        raise HTTPException(status_code=403, detail="You do not have project-management access")
    return [
        member for member in list_users(user.tenant_id)
        if member["user_id"] != user.user_id
    ]


@app.post("/admin/users", status_code=201)
def create_organization_user(
    request: CreateOrganizationUserRequest,
    admin_user: UserContext = Depends(get_current_user),
):
    """Create a member account with a one-time temporary password."""
    if not admin_user.is_org_admin:
        raise HTTPException(status_code=403, detail="Only organization admins can add users")
    email = request.email.strip().lower()
    if email == settings.auth_username.lower() or get_user_by_email(email):
        raise HTTPException(status_code=409, detail="An account with that email already exists")
    admin_record = get_user_by_id(admin_user.user_id)
    organization_name = (admin_record or {}).get("organization") or "your organization"
    temporary_password = "".join(
        secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%")
        for _ in range(14)
    )
    salt = settings.auth_secret.encode()[:16].ljust(16, b"0")
    create_user(
        username=email,
        password_hash=_hash_password(temporary_password, salt),
        provider="password",
        provider_subject=None,
        tenant_id=admin_user.tenant_id,
        full_name=request.full_name.strip(),
        organization=organization_name,
        team_name=request.team_name.strip() if request.team_name else None,
        must_change_password=True,
        email_verified=True,
    )
    audit_log(admin_user.tenant_id, admin_user.user_id, "CREATE_USER", "user", email, f"email={email}")
    from app.services.email_notifications import send_notification_email
    send_notification_email(
        email,
        f"Welcome to {organization_name} on DocFlow",
        f"Hello {request.full_name.strip()},\n\n"
        f"Welcome to {organization_name}. Your DocFlow account has been created by your organization administrator.\n\n"
        f"Sign in at: {settings.frontend_url.rstrip('/')}/login\n"
        f"Email: {email}\n"
        f"Temporary password: {temporary_password}\n\n"
        "For your security, you will be asked to change this temporary password after your first sign-in.\n"
        "Welcome to the team!",
    )
    return {"status": "created", "email": email, "full_name": request.full_name.strip()}


@app.delete("/admin/users/{user_id}")
def remove_organization_user(
    user_id: str,
    admin_user: UserContext = Depends(get_current_user),
):
    """Remove a user from the organization, including project memberships."""
    if not admin_user.is_org_admin:
        raise HTTPException(status_code=403, detail="Only organization admins can remove users")
    if user_id == admin_user.user_id:
        raise HTTPException(status_code=409, detail="You cannot remove your own account")
    target = get_user_by_id(user_id)
    if not target or target["tenant_id"] != admin_user.tenant_id:
        raise HTTPException(status_code=404, detail="User was not found in your organization")
    if target.get("is_org_admin"):
        raise HTTPException(status_code=409, detail="Remove organization-admin status before removing this user")
    audit_log(admin_user.tenant_id, admin_user.user_id, "REMOVE_USER", "user", user_id, f"email={target['username']}")
    delete_user(user_id, admin_user.tenant_id)
    return {"status": "removed", "user_id": user_id}


@app.delete("/admin/access/{user_id}")
def revoke_organization_admin(
    user_id: str,
    admin_user: UserContext = Depends(get_current_user),
):
    """Revoke organization-admin status without removing the user."""
    if not admin_user.is_org_admin:
        raise HTTPException(status_code=403, detail="Only organization admins can change organization roles")
    if user_id == admin_user.user_id:
        raise HTTPException(status_code=409, detail="You cannot remove your own admin access")
    target = get_user_by_id(user_id)
    if not target or target["tenant_id"] != admin_user.tenant_id:
        raise HTTPException(status_code=404, detail="User was not found in your organization")
    update_user_access(user_id, is_org_admin=False)
    audit_log(admin_user.tenant_id, admin_user.user_id, "REMOVE_ACCESS", "user", user_id, f"email={target['username']};role=admin")
    return {"status": "updated", "user_id": user_id}


@app.post("/admin/access")
def assign_user_access(
    request: AccessAssignmentRequest,
    admin_user: UserContext = Depends(get_current_user),
):
    """Grant the Admin role to an existing tenant user by email."""
    if not admin_user.is_org_admin:
        raise HTTPException(status_code=403, detail="Only the super admin can assign admin access")
    if request.role != "admin" or request.project_id or request.team_name is not None or request.sensitivity_clearance is not None:
        raise HTTPException(status_code=422, detail="The super admin access screen can grant only the global Admin role")
    target = get_user_by_username(request.email.strip().lower())
    if not target or target["tenant_id"] != admin_user.tenant_id:
        raise HTTPException(status_code=404, detail="User was not found in your organization")
    if target["user_id"] == admin_user.user_id:
        raise HTTPException(status_code=409, detail="Organization admins already have full access")
    if target.get("is_org_admin") and not admin_user.is_org_admin:
        raise HTTPException(status_code=403, detail="Only organization admins can modify organization-admin access")
    update_user_access(
        target["user_id"],
        is_org_admin=True,
    )
    details = f"email={target['username']};role=admin"
    audit_log(admin_user.tenant_id, admin_user.user_id, "ASSIGN_ACCESS", "user", target["user_id"], details)
    return {"status": "updated", "user_id": target["user_id"], "email": target["username"]}


@app.post("/admin/project-access")
def assign_project_access(
    request: ProjectAccessRequest,
    admin_user: UserContext = Depends(get_current_user),
):
    """Assign project access according to organization/project/team RBAC."""
    target = get_user_by_username(request.email.strip().lower())
    if not target or target["tenant_id"] != admin_user.tenant_id:
        raise HTTPException(status_code=404, detail="User was not found in your organization")
    if target["user_id"] == admin_user.user_id:
        raise HTTPException(status_code=409, detail="Organization admins already have full access")
    if not get_project(admin_user.tenant_id, request.project_id):
        raise HTTPException(status_code=404, detail="Project was not found in your organization")
    caller_role = "org_admin" if admin_user.is_org_admin else admin_user.project_roles.get(request.project_id)
    caller_profile = get_user_by_id(admin_user.user_id) or {}
    if caller_role not in {"org_admin", ADMIN, TEAM_LEAD}:
        raise HTTPException(status_code=403, detail="You cannot assign access for this project")
    if request.role == ADMIN and request.team_name is not None:
        raise HTTPException(status_code=422, detail="Project Admin assignments cannot include a team")
    if caller_role == ADMIN and request.role == ADMIN:
        raise HTTPException(status_code=403, detail="Project admins can assign only team lead or member roles")
    if caller_role in {"org_admin", ADMIN} and request.role == TEAM_LEAD and not request.team_name:
        raise HTTPException(status_code=422, detail="A team is required when assigning a team lead")
    if caller_role == TEAM_LEAD:
        caller_team = caller_profile.get("team_name")
        if request.role != "member" or not caller_team or request.team_name != caller_team:
            raise HTTPException(status_code=403, detail="Team leads can add only members to their own team")
        if target.get("team_name") not in {None, caller_team}:
            raise HTTPException(status_code=403, detail="Team leads can manage only their own team")
    update_user_access(
        target["user_id"],
        project_id=request.project_id,
        role=request.role,
        team_name=request.team_name.strip() if request.team_name else None,
    )
    audit_log(
        admin_user.tenant_id,
        admin_user.user_id,
        "ASSIGN_PROJECT_ACCESS",
        "user",
        target["user_id"],
        f"email={target['username']};project={request.project_id};role={request.role}"
        f";team={request.team_name or target.get('team_name') or 'Not specified'}",
    )
    return {
        "status": "updated",
        "email": target["username"],
        "project_id": request.project_id,
        "role": request.role,
        "team_name": request.team_name,
    }


@app.delete("/admin/project-access/{user_id}/{project_id}")
def remove_project_access(
    user_id: str,
    project_id: str,
    admin_user: UserContext = Depends(get_current_user),
):
    """Remove a user's project membership (super admin only)."""
    if not admin_user.is_org_admin:
        raise HTTPException(status_code=403, detail="Only organization admins can remove project access")
    target = get_user_by_id(user_id)
    project = get_project(admin_user.tenant_id, project_id)
    if not target or target["tenant_id"] != admin_user.tenant_id or not project:
        raise HTTPException(status_code=404, detail="User or project not found in your organization")
    if not remove_user_role(user_id, project_id):
        raise HTTPException(status_code=404, detail="User is not assigned to this project")
    audit_log(
        admin_user.tenant_id,
        admin_user.user_id,
        "REMOVE_PROJECT_ACCESS",
        "user",
        user_id,
        f"email={target['username']};project={project_id}",
    )
    from app.database import _send_access_change_email
    _send_access_change_email(
        tenant_id=admin_user.tenant_id,
        target_user_id=user_id,
        action="REMOVE_PROJECT_ACCESS",
        details=f"email={target['username']};project={project_id}",
    )
    return {"status": "removed", "user_id": user_id, "project_id": project_id}


@app.post("/documents/{document_id}/submit")
def submit_document_endpoint(
    document_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Submit a draft for review."""
    document = get_document(user.tenant_id, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        require_document_action(user, document, "submit")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if document.get("workflow_state") not in ("draft", "rejected"):
        raise HTTPException(status_code=409, detail="Only drafts can be submitted")
    submit_document(document_id)
    update_document_workflow_state(get_qdrant_client(), user.tenant_id, document_id, "pending_review")
    audit_log(user.tenant_id, user.user_id, "SUBMIT", "document", document_id)
    return {"status": "pending_review", "document_id": document_id}


@app.post("/documents/{document_id}/approve")
def approve_document_endpoint(
    document_id: str,
    request: ApprovalRequest,
    user: UserContext = Depends(get_current_user),
):
    """Approve a pending document (reviewer/admin only)."""
    document = get_document(user.tenant_id, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        require_document_action(user, document, "approve")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    try:
        approve_document(document_id, user.user_id)
        update_document_workflow_state(get_qdrant_client(), user.tenant_id, document_id, "approved")
        audit_log(user.tenant_id, user.user_id, "APPROVE", "document", document_id, request.approval_reason)
        return {"status": "approved", "document_id": document_id}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/documents/{document_id}/reject")
def reject_document_endpoint(
    document_id: str,
    request: RejectionRequest,
    user: UserContext = Depends(get_current_user),
):
    """Reject a document (reviewer/admin only)."""
    document = get_document(user.tenant_id, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        require_document_action(user, document, "reject")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    try:
        reject_document(document_id, request.rejection_reason)
        update_document_workflow_state(get_qdrant_client(), user.tenant_id, document_id, "rejected")
        audit_log(user.tenant_id, user.user_id, "REJECT", "document", document_id, request.rejection_reason)
        return {"status": "rejected", "document_id": document_id}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/projects/{project_id}/pending-approvals", response_model=list[DocumentSummary])
def get_pending_approvals(
    project_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Get documents awaiting approval in a project (admin only)."""
    try:
        require_action(user, project_id, "pending")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    documents = database_list_documents(user.tenant_id, project_id)
    pending = [
        d for d in documents
        if d.get("workflow_state") == "pending_review"
        and can_view_document(
            user,
            project_id=project_id,
            sensitivity_level=d["sensitivity_level"],
            workflow_state=d["workflow_state"],
        )
    ]
    return pending


@app.post("/admin/users/{user_id}/role")
def set_user_project_role(
    user_id: str,
    project_id: str,
    request: SetRoleRequest,
    admin_user: UserContext = Depends(get_current_user),
):
    """Assign a user's role in a project according to project RBAC."""
    target = get_user_by_id(user_id)
    project = get_project(admin_user.tenant_id, project_id)
    if not target or target["tenant_id"] != admin_user.tenant_id or not project:
        raise HTTPException(status_code=404, detail="User or project not found in your organization")
    caller_role = "org_admin" if admin_user.is_org_admin else admin_user.project_roles.get(project_id)
    if caller_role not in {"org_admin", ADMIN, TEAM_LEAD}:
        raise HTTPException(status_code=403, detail="You cannot manage roles in this project")
    if caller_role == ADMIN and request.role == ADMIN:
        raise HTTPException(status_code=403, detail="Project admins can assign only team lead or member roles")
    if caller_role == TEAM_LEAD:
        caller = get_user_by_id(admin_user.user_id) or {}
        if request.role != "member":
            raise HTTPException(status_code=403, detail="Team leads can assign only the member role")
        if get_user_role(user_id, project_id) is None or caller.get("team_name") != target.get("team_name"):
            raise HTTPException(status_code=403, detail="Team leads can manage only existing members of their own team")
    try:
        set_user_role(user_id, project_id, request.role)
        audit_log(
            admin_user.tenant_id,
            admin_user.user_id,
            "SET_ROLE",
            "user",
            user_id,
            f"email={target['username']};role={request.role};project={project_id};team={target.get('team_name') or 'Not specified'}",
        )
        return {"status": "updated", "user_id": user_id, "project_id": project_id, "role": request.role}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/admin/audit-log", response_model=list[AuditLogEntry])
def get_audit_log_endpoint(
    limit: int = 100,
    user: UserContext = Depends(get_current_user),
):
    """Retrieve audit log for the tenant (admin only)."""
    if not user.is_org_admin:
        raise HTTPException(status_code=403, detail="Only organization admins can view audit logs")
    try:
        entries = get_audit_log(user.tenant_id, limit=min(limit, 1000))
        return entries
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/notifications")
def get_notifications(
    since: str | None = None,
    user: UserContext = Depends(get_current_user),
):
    """Return recent relevant activity for the notification badge."""
    events = get_user_notifications(user.tenant_id, user.user_id, user.is_org_admin)
    unread_count = len(events)
    if since:
        unread_count = sum(1 for event in events if event["timestamp"] > since)
    return {
        "notifications": events,
        "unread_count": unread_count,
    }


class AbacSimulateRequest(BaseModel):
    project_id: str
    sensitivity_level: int = 1
    visible_to_teams: list[str] = Field(default_factory=list)
    stage: str | None = None
    doc_type: str | None = None
    workflow_state: str = "approved"
    uploaded_by: str | None = None


@app.get("/rbac/matrix")
def get_rbac_matrix(user: UserContext = Depends(get_current_user)):
    """Return the complete RBAC role-action hierarchy and current user's role mapping."""
    roles_def = [
        {
            "role": "super_admin",
            "name": "Super Admin / Org Admin",
            "scope": "Tenant-Wide (All Projects)",
            "description": "Full administrative control over tenant settings, user directory, project assignments, and cross-project governance.",
            "team_scope": "All teams",
            "actions": ["view", "upload", "draft", "submit", "approve", "reject", "pending", "manage_roles", "audit"],
            "badge_color": "gold",
        },
        {
            "role": "admin",
            "name": "Project Admin",
            "scope": "Assigned Project(s)",
            "description": "Administrative authority within assigned projects, including managing project members and reviewing workflows.",
            "team_scope": "All teams in assigned projects",
            "actions": ["view", "upload", "draft", "submit", "approve", "reject", "pending", "manage_roles"],
            "badge_color": "navy",
        },
        {
            "role": "team_lead",
            "name": "Team Lead",
            "scope": "Assigned Project(s)",
            "description": "Manage roles for existing members of the assigned team.",
            "team_scope": "Own team only",
            "actions": ["view", "upload", "draft", "submit", "manage_roles"],
            "badge_color": "purple",
        },
        {
            "role": "member",
            "name": "Team Member",
            "scope": "Assigned Project(s)",
            "description": "Standard project member access. Can view authorized documents, ingest/upload new files, and submit drafts for review.",
            "team_scope": "Assigned team",
            "actions": ["view", "upload", "draft", "submit"],
            "badge_color": "slate",
        },
    ]
    all_actions = [
        {"action": "view", "label": "View Documents", "description": "Read authorized project files and search chunks"},
        {"action": "upload", "label": "Upload & Ingest", "description": "Ingest and vectorize new documents into project"},
        {"action": "draft", "label": "Create Drafts", "description": "Save unapproved or draft documents and notes"},
        {"action": "submit", "label": "Submit for Review", "description": "Send draft document to the Project Admin queue"},
        {"action": "approve", "label": "Approve Submissions", "description": "Publish reviewed document for project-wide access"},
        {"action": "reject", "label": "Reject Submissions", "description": "Return document with required rejection rationale"},
        {"action": "pending", "label": "Inspect Review Queue", "description": "Access pending-review documents queue"},
        {"action": "manage_roles", "label": "Manage Project Roles", "description": "Assign Member, Project Admin, and Team Lead roles to users"},
        {"action": "audit", "label": "Audit Logs", "description": "Inspect tenant-wide security and access audit trail"},
    ]
    return {
        "roles": roles_def,
        "actions": all_actions,
        "user_role": user.role,
        "is_org_admin": user.is_org_admin,
        "project_roles": user.project_roles,
    }


@app.post("/abac/simulate")
def simulate_abac_policy(
    request: AbacSimulateRequest,
    user: UserContext = Depends(get_current_user),
):
    """Simulate and evaluate multi-attribute access control policies against document attributes."""
    user_db = get_user_by_id(user.user_id) if user.user_id else None
    user_team = user_db.get("team_name") if user_db else (user.team_memberships.get(request.project_id, [None])[0] if user.team_memberships else None)

    evaluations = []

    # 1. Tenant Isolation check
    evaluations.append({
        "rule_name": "Tenant Collection Isolation",
        "dimension": "Tenant Boundary",
        "required": f"Tenant '{user.tenant_id}'",
        "actual": f"Tenant '{user.tenant_id}'",
        "passed": True,
        "explanation": f"Request is isolated within tenant collection '{user.tenant_id}'. Cross-tenant access is physically blocked at database vector layer.",
    })

    # 2. Project Scope check
    is_project_accessible = user.is_org_admin or (request.project_id in user.project_roles)
    user_proj_role = "org_admin" if user.is_org_admin else user.project_roles.get(request.project_id)
    evaluations.append({
        "rule_name": "Project Scope Membership",
        "dimension": "Project Scope",
        "required": f"Role assigned in project '{request.project_id}'",
        "actual": f"Role '{user_proj_role}'" if user_proj_role else "No project assignment",
        "passed": is_project_accessible,
        "explanation": "Super admin bypasses project restrictions with tenant-wide clearance" if user.is_org_admin else (
            f"User has '{user_proj_role}' role in project '{request.project_id}'." if is_project_accessible else
            f"User has not been granted membership in project '{request.project_id}'."
        ),
    })

    # 3. Sensitivity Level vs Clearance
    clearance_passed = user.is_org_admin or (user.sensitivity_clearance >= request.sensitivity_level)
    level_names = {0: "0 (Public)", 1: "1 (Internal)", 2: "2 (Confidential)", 3: "3 (Restricted)"}
    evaluations.append({
        "rule_name": "Sensitivity Clearance Enforcement",
        "dimension": "Sensitivity Clearance",
        "required": f"Clearance >= Level {level_names.get(request.sensitivity_level, str(request.sensitivity_level))}",
        "actual": f"User Clearance Level {level_names.get(user.sensitivity_clearance, str(user.sensitivity_clearance))}" if not user.is_org_admin else "Super Admin (Level 3 Bypass)",
        "passed": clearance_passed,
        "explanation": "Clearance is sufficient to read document content." if clearance_passed else f"User clearance (Level {user.sensitivity_clearance}) is lower than document sensitivity (Level {request.sensitivity_level}). Access prohibited.",
    })

    # 4. Team Visibility Tagging
    teams_req = request.visible_to_teams or []
    team_passed = (len(teams_req) == 0) or user.is_org_admin or (bool(user_team) and user_team in teams_req)
    evaluations.append({
        "rule_name": "Team Visibility Partition",
        "dimension": "Team Membership Tag",
        "required": f"Visible to: [{', '.join(teams_req)}]" if teams_req else "Unrestricted (All Teams in Project)",
        "actual": f"User Team: '{user_team}'" if user_team else "No team assigned",
        "passed": team_passed,
        "explanation": "Document is open to all teams within the project." if not teams_req else (
            "User's team tag matches document visibility tag." if team_passed else f"Document is restricted to [{', '.join(teams_req)}], whereas user belongs to '{user_team}'."
        ),
    })

    # 5. Lifecycle & Workflow State
    can_review = user.is_org_admin or user_proj_role in ("admin", "reviewer")
    is_owner = bool(request.uploaded_by) and request.uploaded_by == user.user_id
    workflow_passed = (request.workflow_state == "approved") or is_owner or can_review
    evaluations.append({
        "rule_name": "Document Lifecycle & State",
        "dimension": "Workflow State Attribute",
        "required": "State == 'approved' OR uploaded_by == user OR Reviewer/Admin role",
        "actual": f"State: '{request.workflow_state}' | Owner: {is_owner} | Reviewer: {can_review}",
        "passed": workflow_passed,
        "explanation": "Document is approved for general access." if request.workflow_state == "approved" else (
            "User is the original uploader (draft/pending owner access)." if is_owner else (
                "User has Reviewer/Admin role to inspect unapproved submissions." if can_review else
                f"Document is in '{request.workflow_state}' state and cannot be viewed by regular members until approved."
            )
        ),
    })

    overall_allowed = is_project_accessible and clearance_passed and team_passed and workflow_passed

    q_filter = {}
    try:
        raw_filter = build_access_filter(user, request.project_id if is_project_accessible else None)
        q_filter = {
            "must": [
                cond.model_dump() if hasattr(cond, "model_dump") else (
                    cond.dict() if hasattr(cond, "dict") else str(cond)
                )
                for cond in (raw_filter.must or [])
            ]
        }
    except Exception as exc:
        q_filter = {"filter_error": str(exc)}

    return {
        "allowed": overall_allowed,
        "verdict": "ACCESS GRANTED (ALLOW)" if overall_allowed else "ACCESS DENIED (DENY)",
        "user_context": {
            "user_id": user.user_id,
            "tenant_id": user.tenant_id,
            "is_org_admin": user.is_org_admin,
            "role": user.role,
            "team_name": user_team,
            "sensitivity_clearance": user.sensitivity_clearance,
        },
        "evaluations": evaluations,
        "qdrant_filter": q_filter,
    }


@app.get("/stages", response_model=list[str])
def get_available_stages(user: UserContext = Depends(get_optional_user)):
    """Get valid SDLC stages for the tenant."""
    return get_valid_stages(user.tenant_id)


@app.get("/chat/sessions")
def get_chat_sessions(
    project_id: str | None = None,
    mode: str = "query",
    user: UserContext = Depends(get_current_user),
):
    if mode not in {"query", "rag"}:
        raise HTTPException(status_code=422, detail="mode must be query or rag")
    if project_id:
        try:
            require_project(user, project_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    return list_chat_sessions(user.tenant_id, user.user_id, project_id, mode)


@app.post("/chat/sessions", status_code=201)
def start_chat_session(
    request: ChatSessionRequest,
    user: UserContext = Depends(get_current_user),
):
    if request.project_id:
        try:
            require_project(user, request.project_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    return create_chat_session(
        user.tenant_id,
        user.user_id,
        request.project_id,
        request.mode,
        request.title,
    )


@app.get("/chat/sessions/{session_id}/messages")
def get_chat_session_messages(
    session_id: str,
    user: UserContext = Depends(get_current_user),
):
    session = get_chat_session(user.tenant_id, user.user_id, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return list_chat_messages(session_id)


@app.post("/chat/sessions/{session_id}/messages", status_code=201)
def append_chat_message(
    session_id: str,
    request: ChatMessageRequest,
    user: UserContext = Depends(get_current_user),
):
    session = get_chat_session(user.tenant_id, user.user_id, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return add_chat_message(session_id, request.role, request.content, request.sources)


@app.delete("/chat/sessions/{session_id}")
def remove_chat_session(
    session_id: str,
    user: UserContext = Depends(get_current_user),
):
    if not delete_chat_session(user.tenant_id, user.user_id, session_id):
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {"status": "deleted", "session_id": session_id}


@app.post("/ask", response_model=AskResponse)
def ask_documents(
    request: AskRequest,
    user: UserContext = Depends(get_optional_user),
    client: QdrantClient = Depends(get_qdrant_client),
):
    """Retrieve authorized chunks and answer the question with Gemini."""
    start = time.time()
    try:
        # A query without an active project is general knowledge. Never mix
        # unrelated tenant documents into a standalone question.
        if not request.project_id:
            return AskResponse(answer=generate_answer(request.query), sources=[])
        access_filter = build_access_filter(user, project_id=request.project_id)
        hits = hybrid_search(
            client=client,
            tenant_id=user.tenant_id,
            query_text=request.query,
            dense_vector=embed_dense(request.query, task_type="RETRIEVAL_QUERY"),
            sparse_vector=embed_sparse(request.query),
            access_filter=access_filter,
        )
        if not hits:
            no_projects = not user.is_org_admin and not user.project_roles
            if no_projects:
                return AskResponse(
                    answer=(
                        "You haven't uploaded any project documents yet.\n\n"
                        "To get started:\n"
                        "1. Go to **04 Ingest Documents** in the sidebar\n"
                        "2. Create a project and upload your first document (PDF, Word, Markdown, etc.)\n"
                        "3. Come back here and ask any question about your documents!\n\n"
                        "DocFlow will use Gemini AI to answer questions grounded in your uploaded content."
                    ),
                    sources=[]
                )
            if not request.project_id:
                return AskResponse(answer="I could not find relevant information in the uploaded documents.", sources=[])
        context_parts = []
        context_length = 0
        max_context_chars = 18000
        for index, hit in enumerate(hits):
            part = (
                f"[Source {index + 1}: {hit['payload']['document_id']}, {hit['payload']['section_title']}]\n"
                f"{hit['payload']['chunk_text']}"
            )
            remaining = max_context_chars - context_length
            if remaining <= 0:
                break
            context_parts.append(part[:remaining])
            context_length += len(context_parts[-1])
        context = "\n\n".join(context_parts)
        project_context = ""
        if request.project_id:
            project_documents = database_list_documents(user.tenant_id, request.project_id)
            project_context = GeneralQueryAgent.build_project_context(
                request.project_id,
                project_documents,
                get_valid_stages(user.tenant_id),
            )
            all_project_chunks = []
            next_offset = None
            while True:
                points, next_offset = client.scroll(
                    collection_name=collection_name(user.tenant_id),
                    scroll_filter=access_filter,
                    limit=256,
                    offset=next_offset,
                    with_payload=True,
                    with_vectors=False,
                )
                all_project_chunks.extend(
                    point.payload for point in points
                    if point.payload and point.payload.get("project_id") == request.project_id
                )
                if next_offset is None:
                    break
            all_project_chunks.sort(key=lambda item: (
                item.get("document_id", ""),
                item.get("section_title", ""),
                item.get("chunk_index", 0),
            ))
            full_project_source = "\n\n".join(
                f"[Document: {chunk.get('document_id', 'unknown')}, "
                f"{chunk.get('section_title', 'Unknown section')}]\n"
                f"{chunk.get('chunk_text', '')}"
                for chunk in all_project_chunks
            )
            project_context = f"{project_context}\n\nComplete project documents:\n{full_project_source}"
        answer = generate_answer(
            "You are DocFlow's General Query Agent. Gemini must handle the complete request. "
            "Treat every stage in the selected project as relevant context, not only the stage "
            "of the highest-ranked search result. Use the retrieved source text plus the complete "
            "stage coverage and gap scan below. "
            "Answer directly, cite document evidence as [Source N] when available, and explain "
            "what to do next when the user asks for an action. You can answer questions about "
            "requirements, summaries, missing documents, drafting, scanning, approvals, access, "
            "and project workflow. Never claim an action was completed unless this API actually "
            "performed it. If evidence is missing, say that clearly.\n\n"
            f"Question: {request.query}\n\n"
            f"Retrieved sources:\n{context or 'No matching source chunks were retrieved.'}\n\n"
            f"Whole-project scan:\n{project_context or 'No project was selected.'}"
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    source_hits = _unique_source_hits(hits)
    sources = [
        SearchResult(
            document_id=hit["payload"].get("document_id", ""),
            section_title=_canonical_source_name(hit["payload"].get("section_title", "Unknown source")),
            chunk_text=hit["payload"]["chunk_text"],
            score=hit["score"],
        )
        for hit in source_hits
    ]
    
    # Telemetry
    latency_ms = (time.time() - start) * 1000
    PerformanceMetrics.log_query_latency(request.query, latency_ms, len(hits))
    
    return AskResponse(answer=answer, sources=sources)


@app.post("/search", response_model=list[SearchResult])
def search_documents(
    request: SearchRequest,
    user: UserContext = Depends(get_optional_user),
    client: QdrantClient = Depends(get_qdrant_client),
):
    """Search documents with hybrid retrieval and return matching chunks."""
    start = time.time()
    
    try:
        access_filter = build_access_filter(user, project_id=request.project_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        dense_vector = embed_dense(request.query, task_type="RETRIEVAL_QUERY")
        sparse_vector = embed_sparse(request.query)
        hits = hybrid_search(
            client=client,
            tenant_id=user.tenant_id,
            query_text=request.query,
            dense_vector=dense_vector,
            sparse_vector=sparse_vector,
            access_filter=access_filter,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    results = [
        SearchResult(
            document_id=hit["payload"]["document_id"],
            section_title=_canonical_source_name(hit["payload"]["section_title"]),
            chunk_text=hit["payload"]["chunk_text"],
            score=hit["score"],
        )
        for hit in _unique_source_hits(hits)
    ]
    
    # Telemetry
    latency_ms = (time.time() - start) * 1000
    PerformanceMetrics.log_query_latency(request.query, latency_ms, len(results))
    
    return results


# ============= AGENT ENDPOINTS =============

from app.agents import DraftingAgent, GapDetectionAgent, GeneralQueryAgent


class GenerateOutlineRequest(BaseModel):
    project_id: str
    stage: str


class GenerateOutlineResponse(BaseModel):
    outline: str
    stage: str


class GapAnalysisResponse(BaseModel):
    project_id: str
    total_gaps: int
    gaps_by_stage: dict
    gap_report: str


class SuggestContentRequest(BaseModel):
    document_type: str
    project_context: str
    stage: str


class FollowupSuggestions(BaseModel):
    followup_questions: list[str]


@app.post("/agents/drafting/outline", response_model=GenerateOutlineResponse)
def agent_generate_outline(
    request: GenerateOutlineRequest,
    user: UserContext = Depends(get_current_user),
):
    """Generate a documentation outline for a project stage (Drafting Agent)."""
    try:
        documents = database_list_documents(user.tenant_id, request.project_id)
        outline = DraftingAgent.generate_document_outline(
            project_id=request.project_id,
            stage=request.stage,
            existing_docs=documents,
            user=user,
        )
        audit_log(user.tenant_id, user.user_id, "AGENT_CALL", "drafting_agent", request.project_id, f"stage={request.stage}")
        return GenerateOutlineResponse(outline=outline, stage=request.stage)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/agents/gap-detection/analyze", response_model=GapAnalysisResponse)
def agent_analyze_gaps(
    project_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Analyze documentation gaps in a project (Gap-Detection Agent)."""
    try:
        stages = get_valid_stages(user.tenant_id)
        documents = database_list_documents(user.tenant_id, project_id)
        
        gaps = GapDetectionAgent.detect_gaps(
            project_id=project_id,
            stages=stages,
            existing_docs=documents,
        )
        
        gap_report = GapDetectionAgent.generate_gap_report(
            project_id=project_id,
            gaps=gaps,
            existing_docs=documents,
        )
        
        audit_log(user.tenant_id, user.user_id, "AGENT_CALL", "gap_detection_agent", project_id, f"gaps_found={gaps['total_gaps']}")
        
        return GapAnalysisResponse(
            project_id=project_id,
            total_gaps=gaps["total_gaps"],
            gaps_by_stage=gaps["gaps_by_stage"],
            gap_report=gap_report,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/agents/query/followups", response_model=FollowupSuggestions)
def agent_suggest_followups(
    request: AskRequest,
    user: UserContext = Depends(get_optional_user),
    client: QdrantClient = Depends(get_qdrant_client),
):
    """Suggest follow-up questions based on an answer (General Query Agent)."""
    try:
        access_filter = build_access_filter(user, project_id=request.project_id)
        hits = hybrid_search(
            client=client,
            tenant_id=user.tenant_id,
            query_text=request.query,
            dense_vector=embed_dense(request.query, task_type="RETRIEVAL_QUERY"),
            sparse_vector=embed_sparse(request.query),
            access_filter=access_filter,
        )
        
        if not hits:
            return FollowupSuggestions(followup_questions=[])
        
        # Generate initial answer
        context = "\n\n".join(
            f"[Source {index + 1}: {hit['payload']['document_id']}]\n{hit['payload']['chunk_text']}"
            for index, hit in enumerate(hits[:3])
        )
        answer = generate_answer(f"Briefly answer: {request.query}\n\nContext:\n{context}")
        
        # Suggest follow-ups
        followups = GeneralQueryAgent.suggest_followup_queries(request.query, answer)
        
        audit_log(user.tenant_id, user.user_id, "AGENT_CALL", "query_agent", request.project_id or "all", f"followups={len(followups)}")
        
        return FollowupSuggestions(followup_questions=followups)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ============= STUDIO: DRAFTING AGENT + SCANNER AGENT (used by the Studio panel) =============

class GenerateDraftRequest(BaseModel):
    document_type: str = Field(min_length=1, max_length=120)
    project_stage: str = Field(min_length=1, max_length=120)
    instructions: str = Field(default="", max_length=8000)
    project_id: str | None = None
    current_content: str | None = None


class GenerateDraftResponse(BaseModel):
    draft: str
    document_type: str
    project_stage: str


class ScanDraftRequest(BaseModel):
    document_text: str = Field(min_length=1, max_length=100_000)
    document_type: str = Field(min_length=1, max_length=120)
    project_stage: str = Field(min_length=1, max_length=120)


class ScanDraftResponse(BaseModel):
    score: dict
    revised_document: str | None = None
    line_suggestions: list[dict] = Field(default_factory=list)


def _scan_document_text(document_text: str, document_type: str, project_stage: str, user: UserContext) -> ScanDraftResponse:
    result = score_document(document_text, document_type, project_stage)
    revised = None if result.get("passed") else reform_document(document_text, document_type, project_stage, result)
    line_suggestions = review_document_lines(document_text, document_type, project_stage)
    audit_log(user.tenant_id, user.user_id, "AGENT_CALL", "scanner_agent", document_type, f"passed={result.get('passed')}")
    return ScanDraftResponse(score=result, revised_document=revised, line_suggestions=line_suggestions)


class SaveDraftRequest(BaseModel):
    document_text: str = Field(min_length=1, max_length=100_000)
    document_type: str = Field(min_length=1, max_length=120)
    project_stage: str = Field(min_length=1, max_length=120)
    filename: str | None = None
    project_id: str | None = None


class SaveDraftResponse(BaseModel):
    path: str
    filename: str


class DownloadDraftRequest(BaseModel):
    document_text: str = Field(min_length=1, max_length=100_000)
    document_type: str = Field(min_length=1, max_length=120)
    project_stage: str = Field(min_length=1, max_length=120)
    project_id: str | None = None


@app.post("/studio/generate-draft", response_model=GenerateDraftResponse)
def studio_generate_draft(
    request: GenerateDraftRequest,
    user: UserContext = Depends(get_current_user),
):
    """Drafting Agent: turn free-form instructions into a structured first draft."""
    if request.project_id:
        try:
            require_action(user, request.project_id, "draft")
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    draft = generate_draft(
        request.document_type,
        request.project_stage,
        user_prompt=request.instructions,
        current_content=request.current_content,
    )
    audit_log(user.tenant_id, user.user_id, "AGENT_CALL", "drafting_agent", request.project_id or "studio", f"doc_type={request.document_type}")
    return GenerateDraftResponse(draft=draft, document_type=request.document_type, project_stage=request.project_stage)


@app.post("/studio/scan-draft", response_model=ScanDraftResponse)
def studio_scan_draft(
    request: ScanDraftRequest,
    user: UserContext = Depends(get_current_user),
):
    """Scanner Agent: score a draft against the structure/completeness/labeling rubric,
    and propose a revision when it falls below the passing threshold."""
    return _scan_document_text(request.document_text, request.document_type, request.project_stage, user)


@app.post("/studio/scan-upload", response_model=ScanDraftResponse)
def studio_scan_upload(
    file: UploadFile = File(...),
    document_type: str = Form("General Document"),
    project_stage: str = Form("Requirements"),
    user: UserContext = Depends(get_current_user),
):
    """Upload any supported document and scan its extracted text."""
    contents = file.file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Documents must be 10 MB or smaller")
    try:
        text = extract_text(contents, file.filename or "document.txt")
    except (ValueError, RuntimeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not text.strip():
        raise HTTPException(status_code=422, detail="The uploaded document contains no readable text")
    return _scan_document_text(text, document_type, project_stage, user)


@app.post("/projects/{project_id}/documents/{document_id}/scan", response_model=ScanDraftResponse)
def scan_project_document(
    project_id: str,
    document_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Scan a readable document already stored in the current project."""
    try:
        require_action(user, project_id, "view")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    document = get_document(user.tenant_id, document_id)
    if not document or document.get("project_id") != project_id:
        raise HTTPException(status_code=404, detail="Project document not found")
    stored_path = Path(document["stored_path"])
    if not stored_path.exists():
        raise HTTPException(status_code=404, detail="Stored document file not found")
    try:
        text = extract_text(stored_path.read_bytes(), document["filename"])
    except (ValueError, RuntimeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _scan_document_text(text, document["doc_type"], document["stage"], user)


@app.post("/studio/save-draft", response_model=SaveDraftResponse)
def studio_save_draft(
    request: SaveDraftRequest,
    user: UserContext = Depends(get_current_user),
):
    """Persist an approved draft to the server-side drafts/ folder."""
    saved = save_document(
        request.document_text,
        request.document_type,
        request.project_stage,
        filename=request.filename,
        user_id=user.user_id,
    )
    audit_log(user.tenant_id, user.user_id, "SAVE_DRAFT", "draft", saved["filename"], f"project={request.project_id or 'none'}")
    return SaveDraftResponse(**saved)


@app.get("/studio/saved-drafts")
def studio_saved_drafts(user: UserContext = Depends(get_current_user)):
    """List drafts saved in the local Studio drafts folder."""
    return {"drafts": load_saved_documents(user.user_id)}


@app.get("/studio/saved-drafts/{filename}")
def studio_saved_draft(filename: str, user: UserContext = Depends(get_current_user)):
    """Return one saved Studio draft for viewing in the preview."""
    safe_filename = Path(filename).name
    if safe_filename != filename or not safe_filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="Invalid saved draft filename")
    draft_path = (
        Path(__file__).resolve().parents[2]
        / "drafts"
        / re.sub(r"[^a-zA-Z0-9_.-]+", "_", user.user_id).strip("._")
        / safe_filename
    )
    if not draft_path.is_file():
        raise HTTPException(status_code=404, detail="Saved draft not found")
    return PlainTextResponse(draft_path.read_text(encoding="utf-8"))


@app.post("/studio/download-draft")
def studio_download_draft(
    request: DownloadDraftRequest,
    user: UserContext = Depends(get_current_user),
):
    """Create a formatted Word document from a generated draft."""
    if request.project_id:
        try:
            require_action(user, request.project_id, "draft")
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    html_document = _draft_to_word_html(request.document_text)
    with NamedTemporaryFile("w", suffix=".doc", delete=False, encoding="utf-8") as temporary:
        temporary.write(html_document)
        path = temporary.name
    safe_type = re.sub(r"[^a-zA-Z0-9]+", "-", request.document_type).strip("-").lower() or "draft"
    return FileResponse(path, media_type="application/msword", filename=f"{safe_type}-{request.project_stage.lower().replace(' ', '-')}.doc")


def _draft_to_word_html(document_text: str) -> str:
    """Create a Word-compatible document that preserves the preview's HTML layout."""
    lines = document_text.replace("\r\n", "\n").splitlines()
    blocks = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            index += 1
            continue
        if stripped.startswith("```"):
            diagram = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                diagram.append(lines[index])
                index += 1
            blocks.append(f'<pre class="diagram">{html.escape(chr(10).join(diagram).strip())}</pre>')
            index += 1
            continue
        separator_index = index + 1
        while separator_index < len(lines) and not lines[separator_index].strip():
            separator_index += 1
        if _is_markdown_table_row(stripped) and separator_index < len(lines) and _is_markdown_table_separator(lines[separator_index].strip()):
            headers = _markdown_table_cells(stripped)
            rows = []
            index = separator_index + 1
            while index < len(lines):
                if not lines[index].strip():
                    index += 1
                    continue
                if not _is_markdown_table_row(lines[index].strip()):
                    break
                rows.append(_markdown_table_cells(lines[index].strip()))
                index += 1
            header_html = "".join(f"<th>{_inline_word_html(value)}</th>" for value in headers)
            row_html = "".join(
                "<tr>" + "".join(
                    f"<td>{_inline_word_html(values[column] if column < len(values) else '')}</td>"
                    for column in range(len(headers))
                ) + "</tr>"
                for values in rows
            )
            blocks.append(f'<table><thead><tr>{header_html}</tr></thead><tbody>{row_html}</tbody></table>')
            continue
        if stripped.startswith("#"):
            level = min(len(stripped) - len(stripped.lstrip("#")), 3)
            blocks.append(f"<h{level}>{_inline_word_html(stripped[level:].strip())}</h{level}>")
        elif re.match(r"^[-*]\s+", stripped):
            blocks.append(f"<p class=\"bullet\">{_inline_word_html(re.sub(r'^[-*]\s+', '', stripped))}</p>")
        elif re.match(r"^\d+\.\s+", stripped):
            blocks.append(f"<p class=\"numbered\">{_inline_word_html(re.sub(r'^\d+\.\s+', '', stripped))}</p>")
        else:
            blocks.append(f"<p>{_inline_word_html(stripped)}</p>")
        index += 1
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
@page {{ margin: 0.75in 0.85in; }}
body {{ font-family: Aptos, Arial, sans-serif; font-size: 10.5pt; color: #111827; line-height: 1.45; }}
h1 {{ font-size: 22pt; margin: 0 0 18pt; border-bottom: 2px solid #111827; padding-bottom: 8pt; }}
h2 {{ font-size: 15pt; margin: 20pt 0 6pt; border-bottom: 1px solid #d1d5db; padding-bottom: 3pt; }}
h3 {{ font-size: 12pt; margin: 14pt 0 5pt; }}
p {{ margin: 0 0 8pt; }}
.bullet {{ margin-left: 18pt; text-indent: -10pt; }}
.bullet:before {{ content: "• "; }}
.numbered {{ margin-left: 20pt; text-indent: -12pt; }}
table {{ width: 100%; border-collapse: collapse; margin: 12pt 0 16pt; table-layout: auto; }}
th, td {{ border: 1px solid #9ca3af; padding: 6pt 8pt; vertical-align: top; text-align: left; }}
th {{ background: #d9eaf7; font-weight: bold; }}
tr:nth-child(even) td {{ background: #f9fafb; }}
.diagram {{ white-space: pre; font-family: Consolas, "Courier New", monospace; font-size: 9pt; background: #f3f4f6; border: 1px solid #9ca3af; padding: 10pt; margin: 12pt 0 16pt; }}
code {{ font-family: Consolas, "Courier New", monospace; }}
</style>
</head>
<body>{''.join(blocks)}</body>
</html>"""


def _inline_word_html(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"__(.+?)__", r"<strong>\1</strong>", escaped)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", escaped)


def _markdown_table_cells(line: str) -> list[str]:
    return [html.unescape(cell).strip() for cell in line.strip().strip("|").split("|")]


def _is_markdown_table_row(line: str) -> bool:
    return line.startswith("|") and line.endswith("|") and len(_markdown_table_cells(line)) >= 2


def _is_markdown_table_separator(line: str) -> bool:
    return _is_markdown_table_row(line) and all(
        re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in _markdown_table_cells(line)
    )


def _group_openapi_routes() -> None:
    """Apply stable product groups to routes that do not declare tags inline."""
    for route in app.routes:
        path = getattr(route, "path", "")
        if not hasattr(route, "tags") or route.tags:
            continue
        if path in {"/health", "/agent/chat"}:
            route.tags = ["System"]
        elif path.startswith(("/signup", "/login", "/auth/", "/me")):
            route.tags = ["Identity"]
        elif path.startswith(("/ask", "/search", "/agents/")):
            route.tags = ["Assistant"]
        elif path.startswith("/studio/"):
            route.tags = ["Studio"]
        elif path.startswith(("/admin/", "/rbac/", "/abac/")) or any(
            action in path for action in ("/submit", "/approve", "/reject", "/pending-approvals")
        ):
            route.tags = ["Governance"]
        elif path.startswith("/notes"):
            route.tags = ["Notes"]
        else:
            route.tags = ["Workspace"]


_group_openapi_routes()
