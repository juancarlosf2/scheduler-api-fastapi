"""Persisted email, host isolation, and provider-free webhook checks."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import hash_api_key
from app.dependencies import get_session
from app.integrations.email import EmailDeliveryError
from app.models import Base, Booking, EventType, Host, Schedule, utc_now
from app.notification_routes import get_email_adapter, router
from app.notifications import (
    EmailDelivery, ResendWebhookEvent, dispatch_booking_emails, dispatch_due_booking_emails,
    enqueue_booking_emails, process_verified_webhook, refresh_queued_booking_emails,
)
from app.storage import make_engine, make_session_factory


@pytest.fixture
def environment(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'notifications.db'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    api = FastAPI()
    api.include_router(router)

    def test_session():
        with factory() as session:
            yield session

    api.dependency_overrides[get_session] = test_session
    with factory() as session:
        for username in ("alice", "bob"):
            host = Host(username=username, display_name=username.title(), email=f"{username}@example.com",
                        timezone="UTC", api_key_hash=hash_api_key(f"{username}-key"))
            schedule = Schedule(host=host, name="Hours", timezone="UTC")
            session.add(schedule)
            session.flush()
            event = EventType(host_id=host.id, schedule_id=schedule.id, name="<Planning & Review>",
                              slug="planning", location_type="in_person", location_value="Office")
            session.add(event)
            session.flush()
            booking = Booking(
                host_id=host.id, event_type_id=event.id, invitee_name="<Guest>",
                invitee_email=f"{username}-guest@example.com", invitee_timezone="UTC",
                start_at=datetime(2026, 10, 1, 14, tzinfo=timezone.utc),
                end_at=datetime(2026, 10, 1, 14, 30, tzinfo=timezone.utc),
                status="scheduled", cancel_token_hash=f"cancel-{username}",
                reschedule_token_hash=f"reschedule-{username}",
            )
            session.add(booking)
            session.flush()
            enqueue_booking_emails(session, booking.id, "confirmed")
        session.commit()
    try:
        with TestClient(api) as client:
            yield SimpleNamespace(client=client, factory=factory, api=api)
    finally:
        engine.dispose()


def _delivery(session, recipient="invitee", host="alice"):
    return session.scalar(
        select(EmailDelivery).join(Booking, EmailDelivery.booking_id == Booking.id)
        .join(Host, Booking.host_id == Host.id)
        .where(Host.username == host, EmailDelivery.recipient_type == recipient)
    )


def test_outbox_is_idempotent_and_renders_escaped_snapshots(environment):
    with environment.factory() as session:
        delivery = _delivery(session)
        booking_id = delivery.booking_id
        before = (delivery.id, delivery.subject, delivery.html_body)
        assert "&lt;Planning &amp; Review&gt;" in delivery.html_body
        assert "&lt;Guest&gt;" not in delivery.html_body  # Invitee sees the host name.
        enqueue_booking_emails(session, booking_id, "confirmed")
        session.commit()
        rows = session.scalars(select(EmailDelivery).where(EmailDelivery.booking_id == booking_id)).all()
        assert len(rows) == 2
        assert (delivery.id, delivery.subject, delivery.html_body) == before
        assert {row.recipient_type for row in rows} == {"host", "invitee"}
        assert "&lt;Guest&gt;" in _delivery(session, "host").html_body


def test_calendar_link_refreshes_only_queued_messages(environment):
    with environment.factory() as session:
        queued = _delivery(session)
        attempted = _delivery(session, "host")
        original_key = queued.idempotency_key
        attempted.status = "sent"
        booking = session.get(Booking, queued.booking_id)
        booking.meeting_join_url = "https://meet.example/a?x=1&y=2"
        refreshed = refresh_queued_booking_emails(session, booking.id)
        session.commit()
        assert [row.id for row in refreshed] == [queued.id]
        assert queued.idempotency_key == original_key
        assert "https://meet.example/a?x=1&amp;y=2" in queued.html_body
        assert "Join:" not in attempted.text_body


def test_reschedule_occurrences_distinguish_return_to_earlier_slot(environment):
    with environment.factory() as session:
        booking = session.get(Booking, _delivery(session).booking_id)
        first_start = booking.start_at.replace(tzinfo=timezone.utc)
        second_start = first_start + timedelta(days=1)
        booking.reschedule_token_hash = "first-rotated-token-hash"
        first = enqueue_booking_emails(session, booking.id, "rescheduled", start_at=first_start)
        repeated = enqueue_booking_emails(session, booking.id, "rescheduled", start_at=first_start)
        assert [row.id for row in repeated] == [row.id for row in first]

        booking.start_at = second_start
        booking.end_at += timedelta(days=1)
        booking.reschedule_token_hash = "second-rotated-token-hash"
        second = enqueue_booking_emails(session, booking.id, "rescheduled", start_at=second_start)

        booking.start_at = first_start
        booking.end_at -= timedelta(days=1)
        booking.reschedule_token_hash = "third-rotated-token-hash"
        third = enqueue_booking_emails(session, booking.id, "rescheduled", start_at=first_start)
        booking.meeting_join_url = "https://meet.example/return"
        refreshed = refresh_queued_booking_emails(session, booking.id)
        session.commit()
        assert {row.id for row in refreshed} == {row.id for row in third}
        assert all("meet.example/return" not in row.text_body for row in first + second)
        assert all("meet.example/return" in row.text_body for row in third)
        assert len({first[0].id, second[0].id, third[0].id}) == 3
        assert len({first[0].idempotency_key, second[0].idempotency_key, third[0].idempotency_key}) == 3
        assert len(session.scalars(select(EmailDelivery).where(
            EmailDelivery.booking_id == booking.id, EmailDelivery.lifecycle == "rescheduled",
        )).all()) == 6


def test_due_recovery_dispatches_queued_failed_and_stale_processing(environment):
    with environment.factory() as session:
        queued = _delivery(session, "host", "alice")
        failed_due = _delivery(session, "invitee", "alice")
        failed_future = _delivery(session, "host", "bob")
        stale = _delivery(session, "invitee", "bob")
        failed_due.status = "failed"
        failed_due.next_attempt_at = utc_now() - timedelta(minutes=1)
        failed_future.status = "failed"
        failed_future.next_attempt_at = utc_now() + timedelta(hours=1)
        stale.status = "processing"
        stale.next_attempt_at = utc_now() - timedelta(minutes=1)
        session.commit()
        sent = Mock(side_effect=["email-queued", "email-failed", "email-stale"])
        recovered = dispatch_due_booking_emails(session, adapter=SimpleNamespace(send=sent))
        assert {row.id for row in recovered} == {queued.id, failed_due.id, stale.id}
        assert sent.call_count == 3
        assert {row.status for row in recovered} == {"sent"}
        session.refresh(failed_future)
        assert failed_future.status == "failed"
        assert failed_future.attempt_count == 0
        assert dispatch_due_booking_emails(session, adapter=SimpleNamespace(send=sent)) == []


def test_host_can_list_and_recover_queued_delivery(environment):
    with environment.factory() as session:
        delivery = _delivery(session)
        booking_id, delivery_id = delivery.booking_id, delivery.id
    list_url = f"/v1/hosts/me/bookings/{booking_id}/email-deliveries"
    assert environment.client.get(list_url, headers={"Authorization": "Bearer bob-key"}).status_code == 404
    listed = environment.client.get(list_url, headers={"Authorization": "Bearer alice-key"})
    assert listed.status_code == 200, listed.text
    assert {row["recipient_type"] for row in listed.json()} == {"host", "invitee"}
    assert all(row["status"] == "queued" for row in listed.json())

    adapter = SimpleNamespace(send=Mock(return_value="email-recovered"))
    environment.api.dependency_overrides[get_email_adapter] = lambda: adapter
    retry_url = f"{list_url}/{delivery_id}/retry"
    assert environment.client.post(retry_url, headers={"Authorization": "Bearer bob-key"}).status_code == 404
    response = environment.client.post(retry_url, headers={"Authorization": "Bearer alice-key"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "sent"
    assert adapter.send.call_count == 1
    assert environment.client.post(retry_url, headers={"Authorization": "Bearer alice-key"}).status_code == 409


def test_host_can_recover_stale_processing_delivery(environment):
    with environment.factory() as session:
        delivery = _delivery(session)
        delivery.status = "processing"
        delivery.next_attempt_at = utc_now() - timedelta(minutes=1)
        booking_id, delivery_id = delivery.booking_id, delivery.id
        session.commit()
    adapter = SimpleNamespace(send=Mock(return_value="email-recovered-stale"))
    environment.api.dependency_overrides[get_email_adapter] = lambda: adapter
    retry_url = f"/v1/hosts/me/bookings/{booking_id}/email-deliveries/{delivery_id}/retry"
    assert environment.client.post(retry_url, headers={"Authorization": "Bearer bob-key"}).status_code == 404
    response = environment.client.post(retry_url, headers={"Authorization": "Bearer alice-key"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "sent"
    assert adapter.send.call_count == 1


def test_dispatch_failure_is_retryable_and_hides_provider_error(environment):
    bad_adapter = SimpleNamespace(send=Mock(side_effect=RuntimeError("secret provider token")))
    with environment.factory() as session:
        delivery = _delivery(session)
        dispatch_booking_emails(session, delivery.booking_id, adapter=bad_adapter)
        session.refresh(delivery)
        assert delivery.status == "failed"
        assert delivery.attempt_count == 1
        assert "secret" not in delivery.last_error
        first_key = delivery.idempotency_key
        first_snapshot = delivery.text_body
        delivery_id = delivery.id
        booking_id = delivery.booking_id

    good_adapter = SimpleNamespace(send=Mock(return_value="email-accepted"))
    environment.api.dependency_overrides[get_email_adapter] = lambda: good_adapter
    denied = environment.client.post(
        f"/v1/hosts/me/bookings/{booking_id}/email-deliveries/{delivery_id}/retry",
        headers={"Authorization": "Bearer bob-key"},
    )
    assert denied.status_code == 404
    response = environment.client.post(
        f"/v1/hosts/me/bookings/{booking_id}/email-deliveries/{delivery_id}/retry",
        headers={"Authorization": "Bearer alice-key"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "sent"
    assert response.json()["attempt_count"] == 2
    assert "secret provider token" not in response.text
    assert good_adapter.send.call_args.kwargs["idempotency_key"] == first_key
    assert good_adapter.send.call_args.kwargs["text"] == first_snapshot
    assert environment.client.post(
        f"/v1/hosts/me/bookings/{booking_id}/email-deliveries/{delivery_id}/retry",
        headers={"Authorization": "Bearer alice-key"},
    ).status_code == 409


def test_verified_webhook_updates_once_and_forged_webhook_is_rejected(environment):
    with environment.factory() as session:
        delivery = _delivery(session)
        delivery.status = "sent"
        delivery.resend_message_id = "email-1"
        session.commit()
        delivery_id = delivery.id
    adapter = SimpleNamespace(verify_webhook=Mock(return_value={
        "type": "email.delivered", "created_at": "2026-10-01T15:00:00Z",
        "data": {"email_id": "email-1"},
    }))
    environment.api.dependency_overrides[get_email_adapter] = lambda: adapter
    headers = {"svix-id": "event-1", "svix-timestamp": "123", "svix-signature": "v1,valid"}
    response = environment.client.post("/v1/webhooks/resend", content=b'{"event":"raw"}', headers=headers)
    assert response.status_code == 200, response.text
    assert adapter.verify_webhook.call_args.kwargs["raw_payload"] == '{"event":"raw"}'
    assert environment.client.post("/v1/webhooks/resend", content=b'{"event":"raw"}', headers=headers).status_code == 200
    with environment.factory() as session:
        delivery = session.get(EmailDelivery, delivery_id)
        assert delivery.status == "delivered"
        assert delivery.last_event_id == "event-1"
        assert len(session.scalars(select(ResendWebhookEvent)).all()) == 1

    adapter.verify_webhook.side_effect = EmailDeliveryError("secret signing key")
    forged = environment.client.post("/v1/webhooks/resend", content=b"forged", headers={
        "svix-id": "event-forged", "svix-timestamp": "123", "svix-signature": "invalid",
    })
    assert forged.status_code == 400
    assert "secret" not in forged.text
    with environment.factory() as session:
        assert len(session.scalars(select(ResendWebhookEvent)).all()) == 1


def test_verified_webhook_before_send_response_is_reconciled(environment):
    with environment.factory() as session:
        delivery = _delivery(session)
        process_verified_webhook(session, "event-early", {
            "type": "email.delivered", "created_at": "2026-10-01T15:00:00Z",
            "data": {"email_id": "email-early"},
        })
        assert session.get(EmailDelivery, delivery.id).status == "queued"
        dispatch_booking_emails(session, delivery.booking_id, adapter=SimpleNamespace(
            send=Mock(side_effect=lambda **args: "email-early" if args["to"].endswith("guest@example.com") else "email-host"),
        ))
        session.refresh(delivery)
        assert delivery.status == "delivered"
        assert delivery.last_event_id == "event-early"
