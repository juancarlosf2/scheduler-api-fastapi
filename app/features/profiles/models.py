"""Profiles persistence."""

from __future__ import annotations
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from app.core.model import Base


class HostWorkspace(Base):
    __tablename__ = "host_workspaces"

    host_id: Mapped[str] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), primary_key=True
    )
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="DO")
    date_format: Mapped[str] = mapped_column(
        String(12), nullable=False, default="DD/MM/YYYY"
    )
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    time_format: Mapped[str] = mapped_column(String(3), nullable=False, default="24h")
    phone_number: Mapped[str | None] = mapped_column(String(40))
    current_step_id: Mapped[str] = mapped_column(
        String(30), nullable=False, default="role"
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    role: Mapped[str | None] = mapped_column(String(30))
    preferred_location_type: Mapped[str | None] = mapped_column(String(30))
    preferred_location_value: Mapped[str | None] = mapped_column(String(240))
    calendar_connection_skipped: Mapped[bool] = mapped_column(
        default=False, nullable=False
    )
