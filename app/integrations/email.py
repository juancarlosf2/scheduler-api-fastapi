"""Optional Resend transport for rendered transactional emails."""

from __future__ import annotations

import os
import hashlib
from datetime import datetime, timezone
from typing import Any, Mapping


class EmailDeliveryError(Exception):
    """Safe error for callers; provider details must not enter API responses."""


def booking_email_idempotency_key(
    booking_id: str, lifecycle: str, recipient: str, start_at: datetime | None = None,
    *, occurrence_id: str | None = None,
) -> str:
    """Keep a key stable for one lifecycle occurrence and its provider retries."""
    if lifecycle not in {"confirmed", "rescheduled", "canceled"} or recipient not in {"host", "invitee"}:
        raise ValueError("Invalid booking email lifecycle or recipient")
    prefix = "host-" if recipient == "host" else ""
    if lifecycle == "confirmed":
        return f"{prefix}booking-confirmation/{booking_id}"
    occurrence = ""
    if lifecycle == "rescheduled":
        if start_at is None or start_at.tzinfo is None or start_at.utcoffset() is None:
            raise ValueError("Rescheduled email requires a timezone-aware start_at")
        iso = start_at.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        occurrence = f"/{iso}"
        if occurrence_id is not None:
            occurrence += f"/{hashlib.sha256(occurrence_id.encode()).hexdigest()[:32]}"
    return f"{prefix}booking-{lifecycle}/{booking_id}{occurrence}"


class ResendEmailAdapter:
    """Send already-rendered email through Resend with optional idempotency."""

    def __init__(self, client: Any | None = None, *, from_email: str | None = None) -> None:
        self.from_email = from_email or os.getenv("RESEND_FROM_EMAIL")
        if not self.from_email:
            raise EmailDeliveryError("Email sender is not configured")
        if client is None:
            api_key = os.getenv("RESEND_API_KEY")
            if not api_key:
                raise EmailDeliveryError("Email delivery is not configured")
            try:
                import resend
            except ImportError as error:
                raise EmailDeliveryError("Resend integration is not installed") from error
            resend.api_key = api_key
            client = resend
        self.client = client

    def send(
        self, *, to: str, subject: str, text: str, html: str | None = None,
        idempotency_key: str | None = None, tags: list[dict[str, str]] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> str:
        payload: dict[str, Any] = {"from": self.from_email, "to": to, "subject": subject, "text": text}
        if html is not None:
            payload["html"] = html
        if tags is not None:
            payload["tags"] = tags
        if attachments is not None:
            payload["attachments"] = attachments
        try:
            if idempotency_key is not None:
                result = self.client.Emails.send(payload, idempotency_key=idempotency_key)
            else:
                result = self.client.Emails.send(payload)
        except Exception as error:
            raise EmailDeliveryError("Failed to send email") from error
        if isinstance(result, Mapping):
            result = result.get("data", result)
            message_id = result.get("id") if isinstance(result, Mapping) else None
        else:
            message_id = getattr(result, "id", None)
        if not isinstance(message_id, str) or not message_id:
            raise EmailDeliveryError("Failed to send email")
        return message_id

    def verify_webhook(self, *, raw_payload: str, headers: Mapping[str, str]) -> Any:
        """Return the verified event; callers must pass the unchanged request body."""
        secret = os.getenv("RESEND_WEBHOOK_SECRET")
        if not secret:
            raise EmailDeliveryError("Email webhook is not configured")
        normalized = {key.lower(): value for key, value in headers.items()}
        if not all(normalized.get(key) for key in ("svix-id", "svix-timestamp", "svix-signature")):
            raise EmailDeliveryError("Invalid email webhook")
        try:
            return self.client.Webhooks.verify({
                "payload": raw_payload,
                "headers": {"id": normalized["svix-id"], "timestamp": normalized["svix-timestamp"],
                            "signature": normalized["svix-signature"]},
                "webhook_secret": secret,
            })
        except Exception as error:
            raise EmailDeliveryError("Invalid email webhook") from error
