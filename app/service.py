"""Compatibility imports for the feature module. New code should import app.features.bookings.service."""

from app.features.bookings.service import SchedulerService
from app.core.errors import DomainError

__all__ = ['SchedulerService', 'DomainError']
