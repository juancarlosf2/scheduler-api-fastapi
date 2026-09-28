"""Host-owned Google Calendar connection and calendar preferences.

Provider credentials live in Composio. This module stores only the connected
account identifier and the host's choices for synced calendars.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.features.calendars.providers.composio import (
    CalendarConnectionExpired,
    CalendarConnectionRequiresReconnect,
    CalendarError,
)
from app.features.calendars.ports import CalendarPort
from app.features.calendars.models import CalendarConnection
from app.features.calendars.models import ExternalCalendar
from app.core.errors import DomainError


class CalendarService:
    def __init__(self, session: Session):
        self.session = session

    def _connection(self, host_id: str) -> CalendarConnection | None:
        return self.session.scalar(
            select(CalendarConnection)
            .options(selectinload(CalendarConnection.calendars))
            .where(
                CalendarConnection.host_id == host_id,
                CalendarConnection.provider == "google",
            )
        )

    def settings(self, host_id: str) -> CalendarConnection | None:
        """An unconnected host has no settings yet."""
        return self._connection(host_id)

    def _commit(self) -> None:
        try:
            self.session.commit()
        except IntegrityError as error:
            self.session.rollback()
            raise DomainError("Calendar settings conflict", 409) from error

    def _upsert_connection(
        self,
        host_id: str,
        connected_account_id: str,
        email: str | None,
        status: str | None,
    ) -> CalendarConnection:
        connection = self._connection(host_id)
        if connection is None:
            connection = CalendarConnection(
                host_id=host_id, connected_account_id=connected_account_id
            )
            self.session.add(connection)
        elif connection.connected_account_id != connected_account_id:
            # Choices from another provider account must never be used for this account.
            connection.calendars.clear()
        connection.connected_account_id = connected_account_id
        connection.provider_account_email = email
        connection.status = (status or "active").lower()
        self._commit()
        return connection

    def create_connect_link(
        self,
        host_id: str,
        adapter: CalendarPort,
        callback_url: str | None = None,
    ) -> dict[str, str | None]:
        try:
            active = adapter.find_active_connection(user_id=host_id)
            if active is not None:
                self._upsert_connection(
                    host_id,
                    active["connected_account_id"],
                    active.get("provider_account_email"),
                    active.get("status"),
                )
                return {
                    "redirect_url": None,
                    "status": active.get("status") or "active",
                }

            link = adapter.create_connect_link(
                user_id=host_id, callback_url=callback_url
            )
        except CalendarError as error:
            raise DomainError(str(error), 502) from error

        self._upsert_connection(
            host_id,
            link["connected_account_id"],
            None,
            link.get("status") or "initiated",
        )
        return {
            "redirect_url": link.get("redirect_url"),
            "status": link.get("status") or "initiated",
        }

    def sync(self, host_id: str, adapter: CalendarPort) -> CalendarConnection:
        connection = self._connection(host_id)
        if connection is None:
            raise DomainError("Connect Google Calendar before syncing calendars")

        try:
            calendars = adapter.list_calendars(
                user_id=host_id, connected_account_id=connection.connected_account_id
            )
        except (
            CalendarConnectionExpired,
            CalendarConnectionRequiresReconnect,
        ) as error:
            connection.status = "expired"
            self._commit()
            raise DomainError(
                "Reconnect Google Calendar before syncing calendars"
            ) from error
        except CalendarError as error:
            raise DomainError(str(error), 502) from error

        preferences = {
            calendar.provider_calendar_id: (
                calendar.check_conflicts,
                calendar.add_events,
            )
            for calendar in connection.calendars
        }
        # An existing destination remains selected if it is in the new provider list.
        synced_ids = {calendar["id"] for calendar in calendars}
        destination_exists = any(
            add_events and provider_id in synced_ids
            for provider_id, (_, add_events) in preferences.items()
        )
        first_primary_id = next(
            (item["id"] for item in calendars if item["is_primary"]), None
        )

        connection.calendars.clear()
        self.session.flush()  # Remove old rows before inserting into unique provider/destination indexes.
        for item in calendars:
            provider_id = item["id"]
            check_conflicts, add_events = preferences.get(provider_id, (True, False))
            connection.calendars.append(
                ExternalCalendar(
                    provider_calendar_id=provider_id,
                    name=item["name"],
                    time_zone=item.get("time_zone"),
                    is_primary=item["is_primary"],
                    check_conflicts=check_conflicts,
                    add_events=add_events
                    or (not destination_exists and provider_id == first_primary_id),
                )
            )
        connection.status = "active"
        self._commit()
        return connection

    def update_preferences(
        self, host_id: str, calendar_id: str, *, check_conflicts: bool, add_events: bool
    ) -> ExternalCalendar:
        connection = self._connection(host_id)
        if connection is None:
            raise DomainError("Connect Google Calendar before updating calendars")
        calendar = next(
            (item for item in connection.calendars if item.id == calendar_id), None
        )
        if calendar is None:
            raise DomainError("Calendar not found", 404)

        if add_events:
            for other in connection.calendars:
                if other.add_events and other.id != calendar_id:
                    other.add_events = False
            self.session.flush()  # Satisfy the one-destination index during the switch.
        calendar.check_conflicts = check_conflicts
        calendar.add_events = add_events
        self._commit()
        return calendar
