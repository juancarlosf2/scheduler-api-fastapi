"""Shared SQLAlchemy registry and identity/time defaults."""

from __future__ import annotations
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy.orm import DeclarativeBase


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass
