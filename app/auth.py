"""Host API key helpers.

The raw key is returned at registration and is never stored in the database.
"""

import hashlib
import os
import secrets

from cryptography.fernet import Fernet


def create_api_key() -> str:
    return secrets.token_urlsafe(32)


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def auth_mode() -> str:
    """Choose an auth mode without silently downgrading partial WorkOS setup."""
    mode = os.getenv("APP_AUTH_MODE", "").strip().lower()
    workos_names = (
        "WORKOS_API_KEY", "WORKOS_CLIENT_ID", "WORKOS_COOKIE_PASSWORD", "WORKOS_REDIRECT_URI"
    )
    configured = [bool(os.getenv(name)) for name in workos_names]
    production = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "")).lower() == "production"
    if mode not in ("", "local", "workos"):
        raise RuntimeError("Invalid authentication configuration")
    if mode == "local":
        if production or any(configured):
            raise RuntimeError("Invalid authentication configuration")
        return "local"
    if mode == "workos" or any(configured) or production:
        if not all(configured):
            raise RuntimeError("Invalid authentication configuration")
        try:
            Fernet(os.environ["WORKOS_COOKIE_PASSWORD"].encode("ascii"))
        except (ValueError, TypeError):
            raise RuntimeError("Invalid authentication configuration") from None
        return "workos"
    raise RuntimeError("Authentication mode must be configured")
