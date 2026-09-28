"""HTTP contract checks for credential binding and public booking."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_session
from app.main import app
from app.storage import create_schema, make_engine, make_session_factory


@pytest.fixture
def client(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'scheduler-test.db'}")
    create_schema(engine)
    session_factory = make_session_factory(engine)

    def test_session():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = test_session
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def register(client, username):
    response = client.post(
        "/v1/hosts",
        json={
            "username": username,
            "display_name": username.title(),
            "email": f"{username}@example.com",
            "timezone": "UTC",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert "api_key_hash" not in body["host"]
    return body


def auth(key):
    return {"Authorization": f"Bearer {key}"}


def test_host_credentials_are_bound_to_one_host(client):
    alice = register(client, "alice")
    bob = register(client, "bob")

    assert alice["api_key"] != bob["api_key"]
    assert client.get("/v1/hosts/me").status_code == 401
    assert client.get("/v1/hosts/me", headers=auth("invalid")).status_code == 401
    assert client.get("/v1/hosts/me", headers=auth(alice["api_key"])).json()["id"] == alice["host"]["id"]
    assert client.get("/v1/hosts/me", headers=auth(bob["api_key"])).json()["id"] == bob["host"]["id"]

    created = client.post(
        "/v1/hosts/me/event-types",
        headers=auth(alice["api_key"]),
        json={"name": "Office hours", "location_type": "in_person", "location_value": "Office"},
    )
    assert created.status_code == 201, created.text
    event_id = created.json()["id"]
    assert [event["id"] for event in client.get("/v1/hosts/me/event-types", headers=auth(bob["api_key"])).json()] == []

    forbidden = client.put(
        f"/v1/hosts/me/event-types/{event_id}",
        headers=auth(bob["api_key"]),
        json={"name": "Stolen"},
    )
    assert forbidden.status_code == 404


def test_second_booking_for_same_slot_conflicts(client):
    host = register(client, "host")
    headers = auth(host["api_key"])
    target = (datetime.now(timezone.utc) + timedelta(days=3)).date()
    weekday = (target.weekday() + 1) % 7  # Scheduler uses Sunday=0.

    schedule = client.put(
        "/v1/hosts/me/availability",
        headers=headers,
        json={
            "name": "Weekday hours",
            "timezone": "UTC",
            "intervals": [{"weekday": weekday, "start_minute": 540, "end_minute": 1020}],
        },
    )
    assert schedule.status_code == 200, schedule.text
    assert schedule.json()["intervals"][0]["weekday"] == weekday
    created = client.post(
        "/v1/hosts/me/event-types",
        headers=headers,
        json={
            "name": "Consultation",
            "location_type": "in_person",
            "location_value": "Office",
            "minimum_notice_minutes": 0,
        },
    )
    assert created.status_code == 201, created.text
    event_id = created.json()["id"]
    published = client.post(f"/v1/hosts/me/event-types/{event_id}/publish", headers=headers)
    assert published.status_code == 200, published.text
    slug = published.json()["slug"]

    slots = client.get(
        f"/v1/public/host/events/{slug}/slots",
        params={"range_start_date": target.isoformat(), "range_end_date": target.isoformat()},
    )
    assert slots.status_code == 200, slots.text
    assert slots.json(), "expected a bookable slot"
    start_at = slots.json()[0]["start_at"]

    def book(email):
        return client.post(
            f"/v1/public/host/events/{slug}/bookings",
            json={
                "start_at": start_at,
                "invitee_name": "Guest",
                "invitee_email": email,
                "invitee_timezone": "UTC",
            },
        )

    first = book("first@example.com")
    assert first.status_code == 201, first.text
    assert first.json()["cancel_token"]
    assert "cancel_token_hash" not in first.text
    second = book("second@example.com")
    assert second.status_code == 409, second.text

    booking_id = first.json()["booking"]["id"]
    bad_cancel = client.post(
        f"/v1/bookings/{booking_id}/cancel", json={"token": "x" * 32}
    )
    assert bad_cancel.status_code in (403, 404)
    canceled = client.post(
        f"/v1/bookings/{booking_id}/cancel",
        json={"token": first.json()["cancel_token"]},
    )
    assert canceled.status_code == 200, canceled.text
    assert canceled.json()["status"] == "canceled"
    assert book("second@example.com").status_code == 201
