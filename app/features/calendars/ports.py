"""Provider interface used by calendar settings and booking operations.

Implement this protocol to replace Composio without changing booking rules.
Provider methods must raise a safe CalendarError on expected failures.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol


class CalendarPort(Protocol):
    def find_active_connection(
        self, *, user_id: str
    ) -> dict[str, str | None] | None: ...

    def create_connect_link(
        self, *, user_id: str, callback_url: str | None = None
    ) -> dict[str, str | None]: ...

    def list_calendars(
        self, *, user_id: str, connected_account_id: str
    ) -> list[dict[str, Any]]: ...

    def list_busy_intervals(
        self,
        *,
        user_id: str,
        connected_account_id: str,
        calendar_id: str,
        time_min: datetime,
        time_max: datetime,
    ) -> list[dict[str, Any]]: ...

    def create_event(
        self,
        *,
        user_id: str,
        connected_account_id: str,
        calendar_id: str,
        start_at: datetime,
        end_at: datetime,
        timezone_name: str,
        summary: str,
        attendees: list[str] | None = None,
        description: str | None = None,
        location: str | None = None,
        conference_provider: str | None = None,
    ) -> dict[str, str | None]: ...

    def patch_event(
        self,
        *,
        user_id: str,
        connected_account_id: str,
        calendar_id: str,
        event_id: str,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        timezone_name: str | None = None,
        summary: str | None = None,
        attendees: list[str] | None = None,
        description: str | None = None,
        location: str | None = None,
    ) -> dict[str, str | None]: ...

    def delete_event(
        self,
        *,
        user_id: str,
        connected_account_id: str,
        calendar_id: str,
        event_id: str,
    ) -> None: ...
