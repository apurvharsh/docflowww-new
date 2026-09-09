"""Small self-contained authentication layer for local deployment."""

import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.config import settings
from app.database import get_user_access_context, get_user_by_id, get_user_by_email
from app.models.schema import UserContext


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000).hex()


def authenticate(username: str, password: str) -> str | None:
    if settings.auth_password and hmac.compare_digest(username, settings.auth_username):
        salt = settings.auth_secret.encode()[:16].ljust(16, b"0")
        expected = _hash_password(settings.auth_password, salt)
        supplied = _hash_password(password, salt)
        if hmac.compare_digest(supplied, expected):
            return create_token(subject=username, user_id=settings.dev_user_id)
    user = get_user_by_email(username)
    if not user or not user["password_hash"]:
        return None
    salt = settings.auth_secret.encode()[:16].ljust(16, b"0")
    if not hmac.compare_digest(_hash_password(password, salt), user["password_hash"]):
        return None
    return create_token(subject=user["username"], user_id=user["user_id"])


def create_token(subject: str | None = None, user_id: str | None = None) -> str:
    payload = {"sub": subject or settings.auth_username, "exp": int(time.time()) + 8 * 60 * 60}
    if user_id:
        payload["uid"] = user_id
    encoded = _encode(payload)
    signature = _sign(encoded)
    return f"{encoded}.{signature}"


def user_from_token(token: str) -> UserContext:
    try:
        encoded, signature = token.split(".", 1)
        if not hmac.compare_digest(signature, _sign(encoded)):
            raise ValueError("invalid signature")
        payload = json.loads(_decode(encoded))
        if payload["exp"] < time.time() or not payload["sub"]:
            raise ValueError("expired token")
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid or expired token") from exc
    user = get_user_by_id(payload.get("uid", "")) if payload.get("uid") else None
    if user:
        context = get_user_access_context(user["user_id"])
        if context:
            return context
        raise ValueError("Invalid or expired token")
    if payload["sub"] != settings.auth_username:
        raise ValueError("Invalid or expired token")
    return UserContext(user_id=settings.dev_user_id, tenant_id=settings.dev_tenant_id, is_org_admin=settings.dev_org_admin, role="admin" if settings.dev_org_admin else "member", sensitivity_clearance=settings.dev_sensitivity_clearance)


def google_is_configured() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def create_oauth_state() -> str:
    payload = {"nonce": secrets.token_urlsafe(32), "exp": int(time.time()) + 600}
    encoded = _encode(payload)
    return f"{encoded}.{_sign(encoded)}"


def validate_oauth_state(state: str) -> bool:
    """Validate the signed, short-lived OAuth state returned by Google."""
    try:
        encoded, signature = state.split(".", 1)
        if not hmac.compare_digest(signature, _sign(encoded)):
            return False
        payload = json.loads(_decode(encoded))
        return bool(payload.get("nonce")) and int(payload.get("exp", 0)) >= int(time.time())
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


# --- NEW: centralized cookie-security helper -------------------------------
# Import this in your route file (wherever /auth/google and
# /auth/google/callback are defined) instead of hardcoding secure=True.
#
# Usage in your route handler:
#
#   from app.auth import create_oauth_state, oauth_state_cookie_kwargs
#
#   @router.get("/auth/google")
#   async def google_login(request: Request):
#       state = create_oauth_state()
#       response = RedirectResponse(google_authorization_url(state))
#       response.set_cookie("oauth_state", state, **oauth_state_cookie_kwargs(request))
#       return response
#
#   @router.get("/auth/google/callback")
#   async def google_callback(request: Request, state: str, code: str):
#       cookie_state = request.cookies.get("oauth_state")
#       if not cookie_state or not hmac.compare_digest(cookie_state, state):
#           raise HTTPException(status_code=400, detail="Invalid Google sign-in state")
#       ...
#
def oauth_state_cookie_kwargs(request) -> dict:
    """Cookie kwargs for the OAuth state cookie.

    `secure` is only True when the incoming request is actually HTTPS —
    browsers silently drop Secure cookies over plain HTTP, which is what
    causes 'Invalid Google sign-in state' when testing on
    http://localhost or http://127.0.0.1.
    """
    is_https = request.url.scheme == "https"
    return {
        "httponly": True,
        "secure": is_https,
        "samesite": "lax",
        "max_age": 600,
        "path": "/",
    }
# -----------------------------------------------------------------------------


def public_google_redirect_uri() -> str:
    return f"{settings.frontend_url.rstrip('/')}/api/auth/google/callback"


def google_authorization_url(state: str) -> str:
    if not google_is_configured():
        raise ValueError("Google sign-in is not configured")
    query = urlencode({
        "client_id": settings.google_client_id,
        "redirect_uri": public_google_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "state": state,
        "prompt": "select_account",
    })
    return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"


def exchange_google_code(code: str) -> dict:
    if not google_is_configured():
        raise ValueError("Google sign-in is not configured")
    payload = urlencode({
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": public_google_redirect_uri(),
        "grant_type": "authorization_code",
    }).encode()
    request = Request(
        "https://oauth2.googleapis.com/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            token_data = json.loads(response.read())
    except Exception as exc:
        raise ValueError("Google token exchange failed") from exc
    identity_token = token_data.get("id_token")
    if not identity_token:
        raise ValueError("Google did not return an identity token")
    try:
        with urlopen(f"https://oauth2.googleapis.com/tokeninfo?id_token={identity_token}", timeout=15) as response:
            identity = json.loads(response.read())
    except Exception as exc:
        raise ValueError("Google identity verification failed") from exc
    if identity.get("aud") != settings.google_client_id or identity.get("email_verified") != "true":
        raise ValueError("Google account could not be verified")
    return {"subject": identity["sub"], "email": identity["email"].lower(), "name": identity.get("name")}


def _encode(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")


def _decode(value: str) -> str:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()


def _sign(value: str) -> str:
    return base64.urlsafe_b64encode(hmac.new(settings.auth_secret.encode(), value.encode(), hashlib.sha256).digest()).decode().rstrip("=")