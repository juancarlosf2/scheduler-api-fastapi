"""Database URL configuration checks that never open a remote connection."""

import pytest

from app.storage import make_engine


@pytest.mark.parametrize("scheme", ["postgresql", "postgres", "postgresql+psycopg"])
def test_neon_connection_url_uses_psycopg3_and_preserves_options(scheme):
    url = (
        f"{scheme}://example:encoded%40password@ep-example-pooler.us-east-1.aws.neon.tech/"
        "neondb?sslmode=require&channel_binding=require"
    )
    engine = make_engine(url)
    try:
        assert engine.url.drivername == "postgresql+psycopg"
        assert engine.url.host == "ep-example-pooler.us-east-1.aws.neon.tech"
        assert engine.url.password == "encoded@password"
        assert engine.url.query["sslmode"] == "require"
        assert engine.url.query["channel_binding"] == "require"
        assert engine.pool._pre_ping is True
    finally:
        engine.dispose()


def test_sqlite_retains_its_local_driver(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'scheduler.db'}")
    try:
        assert engine.url.drivername == "sqlite"
        assert engine.pool._pre_ping is False
    finally:
        engine.dispose()
