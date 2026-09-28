"""Host-owned email retry and public, signature-verified Resend webhook routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dependencies import get_current_host, get_session
from app.integrations.email import EmailDeliveryError, ResendEmailAdapter
from app.models import Booking, Host, utc_now
from app.notifications import EmailDelivery, _utc, dispatch_delivery, process_verified_webhook


router = APIRouter(tags=["notifications"])


class DeliveryView(BaseModel):
    id: str
    booking_id: str
    recipient_type: str
    lifecycle: str
    to_email: str
    status: str
    attempt_count: int
    next_attempt_at: str | None
    sent_at: str | None
    delivered_at: str | None
    failed_at: str | None
    last_error: str | None


def get_email_adapter() -> ResendEmailAdapter:
    """Dependency override point for alternate transports and provider-free tests."""
    try:
        return ResendEmailAdapter()
    except EmailDeliveryError:
        raise HTTPException(status_code=503, detail="Email delivery is not configured") from None


def _view(delivery: EmailDelivery) -> DeliveryView:
    return DeliveryView(
        id=delivery.id, booking_id=delivery.booking_id,
        recipient_type=delivery.recipient_type, lifecycle=delivery.lifecycle,
        to_email=delivery.to_email, status=delivery.status,
        attempt_count=delivery.attempt_count,
        next_attempt_at=delivery.next_attempt_at.isoformat() if delivery.next_attempt_at else None,
        sent_at=delivery.sent_at.isoformat() if delivery.sent_at else None,
        delivered_at=delivery.delivered_at.isoformat() if delivery.delivered_at else None,
        failed_at=delivery.failed_at.isoformat() if delivery.failed_at else None,
        last_error=delivery.last_error,
    )


@router.get("/v1/hosts/me/bookings/{booking_id}/email-deliveries", response_model=list[DeliveryView])
def list_booking_emails(
    booking_id: str,
    host: Host = Depends(get_current_host),
    session: Session = Depends(get_session),
) -> list[DeliveryView]:
    booking = session.scalar(select(Booking.id).where(Booking.id == booking_id, Booking.host_id == host.id))
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    deliveries = session.scalars(select(EmailDelivery).where(
        EmailDelivery.booking_id == booking_id,
    ).order_by(EmailDelivery.created_at, EmailDelivery.id)).all()
    return [_view(delivery) for delivery in deliveries]


@router.post("/v1/hosts/me/bookings/{booking_id}/email-deliveries/{delivery_id}/retry", response_model=DeliveryView)
def retry_booking_email(
    booking_id: str, delivery_id: str,
    host: Host = Depends(get_current_host),
    session: Session = Depends(get_session),
    adapter: ResendEmailAdapter = Depends(get_email_adapter),
) -> DeliveryView:
    delivery = session.scalar(
        select(EmailDelivery).join(Booking, EmailDelivery.booking_id == Booking.id).where(
            EmailDelivery.id == delivery_id,
            EmailDelivery.booking_id == booking_id,
            Booking.host_id == host.id,
        )
    )
    if delivery is None:
        raise HTTPException(status_code=404, detail="Email delivery not found")
    if delivery.status not in {"queued", "failed"} and not (
        delivery.status == "processing" and delivery.next_attempt_at is not None
        and _utc(delivery.next_attempt_at) <= utc_now()
    ):
        raise HTTPException(status_code=409, detail="Email delivery is not retryable")
    result = dispatch_delivery(session, delivery_id, adapter=adapter, force=True)
    return _view(result)


@router.post("/v1/webhooks/resend", include_in_schema=True)
async def resend_webhook(
    request: Request,
    session: Session = Depends(get_session),
    adapter: ResendEmailAdapter = Depends(get_email_adapter),
) -> dict[str, str]:
    try:
        raw_payload = (await request.body()).decode("utf-8")
        event = adapter.verify_webhook(raw_payload=raw_payload, headers=request.headers)
    except (EmailDeliveryError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid email webhook") from None
    event_id = request.headers.get("svix-id")
    if not event_id or not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="Invalid email webhook")
    try:
        process_verified_webhook(session, event_id, event)
    except ValueError:
        session.rollback()
        raise HTTPException(status_code=400, detail="Invalid email webhook") from None
    return {"status": "ok"}
