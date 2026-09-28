"""Availability SQLAlchemy tables."""

from __future__ import annotations
from typing import TYPE_CHECKING
from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.model import Base, new_id

if TYPE_CHECKING:
    from app.features.hosts.models import Host


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    host_id: Mapped[str] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    name: Mapped[str] = mapped_column(
        String(120), nullable=False, default="Default hours"
    )
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, default="UTC")
    host: Mapped[Host] = relationship(back_populates="schedule")
    intervals: Mapped[list[AvailabilityInterval]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan"
    )


class AvailabilityInterval(Base):
    __tablename__ = "availability_intervals"
    __table_args__ = (
        CheckConstraint("(weekday IS NULL) != (date IS NULL)", name="interval_scope"),
        CheckConstraint(
            "weekday IS NULL OR weekday BETWEEN 0 AND 6", name="interval_weekday"
        ),
        CheckConstraint(
            "start_minute >= 0 AND end_minute <= 1440 AND start_minute < end_minute",
            name="interval_minutes",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_id: Mapped[str] = mapped_column(
        ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False
    )
    weekday: Mapped[int | None] = mapped_column(Integer)
    date: Mapped[str | None] = mapped_column(String(10))
    start_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    end_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    schedule: Mapped[Schedule] = relationship(back_populates="intervals")
