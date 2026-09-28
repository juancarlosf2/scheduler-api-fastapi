"""Calendar lifecycle checks use an injected provider, never a network client."""

from datetime import datetime, timedelta, timezone
from threading import Event, Thread

import pytest
from sqlalchemy import select

from app.integrations.calendar import CalendarError
from app.models import Booking, CalendarConnection, ExternalCalendar
from app.schemas import AvailabilityInput, BookingInput, EventTypeInput, HostCreate, IntervalInput, RescheduleInput
from app.service import DomainError, SchedulerService
from app.storage import create_schema, make_engine, make_session_factory


class FakeCalendar:
    def __init__(self):
        self.busy = []
        self.created = []
        self.patched = []
        self.deleted = []
        self.fail_create = False
        self.fail_patch = False
        self.fail_delete = False
        self.interrupt_create = False
        self.interrupt_patch = False
        self.interrupt_delete = False
        self.on_write = None

    def list_busy_intervals(self, **kwargs):
        return list(self.busy)

    def create_event(self, **kwargs):
        if self.on_write:
            self.on_write()
        self.created.append(kwargs)
        if self.interrupt_create:
            raise KeyboardInterrupt
        if self.fail_create:
            raise CalendarError("Calendar write failed")
        return {"event_id": f"event-{len(self.created)}", "meeting_join_url": "https://meet.example/room"}

    def patch_event(self, **kwargs):
        if self.on_write:
            self.on_write()
        self.patched.append(kwargs)
        if self.interrupt_patch:
            raise KeyboardInterrupt
        if self.fail_patch:
            raise CalendarError("Calendar patch failed")
        return {"event_id": kwargs["event_id"], "meeting_join_url": "https://meet.example/room"}

    def delete_event(self, **kwargs):
        if self.on_write:
            self.on_write()
        self.deleted.append(kwargs)
        if self.interrupt_delete:
            raise KeyboardInterrupt
        if self.fail_delete:
            raise CalendarError("Calendar delete failed")


@pytest.fixture
def scheduler(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'calendar-bookings.db'}")
    create_schema(engine)
    factory = make_session_factory(engine)
    fake = FakeCalendar()
    with factory() as session:
        service = SchedulerService(session, calendar_adapter=fake)
        host = service.create_host(HostCreate(
            username="host", display_name="Host", email="host@example.com", api_key_hash="c" * 64,
        ))
        target = (datetime.now(timezone.utc) + timedelta(days=4)).date()
        weekday = (target.weekday() + 1) % 7
        service.update_availability(host.id, AvailabilityInput(
            timezone="UTC", intervals=[IntervalInput(weekday=weekday, start_minute=540, end_minute=660)]
        ))
        connection = CalendarConnection(host_id=host.id, connected_account_id="account-1", status="active")
        connection.calendars = [ExternalCalendar(
            provider_calendar_id="primary", name="Primary", check_conflicts=True, add_events=True,
        )]
        session.add(connection)
        session.commit()
        yield service, fake, host.id, target
    engine.dispose()


def event_and_slots(service, host_id, target, *, location_type="in_person"):
    event = service.create_event_type(host_id, EventTypeInput(
        name="Meeting", location_type=location_type,
        location_value="Office" if location_type == "in_person" else None,
        minimum_notice_minutes=0,
    ))
    service.publish_event_type(host_id, event.id)
    slots = service.get_slots("host", event.slug, target, target)
    return event, slots


def book(service, event, slot):
    return service.create_booking("host", event.slug, BookingInput(
        start_at=slot.start_at, invitee_name="Guest", invitee_email="guest@example.com", invitee_timezone="UTC",
    ))


def test_checked_calendar_busy_blocks_slot_and_booking(scheduler):
    service, fake, host_id, target = scheduler
    event, slots = event_and_slots(service, host_id, target)
    first = slots[0]
    fake.busy = [{"event_id": "external", "start_at": first.start_at,
                  "end_at": first.end_at, "summary": "Other commitment"}]
    assert first.start_at not in {slot.start_at for slot in service.get_slots("host", event.slug, target, target)}
    with pytest.raises(DomainError) as error:
        book(service, event, first)
    assert error.value.status_code == 409


def test_google_meet_requires_active_destination_and_syncs_successfully(scheduler):
    service, fake, host_id, target = scheduler
    event = service.create_event_type(host_id, EventTypeInput(name="Video", location_type="google_meet", minimum_notice_minutes=0))
    connection = service._calendar_connection(host_id)
    connection.calendars[0].add_events = False
    service.session.commit()
    with pytest.raises(DomainError):
        service.publish_event_type(host_id, event.id)
    connection.calendars[0].add_events = True
    service.session.commit()
    service.publish_event_type(host_id, event.id)
    slot = service.get_slots("host", event.slug, target, target)[0]
    result = book(service, event, slot)
    assert result.booking.external_sync_status == "synced"
    assert result.booking.meeting_join_url == "https://meet.example/room"
    assert fake.created[0]["conference_provider"] == "google_meet"
    assert fake.created[0]["calendar_id"] == "primary"
    assert service.session.get(Booking, result.booking.id).external_calendar_id == "primary"


