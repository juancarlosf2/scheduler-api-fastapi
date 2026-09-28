"""Host workspace contract, validation, and tenant isolation."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.dependencies import get_session
from app.main import app
from app.models import Booking, Contact, EventType, Host
from app.storage import create_schema, make_engine, make_session_factory
from app.workspace import Workflow


@pytest.fixture
def api(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'workspace.db'}")
    create_schema(engine)
    factory = make_session_factory(engine)

    def session_override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    try:
        with TestClient(app) as client:
            yield client, factory
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def register(client, name):
    response = client.post("/v1/hosts", json={
        "username": name, "display_name": name.title(),
        "email": f"{name}@example.com", "timezone": "UTC",
    })
    assert response.status_code == 201, response.text
    return response.json()


def auth(host):
    return {"Authorization": f"Bearer {host['api_key']}"}


def seed_meeting(factory, owner, *, title="Office Hours", invitee="Invitee"):
    with factory() as session:
        event = session.scalar(select(EventType).where(EventType.host_id == owner["host"]["id"]))
        if event is None:
            raise AssertionError("Create an event type first")
        start = datetime.now(timezone.utc) + timedelta(days=1)
        booking = Booking(
            event_type_id=event.id, host_id=owner["host"]["id"],
            invitee_name=invitee, invitee_email=f"{invitee.lower()}@example.com",
            invitee_timezone="UTC", start_at=start, end_at=start + timedelta(minutes=30),
            cancel_token_hash="a" * 64 if invitee == "Invitee" else "c" * 64,
            reschedule_token_hash="b" * 64 if invitee == "Invitee" else "d" * 64,
        )
        contact = Contact(host_id=owner["host"]["id"], name=invitee,
                          email=f"{invitee.lower()}@example.com")
        session.add_all((booking, contact))
        session.commit()
        return booking.id, contact.id


def event(client, host):
    response = client.post("/v1/hosts/me/event-types", headers=auth(host), json={
        "name": "Office Hours", "location_type": "in_person", "location_value": "Office",
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_profile_settings_are_owned_and_validated(api):
    client, _ = api
    alice = register(client, "alice")
    bob = register(client, "bob")
    assert client.get("/v1/hosts/me/profile-settings").status_code == 401

    path = "/v1/hosts/me/profile-settings"
    original = client.get(path, headers=auth(alice)).json()
    assert original["country_code"] == "DO"
    assert original["email"] == "alice@example.com"
    changed = {key: original[key] for key in (
        "country_code", "date_format", "language", "name", "phone_number",
        "time_format", "timezone", "welcome_message",
    )}
    changed.update(name="  Alice Updated  ", phone_number=" 123456789 ", timezone="America/New_York")
    response = client.put(path, headers=auth(alice), json=changed)
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Alice Updated"
    assert response.json()["phone_number"] == "123456789"
    assert client.get(path, headers=auth(bob)).json()["name"] == "Bob"

    for field, value in (("country_code", "ZZ"), ("date_format", "invalid"),
                         ("name", "   "), ("timezone", "Mars/Base"),
                         ("welcome_message", "x" * 501)):
        invalid = {**changed, field: value}
        assert client.put(path, headers=auth(alice), json=invalid).status_code == 422


def test_meetings_contact_notes_and_csv_are_host_scoped(api):
    client, factory = api
    alice = register(client, "alice")
    bob = register(client, "bob")
    event(client, alice)
    event(client, bob)
    alice_booking, alice_contact = seed_meeting(factory, alice, invitee="Invitee")
    bob_booking, bob_contact = seed_meeting(factory, bob, invitee="Other")

    meetings = client.get("/v1/hosts/me/meetings", headers=auth(alice))
    assert meetings.status_code == 200, meetings.text
    assert [row["id"] for row in meetings.json()] == [alice_booking]
    assert client.get("/v1/hosts/me/meetings", headers=auth(alice), params={"status": "canceled"}).json() == []
    assert client.get("/v1/hosts/me/meetings", headers=auth(alice), params={"event_type_id": event(client, alice)["id"]}).json() == []
    assert client.get("/v1/hosts/me/meetings", headers=auth(alice), params={"range_start": "2020-01-01T00:00:00Z", "range_end": "2019-01-01T00:00:00Z"}).status_code == 400

    assert client.post(f"/v1/hosts/me/meetings/{bob_booking}/cancel", headers=auth(alice)).status_code == 404
    assert client.put(f"/v1/hosts/me/contacts/{bob_contact}/notes", headers=auth(alice), json={"notes": "stolen"}).status_code == 404
    assert client.put(f"/v1/hosts/me/contacts/{alice_contact}/notes", headers=auth(alice), json={"notes": "  Follow up  "}).json()["notes"] == "Follow up"
    assert client.put(f"/v1/hosts/me/contacts/{alice_contact}/notes", headers=auth(alice), json={"notes": "x" * 2001}).status_code == 422
    assert client.put(f"/v1/hosts/me/contacts/{alice_contact}/notes", headers=auth(alice), json={"notes": " "}).json()["notes"] is None

    with factory() as session:
        booking = session.get(Booking, alice_booking)
        booking.invitee_name = '=HYPERLINK("https://example.com")'
        session.commit()
    csv_response = client.get("/v1/hosts/me/meetings.csv", headers=auth(alice))
    assert csv_response.status_code == 200
    assert "'=HYPERLINK" in csv_response.text
    assert "other@example.com" not in csv_response.text
    assert client.post(f"/v1/hosts/me/meetings/{alice_booking}/cancel", headers=auth(alice)).json()["status"] == "canceled"
    assert client.post(f"/v1/hosts/me/meetings/{alice_booking}/cancel", headers=auth(alice)).json()["status"] == "canceled"


def test_onboarding_guide_and_workflows_are_host_scoped(api):
    client, factory = api
    alice = register(client, "alice")
    bob = register(client, "bob")
    setup = client.get("/v1/hosts/me/onboarding", headers=auth(alice))
    assert setup.status_code == 200, setup.text
    assert setup.json()["current_step_id"] == "role"
    assert client.put("/v1/hosts/me/onboarding/step", headers=auth(alice), json={"current_step_id": "missing"}).status_code == 422
    assert client.post("/v1/hosts/me/onboarding/skip", headers=auth(alice), json={"step_id": "role"}).status_code == 400
    assert client.put("/v1/hosts/me/onboarding/role", headers=auth(alice), json={"role": "sales"}).status_code == 200
    assert client.post("/v1/hosts/me/onboarding/skip", headers=auth(alice), json={"step_id": "calendar-usage"}).status_code == 200
    google = client.put("/v1/hosts/me/onboarding/location", headers=auth(alice), json={
        "preferred_location_type": "google_meet", "time_format": "12h",
    })
    assert google.status_code == 200
    assert google.json()["steps"][-1]["state"] == "incomplete"
    assert client.post("/v1/hosts/me/onboarding/complete", headers=auth(alice)).status_code == 400
    assert client.put("/v1/hosts/me/onboarding/location", headers=auth(alice), json={
        "preferred_location_type": "in_person", "preferred_location_value": "  Office  ", "time_format": "12h",
    }).status_code == 200
    assert client.post("/v1/hosts/me/onboarding/complete", headers=auth(alice)).status_code == 200
    guide = client.post("/v1/hosts/me/onboarding/guide/tasks", headers=auth(alice), json={"item_id": "get-to-know"})
    assert guide.status_code == 200, guide.text
    assert guide.json()["completed_task_ids"] == ["get-to-know"]
    assert client.get("/v1/hosts/me/onboarding/guide", headers=auth(bob)).json()["completed_task_ids"] == []
    first_event = guide.json()["first_event"]
    with factory() as session:
        session.add(Workflow(host_id=alice["host"]["id"], event_type_id=first_event["id"],
                             trigger="before_event_start", offset_minutes=60,
                             subject="Reminder", body="See you soon"))
        session.commit()
    assert len(client.get("/v1/hosts/me/workflows", headers=auth(alice)).json()) == 1
    assert client.get("/v1/hosts/me/workflows", headers=auth(bob)).json() == []
    assert client.get("/v1/hosts/me/onboarding", headers=auth(bob)).json()["preferences"]["role"] is None


def test_openapi_describes_workspace_payloads():
    document = app.openapi()
    assert document["paths"]["/v1/hosts/me/profile-settings"]["put"]["requestBody"]
    assert document["paths"]["/v1/hosts/me/meetings"]["get"]["responses"]["200"]["content"]["application/json"]
    assert document["paths"]["/v1/hosts/me/onboarding"]["get"]["responses"]["200"]["content"]["application/json"]
