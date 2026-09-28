"""Hosted AuthKit sign-in and sealed-cookie sessions for host accounts."""

from __future__ import annotations

import os
import re
import secrets
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import auth_mode, create_api_key, hash_api_key
from app.dependencies import get_session
from app.models import Host, Schedule


router = APIRouter(prefix="/v1/auth", tags=["authentication"])
SESSION_COOKIE = "wos_session"
STATE_COOKIE = "wos_oauth_state"


def _settings() -> tuple[str, str, bool]:
    try:
        if auth_mode() != "workos":
            raise HTTPException(status_code=404, detail="WorkOS authentication is unavailable")
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Authentication is not configured") from None
    redirect_uri = os.environ["WORKOS_REDIRECT_URI"]
    uri = urlsplit(redirect_uri)
    localhost = uri.hostname in ("localhost", "127.0.0.1", "::1")
    if (uri.scheme != "https" and not (localhost and uri.scheme == "http")) or not uri.netloc or uri.fragment:
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    post_login = os.getenv("WORKOS_POST_LOGIN_URL", "/docs")
    if not post_login.startswith("/") or post_login.startswith("//") or "\\" in post_login or any(ord(c) < 32 for c in post_login):
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    return redirect_uri, post_login, not localhost


def _client():
    from workos import WorkOSClient

    return WorkOSClient(api_key=os.environ["WORKOS_API_KEY"], client_id=os.environ["WORKOS_CLIENT_ID"])


def _cookie(response: Response, name: str, value: str, secure: bool, max_age: int | None = None) -> None:
    response.set_cookie(name, value, httponly=True, secure=secure, samesite="lax", max_age=max_age, path="/")


def require_same_origin_mutation(request: Request) -> None:
    """Cookie-authenticated writes must originate from the configured app origin."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    redirect_uri, _, _ = _settings()
    parsed = urlsplit(redirect_uri)
    expected = f"{parsed.scheme}://{parsed.netloc}"
    if request.headers.get("origin") != expected:
        raise HTTPException(status_code=403, detail="Invalid request origin")


def authenticated_workos_user(request: Request, response: Response) -> str:
    """Validate the provider-signed JWT inside the sealed session before host lookup."""
    _, _, secure = _settings()
    require_same_origin_mutation(request)
    sealed = request.cookies.get(SESSION_COOKIE)
    if not sealed:
        raise HTTPException(status_code=401, detail="WorkOS session required")
    try:
        session = _client().user_management.load_sealed_session(
            session_data=sealed, cookie_password=os.environ["WORKOS_COOKIE_PASSWORD"]
        )
        result = session.authenticate()
        if not result.authenticated:
            result = session.refresh()
            if result.authenticated:
                _cookie(response, SESSION_COOKIE, result.sealed_session, secure)
        user = result.user if result.authenticated else None
        user_id = user.get("id") if isinstance(user, dict) else None
        if not isinstance(user_id, str) or not user_id:
            raise HTTPException(status_code=401, detail="Invalid WorkOS session")
        return user_id
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid WorkOS session") from None


def _provision_host(db: Session, user) -> Host:
    user_id = getattr(user, "id", None)
    email = getattr(user, "email", None)
    if not isinstance(user_id, str) or not user_id or not isinstance(email, str) or not email:
        raise HTTPException(status_code=401, detail="WorkOS account has no usable identity")
    existing = db.scalar(select(Host).where(Host.workos_user_id == user_id))
    if existing:
        return existing
    # An email match alone cannot link a local host to a WorkOS identity.
    if db.scalar(select(Host).where(Host.email == email.strip().lower())):
        raise HTTPException(status_code=409, detail="Host email is already in use")
    base = re.sub(r"[^a-z0-9_-]+", "-", email.split("@", 1)[0].lower()).strip("-_ ")[:60] or "host"
    name = " ".join(part for part in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if part)
    for _ in range(4):
        host = Host(
            username=f"{base}-{secrets.token_hex(4)}",
            display_name=(name or base)[:120],
            email=email.strip().lower(),
            timezone="UTC",
            api_key_hash=hash_api_key(create_api_key()),
            workos_user_id=user_id,
        )
        db.add(Schedule(host=host, name="Default hours", timezone="UTC"))
        try:
            db.commit()
            return host
        except IntegrityError:
            db.rollback()
            existing = db.scalar(select(Host).where(Host.workos_user_id == user_id))
            if existing:
                return existing
            if db.scalar(select(Host).where(Host.email == email.strip().lower())):
                raise HTTPException(status_code=409, detail="Host email is already in use") from None
    raise HTTPException(status_code=409, detail="Host profile could not be created")


@router.get("/login")
def login():
    redirect_uri, _, secure = _settings()
    state = secrets.token_urlsafe(32)
    try:
        url = _client().user_management.get_authorization_url(
            provider="authkit", redirect_uri=redirect_uri, state=state
        )
    except Exception:
        raise HTTPException(status_code=502, detail="Authentication provider unavailable") from None
    response = RedirectResponse(url, status_code=302)
    _cookie(response, STATE_COOKIE, state, secure, max_age=600)
    return response


@router.get("/callback")
def callback(request: Request, code: str | None = None, state: str | None = None, db: Session = Depends(get_session)):
    _, post_login, secure = _settings()
    expected = request.cookies.get(STATE_COOKIE)
    if not expected or not state or not secrets.compare_digest(expected, state) or not code:
        raise HTTPException(status_code=400, detail="Invalid authentication callback")
    try:
        auth = _client().user_management.authenticate_with_code(code=code)
        if not auth.access_token or not auth.refresh_token:
            raise ValueError("Missing provider session")
    except Exception:
        raise HTTPException(status_code=401, detail="Authentication failed") from None
    _provision_host(db, auth.user)
    from workos.session import seal_session_from_auth_response

    sealed = seal_session_from_auth_response(
        access_token=auth.access_token,
        refresh_token=auth.refresh_token,
        user=auth.user.to_dict(),
        impersonator=auth.impersonator.to_dict() if auth.impersonator else None,
        cookie_password=os.environ["WORKOS_COOKIE_PASSWORD"],
    )
    response = RedirectResponse(post_login, status_code=302)
    response.delete_cookie(STATE_COOKIE, path="/", secure=secure, httponly=True, samesite="lax")
    _cookie(response, SESSION_COOKIE, sealed, secure)
    return response


@router.post("/logout")
def logout(request: Request):
    _, post_login, secure = _settings()
    require_same_origin_mutation(request)
    sealed = request.cookies.get(SESSION_COOKIE)
    url = post_login
    if sealed:
        try:
            session = _client().user_management.load_sealed_session(
                session_data=sealed, cookie_password=os.environ["WORKOS_COOKIE_PASSWORD"]
            )
            auth = session.authenticate()
            if auth.authenticated:
                url = session.get_logout_url()
        except Exception:
            pass
    response = RedirectResponse(url, status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/", secure=secure, httponly=True, samesite="lax")
    response.delete_cookie(STATE_COOKIE, path="/", secure=secure, httponly=True, samesite="lax")
    return response
