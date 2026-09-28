"""Compatibility imports for the feature module. New code should import app.core.dependencies."""

from app.core.dependencies import bearer_scheme, get_current_host, get_session, session_factory

__all__ = ['bearer_scheme', 'get_current_host', 'get_session', 'session_factory']