def test_failed_write_keeps_booking_retryable_and_patch_delete_lifecycle(scheduler):
    service, fake, host_id, target = scheduler
    event, slots = event_and_slots(service, host_id, target)
    fake.fail_create = True
    result = book(service, event, slots[0])
    assert result.booking.external_sync_status == "failed"
    assert service.get_booking(result.booking.id, result.cancel_token).status == "scheduled"
    fake.fail_create = False
    with pytest.raises(DomainError) as error:
        service.retry_calendar_sync(result.booking.host_id, result.booking.id)
    assert error.value.status_code == 409
    retried = service.reconcile_calendar_create(result.booking.host_id, result.booking.id, confirmed_absent=True)
    assert retried.external_sync_status == "synced"
    connection = service._calendar_connection(host_id)
    connection.calendars[0].add_events = False
    service.session.commit()
    connection.calendars.append(ExternalCalendar(
        provider_calendar_id="secondary", name="Secondary", check_conflicts=False, add_events=True,
    ))
    service.session.commit()
    fake.fail_patch = True
    moved = service.reschedule_booking(result.booking.id, result.reschedule_token, RescheduleInput(
        start_at=slots[1].start_at, invitee_timezone="UTC",
    ))
    assert moved.booking.external_sync_status == "failed"
    assert fake.patched[0]["event_id"] == "event-2"
    assert fake.patched[0]["calendar_id"] == "primary"
    fake.fail_patch = False
    assert service.retry_calendar_sync(result.booking.host_id, result.booking.id).external_sync_status == "synced"
    fake.fail_delete = True
    canceled = service.cancel_booking(result.booking.id, moved.cancel_token)
    assert canceled.status == "canceled"
    assert canceled.external_sync_status == "failed"
    fake.fail_delete = False
    deleted = service.retry_calendar_sync(result.booking.host_id, result.booking.id)
    assert deleted.external_sync_status == "synced"
    assert fake.deleted[-1]["calendar_id"] == "primary"


def test_pending_create_retries_after_booking_commit_before_provider_call(scheduler, monkeypatch):
    service, fake, host_id, target = scheduler
    event, slots = event_and_slots(service, host_id, target)
    original = service._sync_booking_calendar
    monkeypatch.setattr(service, "_sync_booking_calendar", lambda *args, **kwargs: None)
    result = book(service, event, slots[0])
    booking = service.session.get(Booking, result.booking.id)
    assert booking.external_sync_status == "pending"
    assert booking.external_sync_operation == "create"
    assert booking.external_calendar_id == "primary"
    assert fake.created == []
    monkeypatch.setattr(service, "_sync_booking_calendar", original)
    assert service.retry_calendar_sync(host_id, booking.id).external_sync_status == "synced"
    assert len(fake.created) == 1


def test_interrupted_create_needs_host_reconciliation_without_duplicate(scheduler):
    service, fake, host_id, target = scheduler
    event, slots = event_and_slots(service, host_id, target)

    def assert_inflight_committed():
        with make_session_factory(service.session.bind)() as other:
            persisted = other.scalar(select(Booking).where(Booking.host_id == host_id))
            assert persisted.external_sync_status == "inflight"
            assert persisted.external_sync_operation == "create"
            assert persisted.external_calendar_id == "primary"

    fake.on_write = assert_inflight_committed
    fake.interrupt_create = True
    with pytest.raises(KeyboardInterrupt):
        book(service, event, slots[0])
    booking = service.session.scalar(select(Booking).where(Booking.host_id == host_id))
    with pytest.raises(DomainError) as error:
        service.retry_calendar_sync(host_id, booking.id)
    assert error.value.status_code == 409
    with pytest.raises(DomainError) as error:
        service.reconcile_calendar_create(host_id, booking.id, confirmed_absent=True)
    assert error.value.status_code == 409
    assert len(fake.created) == 1
    fake.interrupt_create = False
    fake.on_write = None
    reconciled = service.reconcile_calendar_create(host_id, booking.id, reconciled_event_id="event-1")
    assert reconciled.external_sync_status == "synced"
    assert len(fake.created) == 1
    assert fake.patched[-1]["event_id"] == "event-1"


