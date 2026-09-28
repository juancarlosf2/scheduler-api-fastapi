"""Transport interfaces for notification delivery and signed webhooks."""

from __future__ import annotations

from typing import Any, Mapping, Protocol


class EmailSender(Protocol):
    def send(
        self,
        *,
        to: str,
        subject: str,
        text: str,
        html: str | None = None,
        idempotency_key: str | None = None,
        tags: list[dict[str, str]] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> str: ...


class EmailWebhookVerifier(Protocol):
    def verify_webhook(
        self, *, raw_payload: str, headers: Mapping[str, str]
    ) -> Any: ...
