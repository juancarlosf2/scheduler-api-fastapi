"""Database and authenticated-host dependencies for HTTP handlers."""

from collections.abc import Iterator
from functools import lru_cache

from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import auth_mode, hash_api_key
from app.models import Host
from app.storage import create_schema, make_engine, make_session_factory


bearer_scheme = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def session_factory():
    engine = make_engine()
    create_schema(engine)
    return make_session_factory(engine)


def get_session() -> Iterator[Session]:
    with session_factory() as session:
        yield session


def get_current_host(
    request: Request,
    response: Response,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: Session = Depends(get_session),
) -> Host:
    try:
        mode = auth_mode()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Authentication is not configured") from None
    if mode == "workos":
        from app.workos_auth import authenticated_workos_user

        user_id = authenticated_workos_user(request, response)
        host = session.scalar(select(Host).where(Host.workos_user_id == user_id))
        if host is None:
            raise HTTPException(status_code=403, detail="Host profile unavailable")
        return host
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Host API key required")

    key_hash = hash_api_key(credentials.credentials)
    host = session.scalar(select(Host).where(Host.api_key_hash == key_hash))
    if host is None:
        raise HTTPException(status_code=401, detail="Invalid host API key")
    return host
