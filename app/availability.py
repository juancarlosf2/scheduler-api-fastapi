"""Availability validation and timezone aware slot generation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .schemas import IntervalInput, Slot


class AvailabilityError(ValueError):
    pass


@dataclass(frozen=True)
class BusyInterval:
    start_at: datetime
    end_at: datetime


def validate_intervals(intervals: list[IntervalInput]) -> None:
    scopes: dict[tuple[str, int | str], list[IntervalInput]] = {}
    for interval in intervals:
        key = ("date", interval.date) if interval.date is not None else ("weekday", interval.weekday)
        scopes.setdefault(key, []).append(interval)
    for scoped in scopes.values():
        ordered = sorted(scoped, key=lambda interval: interval.start_minute)
        if any(left.end_minute > right.start_minute for left, right in zip(ordered, ordered[1:])):
            raise AvailabilityError("Availability intervals cannot overlap")


def _local_to_utc(day: date, minute: int, zone: ZoneInfo) -> datetime | None:
    local = datetime.combine(day, time.min) + timedelta(minutes=minute)
    candidate = local.replace(tzinfo=zone, fold=0).astimezone(timezone.utc)
    # A spring-forward wall time has no corresponding instant.
    if candidate.astimezone(zone).replace(tzinfo=None) != local:
        return None
    return candidate


def generate_slots(
    *,
    now: datetime,
    range_start_date: date,
    range_end_date: date,
    schedule_timezone: str,
    duration_minutes: int,
    slot_interval_minutes: int,
    minimum_notice_minutes: int,
    booking_window_days: int,
    intervals: list[IntervalInput],
    busy_intervals: list[BusyInterval],
) -> list[Slot]:
    if range_start_date > range_end_date:
        raise AvailabilityError("Date range start must be before the end")
    if duration_minutes < 1 or slot_interval_minutes < 1 or minimum_notice_minutes < 0 or booking_window_days < 1:
        raise AvailabilityError("Slot settings are invalid")
    validate_intervals(intervals)
    zone = ZoneInfo(schedule_timezone)
    current = now.astimezone(timezone.utc)
    earliest = current + timedelta(minutes=minimum_notice_minutes)
    local_today = current.astimezone(zone).date()
    final_day = local_today + timedelta(days=booking_window_days)
    first = max(range_start_date, earliest.astimezone(zone).date())
    last = min(range_end_date, final_day)
    if first > last:
        raise AvailabilityError("Date range is outside the booking window")
    latest_exclusive = _local_to_utc(final_day + timedelta(days=1), 0, zone)
    if latest_exclusive is None:
        raise AvailabilityError("Booking window boundary is invalid")
    slots: list[Slot] = []
    day = first
    while day <= last:
        date_intervals = [interval for interval in intervals if interval.date == day.isoformat()]
        day_intervals = date_intervals if date_intervals else [interval for interval in intervals if interval.weekday == (day.weekday() + 1) % 7 and interval.date is None]
        for interval in day_intervals:
            if not interval.is_available:
                continue
            minute = interval.start_minute
            while minute + duration_minutes <= interval.end_minute:
                start = _local_to_utc(day, minute, zone)
                minute += slot_interval_minutes
                if start is None:
                    continue
                end = start + timedelta(minutes=duration_minutes)
                if start < earliest or start >= latest_exclusive:
                    continue
                if any(start < busy.end_at and busy.start_at < end for busy in busy_intervals):
                    continue
                slots.append(Slot(start_at=start, end_at=end, local_date=day.isoformat()))
        day += timedelta(days=1)
    return slots
