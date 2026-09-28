"""Distinct one-time public action tokens and UTC normalization."""

from __future__ import annotations
import hashlib
import secrets
from datetime import datetime, timezone


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _action_tokens() -> tuple[str, str]:
    cancel_token = secrets.token_urlsafe(32)
    reschedule_token = secrets.token_urlsafe(32)
    while reschedule_token == cancel_token:
        reschedule_token = secrets.token_urlsafe(32)
    return cancel_token, reschedule_token
