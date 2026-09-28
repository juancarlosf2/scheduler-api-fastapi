"""Compatibility imports; implementation lives in features.notifications.api."""
from app.features.notifications.api import DeliveryView, get_email_adapter, _view, list_booking_emails, retry_booking_email, resend_webhook, router

__all__ = ['DeliveryView', 'get_email_adapter', '_view', 'list_booking_emails', 'retry_booking_email', 'resend_webhook', 'router']
