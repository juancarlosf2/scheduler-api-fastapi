"""Compatibility imports for the feature module. New code should import app.core.auth."""

from app.core.auth import auth_mode, create_api_key, hash_api_key

__all__ = ['auth_mode', 'create_api_key', 'hash_api_key']