def test_interrupted_patch_and_delete_are_retryable(scheduler):
    service, fake, host_id, target = scheduler
    event, slots = event_and_slots(service, host_id, target)
    result = book(service, event, slots[0])
    fake.interrupt_patch = True
    with pytest.raises(KeyboardInterrupt):
        service.reschedule_booking(result.booking.id, result.reschedule_token, RescheduleInput(
            start_at=slots[1].start_at, invitee_timezone="UTC",
        ))
    booking = service.session.get(Booking, result.booking.id)
    assert booking.external_sync_status == "inflight"
    assert booking.external_sync_operation == "patch"
    fake.interrupt_patch = False
    assert service.retry_calendar_sync(host_id, booking.id).external_sync_status == "synced"
    fake.interrupt_delete = True
    with pytest.raises(KeyboardInterrupt):
        service.cancel_booking_as_host(host_id, result.booking.id)
    assert booking.external_sync_status == "inflight"
    assert booking.external_sync_operation == "delete"
    fake.interrupt_delete = False
    assert service.retry_calendar_sync(host_id, booking.id).external_sync_status == "synced"


def test_cancellation_during_create_deletes_the_created_provider_event(scheduler):
    service, fake, host_id, target = scheduler
    event, slots = event_and_slots(service, host_id, target)

    def cancel_while_provider_creates():
        fake.on_write = None
        with make_session_factory(service.session.bind)() as other:
            booking = other.scalar(select(Booking).where(Booking.host_id == host_id))
            SchedulerService(other, calendar_adapter=fake).cancel_booking_as_host(host_id, booking.id)

    fake.on_write = cancel_while_provider_creates
    result = book(service, event, slots[0])
    assert result.booking.status == "canceled"
    assert result.booking.external_sync_status == "synced"
    assert len(fake.created) == 1
    assert fake.deleted[-1]["event_id"] == "event-1"


def test_late_old_patch_reapplies_latest_booking_times(scheduler):
    service, fake, host_id, target = scheduler
    event, slots = event_and_slots(service, host_id, target)
    result = book(service, event, slots[0])

    def finish_newer_patch_first():
        fake.on_write = None
        with make_session_factory(service.session.bind)() as other:
            booking = other.get(Booking, result.booking.id)
            booking.start_at = slots[2].start_at
            booking.end_at = slots[2].end_at
            booking.external_sync_status = "pending"
            booking.external_sync_operation = "patch"
            other.commit()
            SchedulerService(other, calendar_adapter=fake).retry_calendar_sync(host_id, booking.id)

    fake.on_write = finish_newer_patch_first
    moved = service.reschedule_booking(result.booking.id, result.reschedule_token, RescheduleInput(
        start_at=slots[1].start_at, invitee_timezone="UTC",
    ))
    assert moved.booking.external_sync_status == "synced"
    assert len(fake.patched) == 3
    assert fake.patched[0]["start_at"] == slots[2].start_at
    assert fake.patched[1]["start_at"] == slots[1].start_at
    assert fake.patched[2]["start_at"] == slots[2].start_at


def test_old_patch_completion_reapplies_while_new_patch_response_is_pending(scheduler):
    service, fake, host_id, target = scheduler
    event, slots = event_and_slots(service, host_id, target)
    result = book(service, event, slots[0])
    newer_written = Event()
    finish_newer_response = Event()
    calls = []
    errors = []
    worker = None

    def patch_event(**kwargs):
        nonlocal worker
        calls.append(kwargs)
        if len(calls) == 1:
            with make_session_factory(service.session.bind)() as other:
                booking = other.get(Booking, result.booking.id)
                booking.start_at = slots[2].start_at
                booking.end_at = slots[2].end_at
                booking.external_sync_status = "pending"
                booking.external_sync_operation = "patch"
                other.commit()

            def run_newer_patch():
                try:
                    with make_session_factory(service.session.bind)() as other:
                        SchedulerService(other, calendar_adapter=fake).retry_calendar_sync(host_id, result.booking.id)
                except BaseException as error:
                    errors.append(error)

            worker = Thread(target=run_newer_patch)
            worker.start()
            assert newer_written.wait(timeout=5)
        elif len(calls) == 2:
            newer_written.set()
            assert finish_newer_response.wait(timeout=5)
        return {"event_id": kwargs["event_id"], "meeting_join_url": "https://meet.example/room"}

    fake.patch_event = patch_event
    try:
        moved = service.reschedule_booking(result.booking.id, result.reschedule_token, RescheduleInput(
            start_at=slots[1].start_at, invitee_timezone="UTC",
        ))
    finally:
        finish_newer_response.set()
        if worker is not None:
            worker.join(timeout=5)
    assert not errors
    assert worker is not None and not worker.is_alive()
    assert moved.booking.external_sync_status == "synced"
    assert [call["start_at"] for call in calls] == [
        slots[1].start_at, slots[2].start_at, slots[2].start_at,
    ]
