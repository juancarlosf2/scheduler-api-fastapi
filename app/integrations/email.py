"""Compatibility imports; implementation lives in features.notifications.providers.resend."""
from app.features.notifications.providers.resend import EmailDeliveryError, ResendEmailAdapter, booking_email_idempotency_key

__all__ = ['EmailDeliveryError', 'ResendEmailAdapter', 'booking_email_idempotency_key']
