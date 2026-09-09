"""Request models for API endpoints."""

from pydantic import BaseModel, Field, field_validator


class LoginRequest(BaseModel):
    """Email/password login request."""
    email: str = Field(min_length=5, max_length=254)
    password: str


class SignupRequest(BaseModel):
    """Invitation-backed account creation request."""
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=256)
    full_name: str = Field(min_length=2, max_length=120)
    invitation_token: str = Field(min_length=20, max_length=200)
    team_name: str | None = Field(default=None, max_length=100)
    job_title: str | None = Field(default=None, max_length=100)
    manager_email: str | None = None


class SearchRequest(BaseModel):
    """Search/query request."""
    query: str = Field(min_length=1, max_length=4000)
    project_id: str | None = None

    @field_validator("query")
    @classmethod
    def query_must_contain_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must contain text")
        return value.strip()


class AskRequest(BaseModel):
    """AI question-answering request."""
    query: str = Field(min_length=1, max_length=4000)
    project_id: str | None = None

    @field_validator("query")
    @classmethod
    def query_must_contain_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must contain text")
        return value.strip()


class ApprovalRequest(BaseModel):
    """Document approval request."""
    approval_reason: str | None = None


class RejectionRequest(BaseModel):
    """Document rejection request."""
    rejection_reason: str = Field(min_length=5, max_length=500)


class SetRoleRequest(BaseModel):
    """User role assignment request."""
    role: str = Field(pattern="^(member|reviewer|admin|team_lead)$")


class GenerateOutlineRequest(BaseModel):
    """Drafting agent outline generation request."""
    project_id: str
    stage: str


class SuggestContentRequest(BaseModel):
    """Drafting agent content suggestion request."""
    document_type: str
    project_context: str
    stage: str
