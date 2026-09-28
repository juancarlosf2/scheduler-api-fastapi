"""Compatibility imports for the feature module. New code should import app.features.availability.slots."""

from app.features.availability.slots import AvailabilityError, BusyInterval, generate_slots, validate_intervals

__all__ = ['AvailabilityError', 'BusyInterval', 'generate_slots', 'validate_intervals']
