"""Compatibility imports for the feature module. New code should import app.core.database."""

from app.core.database import create_schema, make_engine, make_session_factory

__all__ = ['create_schema', 'make_engine', 'make_session_factory']
