"""Best-effort delivery after a booking's durable transaction has committed."""

from __future__ import annotations

import logging
import os

from sqlalchemy.orm import Session

from app.notifications import dispatch_booking_emails, refresh_queued_booking_emails


logger = logging.getLogger(__name__)


def deliver_booking_notifications(session: Session, booking_id: str) -> None:
    """Update queued content after calendar sync, then send if Resend is configured.

    Booking success must survive a notification failure. The outbox remains
    available through the host retry endpoint.
    """
    try:
        refresh_queued_booking_emails(session, booking_id)
        session.commit()
        if os.getenv("RESEND_API_KEY") and os.getenv("RESEND_FROM_EMAIL"):
            dispatch_booking_emails(session, booking_id)
    except Exception as exc:
        session.rollback()
        logger.warning("Booking notification delivery deferred: %s", type(exc).__name__)
