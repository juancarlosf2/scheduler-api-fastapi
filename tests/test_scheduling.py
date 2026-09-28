from datetime import date, datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.availability import BusyInterval, generate_slots
from app.schemas import AvailabilityInput, BookingInput, EventTypeInput, HostCreate, IntervalInput, RescheduleInput
from app.service import DomainError, SchedulerService
from app.storage import create_schema, make_engine, make_session_factory


def test_date_override_replaces_weekly_hours_and_spring_gap_is_skipped():
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    slots = generate_slots(
        now=now, range_start_date=date(2026, 3, 8), range_end_date=date(2026, 3, 8),
        schedule_timezone="America/New_York", duration_minutes=30, slot_interval_minutes=30,
        minimum_notice_minutes=0, booking_window_days=30,
        intervals=[
            IntervalInput(weekday=0, start_minute=540, end_minute=600),
            IntervalInput(date="2026-03-08", start_minute=120, end_minute=240),
        ],
        busy_intervals=[],
    )
    assert [slot.start_at.isoformat() for slot in slots] == [
        "2026-03-08T07:00:00+00:00", "2026-03-08T07:30:00+00:00"
    ]


def test_busy_overlap_blocks_slots():
    slots = generate_slots(
        now=datetime(2026, 3, 1, tzinfo=timezone.utc),
        range_start_date=date(2026, 3, 9), range_end_date=date(2026, 3, 9),
        schedule_timezone="UTC", duration_minutes=30, slot_interval_minutes=30,
        minimum_notice_minutes=0, booking_window_days=30,
        intervals=[IntervalInput(weekday=1, start_minute=540, end_minute=600)],
        busy_intervals=[BusyInterval(datetime(2026, 3, 9, 9, 15, tzinfo=timezone.utc),
                                     datetime(2026, 3, 9, 9, 45, tzinfo=timezone.utc))],
    )
    assert slots == []


def test_action_tokens_are_distinct_scoped_and_rotated(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'scheduler.db'}")
    create_schema(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        service = SchedulerService(session)
        host = service.create_host(HostCreate(
            username="host", display_name="Host", email="host@example.com",
            api_key_hash="a" * 64,
        ))
        target = (datetime.now(timezone.utc) + timedelta(days=4)).date()
        weekday = (target.weekday() + 1) % 7
        service.update_availability(host.id, AvailabilityInput(
            timezone="UTC", intervals=[IntervalInput(weekday=weekday, start_minute=540, end_minute=660)]
        ))
        event = service.create_event_type(host.id, EventTypeInput(
            name="Meeting", location_type="in_person", location_value="Office", minimum_notice_minutes=0,
        ))
        service.publish_event_type(host.id, event.id)
        first, second = service.get_slots("host", event.slug, target, target)[:2]
        result = service.create_booking("host", event.slug, BookingInput(
            start_at=first.start_at, invitee_name="Guest", invitee_email="guest@example.com",
            invitee_timezone="UTC",
        ))
        assert result.cancel_token != result.reschedule_token
        with pytest.raises(DomainError):
            service.cancel_booking(result.booking.id, result.reschedule_token)
        moved = service.reschedule_booking(result.booking.id, result.reschedule_token, RescheduleInput(
            start_at=second.start_at, invitee_timezone="UTC",
        ))
        assert moved.cancel_token != result.cancel_token
        with pytest.raises(DomainError):
            service.cancel_booking(result.booking.id, result.cancel_token)
        assert service.cancel_booking(result.booking.id, moved.cancel_token).status == "canceled"
    engine.dispose()


def test_concurrent_reservations_only_create_one_booking(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'scheduler.db'}")
    create_schema(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        service = SchedulerService(session)
        host = service.create_host(HostCreate(username="host", display_name="Host", email="host@example.com", api_key_hash="b" * 64))
        target = (datetime.now(timezone.utc) + timedelta(days=4)).date()
        weekday = (target.weekday() + 1) % 7
        service.update_availability(host.id, AvailabilityInput(timezone="UTC", intervals=[IntervalInput(weekday=weekday, start_minute=540, end_minute=600)]))
        event = service.create_event_type(host.id, EventTypeInput(name="Meeting", location_type="in_person", location_value="Office", minimum_notice_minutes=0))
        service.publish_event_type(host.id, event.id)
        start = service.get_slots("host", event.slug, target, target)[0].start_at
        slug = event.slug

    barrier = Barrier(2)

    def reserve(email: str) -> int:
        with factory() as session:
            service = SchedulerService(session)
            barrier.wait()
            try:
                service.create_booking("host", slug, BookingInput(start_at=start, invitee_name="Guest", invitee_email=email, invitee_timezone="UTC"))
                return 201
            except DomainError as error:
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(reserve, ["one@example.com", "two@example.com"]))
    assert sorted(statuses) == [201, 409]
    engine.dispose()
