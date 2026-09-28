"""Enable local authentication explicitly for tests that exercise the local starter."""

import pytest


@pytest.fixture(autouse=True)
def local_auth_mode(monkeypatch):
    monkeypatch.setenv("APP_AUTH_MODE", "local")
