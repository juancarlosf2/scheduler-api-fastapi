"""Calendar settings keep host choices isolated without contacting Composio."""

import pytest
from fastapi.testclient import TestClient

from app.calendar_routes import get_calendar_adapter
from app.dependencies import get_session
from app.integrations.calendar import CalendarConnectionExpired
from app.main import app
from app.storage import create_schema, make_engine, make_session_factory


class FakeCalendarAdapter:
    def __init__(self):
        self.active = None
        self.expired = False
        self.calendars = [
            {"id": "primary", "name": "Primary", "time_zone": "UTC", "is_primary": True},
            {"id": "other", "name": "Other", "time_zone": "UTC", "is_primary": False},
        ]
        self.calls = []

    def find_active_connection(self, *, user_id):
        self.calls.append(("find", user_id))
        return self.active

    def create_connect_link(self, *, user_id, callback_url=None):
        self.calls.append(("link", user_id, callback_url))
        return {"connected_account_id": f"account-{user_id}", "redirect_url": "https://example.com/oauth", "status": "initiated"}

    def list_calendars(self, *, user_id, connected_account_id):
        self.calls.append(("list", user_id, connected_account_id))
        if self.expired:
            raise CalendarConnectionExpired()
        return self.calendars


@pytest.fixture
def setup(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'calendar.db'}")
    create_schema(engine)
    factory = make_session_factory(engine)
    adapter = FakeCalendarAdapter()

    def test_session():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_calendar_adapter] = lambda: adapter
    try:
        with TestClient(app) as client:
            yield client, adapter
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def host(client, username):
    response = client.post("/v1/hosts", json={
        "username": username,
        "display_name": username,
        "email": f"{username}@example.com",
    })
    assert response.status_code == 201, response.text
    body = response.json()
    return body["host"]["id"], {"Authorization": f"Bearer {body['api_key']}"}


def test_connect_sync_and_preferences_are_host_scoped(setup):
    client, adapter = setup
    alice_id, alice_auth = host(client, "alice")
    _, bob_auth = host(client, "bob")

    assert client.get("/v1/hosts/me/calendar").status_code == 401
    assert client.get("/v1/hosts/me/calendar", headers=alice_auth).json() is None
    assert adapter.calls == []
    missing = client.post("/v1/hosts/me/calendar/sync", headers=alice_auth)
    assert missing.status_code == 400

    link = client.post("/v1/hosts/me/calendar/connect-link", headers=alice_auth, json={
        "callback_url": "https://example.com/return"
    })
    assert link.status_code == 200, link.text
    assert link.json() == {"redirect_url": "https://example.com/oauth", "status": "initiated"}
    assert ("link", alice_id, "https://example.com/return") in adapter.calls

    synced = client.post("/v1/hosts/me/calendar/sync", headers=alice_auth)
    assert synced.status_code == 200, synced.text
    assert synced.json()["status"] == "active"
    calendars = synced.json()["calendars"]
    assert sum(calendar["add_events"] for calendar in calendars) == 1
    assert next(calendar for calendar in calendars if calendar["provider_calendar_id"] == "primary")["add_events"]
    assert all(calendar["check_conflicts"] for calendar in calendars)
    assert client.get("/v1/hosts/me/calendar", headers=bob_auth).json() is None

    other = next(calendar for calendar in calendars if calendar["provider_calendar_id"] == "other")
    changed = client.put(f"/v1/hosts/me/calendar/calendars/{other['id']}/preferences", headers=alice_auth, json={
        "check_conflicts": False, "add_events": True
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()["check_conflicts"] is False
    assert changed.json()["add_events"] is True
    bob_link = client.post("/v1/hosts/me/calendar/connect-link", headers=bob_auth, json={})
    assert bob_link.status_code == 200
    assert client.post("/v1/hosts/me/calendar/sync", headers=bob_auth).status_code == 200
    assert client.put(f"/v1/hosts/me/calendar/calendars/{other['id']}/preferences", headers=bob_auth, json={
        "check_conflicts": True, "add_events": False
    }).status_code == 404

    resynced = client.post("/v1/hosts/me/calendar/sync", headers=alice_auth)
    assert resynced.status_code == 200, resynced.text
    calendars = resynced.json()["calendars"]
    assert sum(calendar["add_events"] for calendar in calendars) == 1
    assert next(calendar for calendar in calendars if calendar["provider_calendar_id"] == "other")["add_events"]
    assert next(calendar for calendar in calendars if calendar["provider_calendar_id"] == "other")["check_conflicts"] is False

    adapter.calendars = [adapter.calendars[0]]
    removed = client.post("/v1/hosts/me/calendar/sync", headers=alice_auth)
    assert removed.status_code == 200, removed.text
    assert len(removed.json()["calendars"]) == 1
    assert removed.json()["calendars"][0]["add_events"] is True


def test_active_connection_and_expired_sync(setup):
    client, adapter = setup
    host_id, headers = host(client, "host")
    adapter.active = {
        "connected_account_id": "already-linked", "provider_account_email": "host@gmail.com", "status": "ACTIVE"
    }
    response = client.post("/v1/hosts/me/calendar/connect-link", headers=headers, json={})
    assert response.status_code == 200, response.text
    assert response.json() == {"redirect_url": None, "status": "ACTIVE"}
    assert not any(call[0] == "link" for call in adapter.calls)
    assert client.get("/v1/hosts/me/calendar", headers=headers).json()["provider_account_email"] == "host@gmail.com"

    adapter.expired = True
    failed = client.post("/v1/hosts/me/calendar/sync", headers=headers)
    assert failed.status_code == 400, failed.text
    assert failed.json()["detail"] == "Reconnect Google Calendar before syncing calendars"
    assert client.get("/v1/hosts/me/calendar", headers=headers).json()["status"] == "expired"


def test_settings_work_without_provider_configuration(setup, monkeypatch):
    client, _ = setup
    _, headers = host(client, "local")
    app.dependency_overrides.pop(get_calendar_adapter)
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    assert client.get("/v1/hosts/me/calendar", headers=headers).json() is None
    unavailable = client.post("/v1/hosts/me/calendar/connect-link", headers=headers, json={})
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"] == "Google Calendar is not configured"
