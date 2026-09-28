"""AuthKit callback, session binding, and fail-closed configuration tests."""

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.dependencies import get_session
from app.main import app
from app.models import Host
from app.storage import create_schema, make_engine, make_session_factory
from workos.session import unseal_data


class FakeUser:
    def __init__(self, user_id, email):
        self.id = user_id
        self.email = email
        self.first_name = email.split("@", 1)[0].title()
        self.last_name = None

    def to_dict(self):
        return {"id": self.id, "email": self.email, "first_name": self.first_name}


class FakeSession:
    def __init__(self, cookie, password):
        self.cookie = cookie
        self.password = password

    def authenticate(self):
        try:
            data = unseal_data(self.cookie, self.password)
        except Exception:
            return SimpleNamespace(authenticated=False, user=None)
        return SimpleNamespace(authenticated=True, user=data["user"])

    def get_logout_url(self):
        return "https://api.workos.com/logout/test"


class FakeUserManagement:
    def __init__(self, password):
        self.password = password
        self.users = {
            "alice-code": FakeUser("user_alice", "alice@example.com"),
            "bob-code": FakeUser("user_bob", "bob@example.com"),
        }

    def get_authorization_url(self, *, provider, redirect_uri, state):
        assert provider == "authkit"
        return f"https://workos.example/authorize?state={state}"

    def authenticate_with_code(self, *, code):
        user = self.users[code]
        return SimpleNamespace(user=user, access_token="access", refresh_token="refresh", impersonator=None)

    def load_sealed_session(self, *, session_data, cookie_password):
        assert cookie_password == self.password
        return FakeSession(session_data, cookie_password)


@pytest.fixture
def client(tmp_path, monkeypatch):
    password = Fernet.generate_key().decode()
    for name, value in {
        "APP_AUTH_MODE": "workos",
        "WORKOS_API_KEY": "sk_test",
        "WORKOS_CLIENT_ID": "client_test",
        "WORKOS_COOKIE_PASSWORD": password,
        "WORKOS_REDIRECT_URI": "http://localhost:8000/v1/auth/callback",
        "WORKOS_POST_LOGIN_URL": "/docs",
    }.items():
        monkeypatch.setenv(name, value)
    engine = make_engine(f"sqlite:///{tmp_path / 'workos.db'}")
    create_schema(engine)
    factory = make_session_factory(engine)

    def test_session():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = test_session
    fake = FakeUserManagement(password)
    monkeypatch.setattr("app.workos_auth._client", lambda: SimpleNamespace(user_management=fake))
    try:
        with TestClient(app, base_url="http://localhost:8000") as test_client:
            yield test_client, factory, fake
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def sign_in(client, code):
    login = client.get("/v1/auth/login", follow_redirects=False)
    assert login.status_code == 302
    assert "httponly" in login.headers["set-cookie"].lower()
    assert "samesite=lax" in login.headers["set-cookie"].lower()
    state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    callback = client.get(f"/v1/auth/callback?code={code}&state={state}", follow_redirects=False)
    assert callback.status_code == 302
    assert callback.headers["location"] == "/docs"
    return client.get("/v1/hosts/me").json()


def test_callback_state_binding_and_host_isolation(client):
    http, factory, _ = client
    assert http.get("/v1/auth/callback?code=alice-code&state=forged").status_code == 400
    assert http.get("/v1/hosts/me", headers={"Authorization": "Bearer arbitrary"}).status_code == 401
    assert http.post("/v1/hosts", json={"username": "other", "display_name": "Other", "email": "other@example.com"}).status_code == 403
    alice = sign_in(http, "alice-code")
    assert alice["email"] == "alice@example.com"
    assert sign_in(http, "alice-code")["id"] == alice["id"]
    bob = sign_in(http, "bob-code")
    assert bob["id"] != alice["id"]
    assert http.get("/v1/hosts/me", headers={"Authorization": "Bearer arbitrary"}).json()["id"] == bob["id"]
    with factory() as db:
        hosts = db.scalars(select(Host)).all()
        assert {h.workos_user_id for h in hosts} == {"user_alice", "user_bob"}
        assert all(h.schedule is not None for h in hosts)
    http.cookies.set("wos_session", "tampered")
    assert http.get("/v1/hosts/me").status_code == 401


def test_email_collision_does_not_link_workos_accounts(client):
    http, _, fake = client
    alice = sign_in(http, "alice-code")
    fake.users["bob-code"].email = "alice@example.com"
    login = http.get("/v1/auth/login", follow_redirects=False)
    state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    collision = http.get(f"/v1/auth/callback?code=bob-code&state={state}", follow_redirects=False)
    assert collision.status_code == 409
    assert http.get("/v1/hosts/me").json()["id"] == alice["id"]


def test_logout_requires_origin_and_clears_cookie(client):
    http, _, _ = client
    sign_in(http, "alice-code")
    assert http.post("/v1/auth/logout").status_code == 403
    result = http.post("/v1/auth/logout", headers={"Origin": "http://localhost:8000"}, follow_redirects=False)
    assert result.status_code == 303
    assert result.headers["location"].startswith("https://api.workos.com/logout/")
    assert http.get("/v1/hosts/me").status_code == 401


def test_host_mutations_require_exact_origin_in_cookie_mode(client):
    http, _, _ = client
    sign_in(http, "alice-code")
    payload = {"name": "Hours", "timezone": "UTC", "intervals": []}
    url = "/v1/hosts/me/availability"
    assert http.put(url, json=payload).status_code == 403
    assert http.put(url, json=payload, headers={"Origin": "http://evil.localhost:8000"}).status_code == 403
    accepted = http.put(url, json=payload, headers={"Origin": "http://localhost:8000"})
    assert accepted.status_code == 200, accepted.text
    # Public booking validation is independent of host cookie origin checks.
    assert http.post("/v1/public/alice/events/unknown/bookings", json={}).status_code == 422


def test_partial_workos_config_and_production_local_mode_fail_closed(client, monkeypatch):
    http, _, _ = client
    monkeypatch.delenv("WORKOS_COOKIE_PASSWORD")
    assert http.get("/v1/hosts/me", headers={"Authorization": "Bearer anything"}).status_code == 503
    assert http.post("/v1/hosts", json={"username": "new", "display_name": "New", "email": "new@example.com"}).status_code == 503
    for name in ("WORKOS_API_KEY", "WORKOS_CLIENT_ID", "WORKOS_REDIRECT_URI"):
        monkeypatch.delenv(name)
    monkeypatch.setenv("APP_AUTH_MODE", "local")
    monkeypatch.setenv("APP_ENV", "production")
    assert http.get("/v1/hosts/me").status_code == 503


def test_authentication_requires_explicit_mode(client, monkeypatch):
    http, _, _ = client
    for name in ("WORKOS_API_KEY", "WORKOS_CLIENT_ID", "WORKOS_COOKIE_PASSWORD", "WORKOS_REDIRECT_URI", "APP_ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("APP_AUTH_MODE", raising=False)
    assert http.get("/v1/hosts/me").status_code == 503
    assert http.post("/v1/hosts", json={"username": "new", "display_name": "New", "email": "new@example.com"}).status_code == 503


def test_production_cookie_security_and_redirect_config(client, monkeypatch):
    http, _, _ = client
    monkeypatch.setenv("WORKOS_REDIRECT_URI", "https://scheduler.example/v1/auth/callback")
    login = http.get("/v1/auth/login", follow_redirects=False)
    assert login.status_code == 302
    assert "secure" in login.headers["set-cookie"].lower()
    monkeypatch.setenv("WORKOS_POST_LOGIN_URL", "https://evil.example/")
    assert http.get("/v1/auth/login").status_code == 503
