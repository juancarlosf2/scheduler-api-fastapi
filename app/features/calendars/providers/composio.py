"""Google Calendar through Composio's pinned direct tool execution API."""

from __future__ import annotations

import os
import re
from datetime import date, datetime, time, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


GOOGLE_CALENDAR_TOOLKIT_VERSION = "20260429_00"
_TOOLKIT = "googlecalendar"
_AUTH_CONFIG_NAME = "Scheduler Google Calendar OAuth v2"
_OAUTH_REDIRECT_URI = "https://backend.composio.dev/api/v3.1/toolkits/auth/callback"
_SCOPES = " ".join(
    (
        "https://www.googleapis.com/auth/calendar.calendars.readonly",
        "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
        "https://www.googleapis.com/auth/calendar.events",
    )
)


class CalendarError(Exception):
    """Safe, user-facing calendar integration failure."""


class CalendarConnectionExpired(CalendarError):
    def __init__(self) -> None:
        super().__init__("Google Calendar connection expired")


class CalendarConnectionRequiresReconnect(CalendarError):
    def __init__(self) -> None:
        super().__init__("Google Calendar connection requires reconnect")


def _value(obj: Any, *keys: str) -> Any:
    for key in keys:
        value = obj.get(key) if isinstance(obj, Mapping) else getattr(obj, key, None)
        if value is not None:
            return value
    return None


def _string(obj: Any, *keys: str) -> str | None:
    value = _value(obj, *keys)
    return value if isinstance(value, str) and value.strip() else None


def _required(obj: Any, *keys: str) -> str:
    value = _string(obj, *keys)
    if value is None:
        raise CalendarError("Google Calendar response was missing expected data")
    return value


def _data(response: Any) -> Any:
    data = _value(response, "data") or response
    return _value(data, "response_data") or data


def _collection(response: Any) -> list[Any]:
    data = _data(response)
    if isinstance(data, list):
        return data
    for key in (
        "items",
        "accounts",
        "connected_accounts",
        "connectedAccounts",
        "calendars",
        "events",
    ):
        value = _value(data, key)
        if isinstance(value, list):
            return value
    return []


def _error_text(error: Any) -> str:
    if isinstance(error, str):
        return error
    return " ".join(
        str(value)
        for value in (
            _value(error, "message", "error", "detail", "code", "status"),
            _value(error, "details", "errors"),
            _value(_value(error, "cause"), "message", "code", "status"),
        )
        if value is not None
    )


def _map_error(error: Any) -> CalendarError:
    raw = _error_text(error) or str(error)
    if re.search(r"connected account", raw, re.I) and re.search(
        r"expired|inactive|410", raw, re.I
    ):
        return CalendarConnectionExpired()
    if re.search(
        r"insufficient (authentication )?scopes|insufficient permission|access_token_scope_insufficient",
        raw,
        re.I,
    ):
        return CalendarConnectionRequiresReconnect()
    return CalendarError("Google Calendar action failed")


def _is_not_found(error: Any) -> bool:
    raw = _error_text(error) or str(error)
    return re.search(r"\b404\b|\bnot found\b", raw, re.I) is not None


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CalendarError("Calendar times must include a timezone offset")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _calendar_time(value: datetime, zone: str | None) -> str:
    if zone is None:
        return _iso(value)
    if value.tzinfo is None or value.utcoffset() is None:
        raise CalendarError("Calendar times must include a timezone offset")
    try:
        return value.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%dT%H:%M:%S")
    except ZoneInfoNotFoundError as error:
        raise CalendarError("Calendar timezone is invalid") from error


def _event_date(value: Any, zone: str | None = None) -> datetime | None:
    text = _string(value, "dateTime", "date_time")
    if text is None:
        day = _string(value, "date")
        if day is None:
            return None
        if zone is None:
            raise CalendarError("Google Calendar event timezone is missing")
        try:
            return datetime.combine(date.fromisoformat(day), time.min, ZoneInfo(zone))
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise CalendarError(
                "Google Calendar event date or timezone is invalid"
            ) from error
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _event_page(response: Any) -> tuple[list[Any], str | None, str | None]:
    data = _data(response)
    if not isinstance(data, Mapping):
        raise CalendarError("Google Calendar events response was invalid")
    events = _value(data, "items", "events")
    if not isinstance(events, list):
        raise CalendarError("Google Calendar events response was invalid")
    token = _value(data, "nextPageToken", "next_page_token")
    if token is not None and (not isinstance(token, str) or not token.strip()):
        raise CalendarError("Google Calendar events pagination was invalid")
    return events, token, _string(data, "timeZone", "time_zone")


def _event_result(response: Any) -> dict[str, str | None]:
    data = _data(response)
    meeting_url = _string(
        data, "hangoutLink", "hangout_link", "meetingJoinUrl", "meeting_join_url"
    )
    if meeting_url is None:
        conference = _value(data, "conferenceData", "conference_data")
        points = _value(conference, "entryPoints", "entry_points")
        if isinstance(points, list):
            for point in points:
                if _string(point, "entryPointType", "entry_point_type") == "video":
                    meeting_url = _string(point, "uri")
                    break
    return {
        "event_id": _required(data, "id", "event_id"),
        "html_link": _string(data, "htmlLink", "html_link"),
        "meeting_join_url": meeting_url,
    }


def _compact(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


class GoogleCalendarAdapter:
    """Call from a host-scoped service with its stable user and account IDs."""

    def __init__(self, client: Any | None = None) -> None:
        if client is None:
            api_key = os.getenv("COMPOSIO_API_KEY")
            if not api_key:
                raise CalendarError("Google Calendar is not configured")
            try:
                from composio import Composio
            except ImportError as error:
                raise CalendarError("Composio integration is not installed") from error
            client = Composio(
                api_key=api_key,
                toolkit_versions={_TOOLKIT: GOOGLE_CALENDAR_TOOLKIT_VERSION},
            )
        self.client = client

    def _execute(
        self,
        slug: str,
        *,
        user_id: str,
        connected_account_id: str,
        arguments: dict[str, Any],
        allow_not_found: bool = False,
    ) -> Any:
        try:
            response = self.client.tools.execute(
                slug,
                arguments=arguments,
                connected_account_id=connected_account_id,
                user_id=user_id,
                version=GOOGLE_CALENDAR_TOOLKIT_VERSION,
            )
        except Exception as error:
            if allow_not_found and _is_not_found(error):
                return None
            raise _map_error(error) from error
        if _value(response, "successful") is not True:
            error = _value(response, "error") or "Tool execution was unsuccessful"
            if allow_not_found and _is_not_found(error):
                return None
            raise _map_error(error)
        return response

    def list_calendars(
        self, *, user_id: str, connected_account_id: str
    ) -> list[dict[str, Any]]:
        response = self._execute(
            "GOOGLECALENDAR_LIST_CALENDARS",
            user_id=user_id,
            connected_account_id=connected_account_id,
            arguments={"max_results": 250, "show_deleted": False, "show_hidden": False},
        )
        return [
            {
                "id": _required(item, "id"),
                "is_primary": _value(item, "primary") is True,
                "name": _required(item, "summary", "name"),
                "time_zone": _string(item, "timeZone", "time_zone"),
            }
            for item in _collection(response)
        ]

    def list_busy_intervals(
        self,
        *,
        user_id: str,
        connected_account_id: str,
        calendar_id: str,
        time_min: datetime,
        time_max: datetime,
    ) -> list[dict[str, Any]]:
        intervals = []
        arguments = {
            "calendarId": calendar_id,
            "timeMin": _iso(time_min),
            "timeMax": _iso(time_max),
            "singleEvents": True,
            "orderBy": "startTime",
        }
        seen_tokens: set[str] = set()
        calendar_zone = None
        while True:
            response = self._execute(
                "GOOGLECALENDAR_EVENTS_LIST",
                user_id=user_id,
                connected_account_id=connected_account_id,
                arguments=arguments,
            )
            events, next_token, page_zone = _event_page(response)
            calendar_zone = page_zone or calendar_zone
            for event in events:
                if _string(event, "status") == "cancelled":
                    continue
                start_value = _value(event, "start")
                end_value = _value(event, "end")
                event_zone = (
                    _string(start_value, "timeZone", "time_zone")
                    or _string(end_value, "timeZone", "time_zone")
                    or calendar_zone
                )
                start = _event_date(start_value, event_zone)
                end = _event_date(end_value, event_zone)
                if _string(start_value, "date") or _string(end_value, "date"):
                    if start is None or end is None or end <= start:
                        raise CalendarError("Google Calendar all-day event was invalid")
                if start and end:
                    intervals.append(
                        {
                            "event_id": _required(event, "id"),
                            "summary": _string(event, "summary"),
                            "start_at": start,
                            "end_at": end,
                        }
                    )
            if next_token is None:
                break
            if next_token in seen_tokens:
                raise CalendarError("Google Calendar events pagination was invalid")
            seen_tokens.add(next_token)
            arguments = {**arguments, "pageToken": next_token}
        return intervals

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
    ) -> dict[str, str | None]:
        if end_at <= start_at:
            raise CalendarError("Calendar event end time must be after start time")
        minutes = round((end_at - start_at).total_seconds() / 60)
        response = self._execute(
            "GOOGLECALENDAR_CREATE_EVENT",
            user_id=user_id,
            connected_account_id=connected_account_id,
            arguments=_compact(
                attendees=attendees,
                calendar_id=calendar_id,
                create_meeting_room=conference_provider == "google_meet",
                description=description,
                event_duration_hour=minutes // 60 or None,
                event_duration_minutes=minutes % 60,
                location=location,
                send_updates="all",
                start_datetime=_calendar_time(start_at, timezone_name),
                summary=summary,
                timezone=timezone_name,
            ),
        )
        return _event_result(response)

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
    ) -> dict[str, str | None]:
        if start_at and end_at and end_at <= start_at:
            raise CalendarError("Calendar event end time must be after start time")
        response = self._execute(
            "GOOGLECALENDAR_PATCH_EVENT",
            user_id=user_id,
            connected_account_id=connected_account_id,
            arguments=_compact(
                attendees=attendees,
                calendar_id=calendar_id,
                description=description,
                end_time=_calendar_time(end_at, timezone_name) if end_at else None,
                event_id=event_id,
                location=location,
                send_updates="all",
                start_time=_calendar_time(start_at, timezone_name)
                if start_at
                else None,
                summary=summary,
                timezone=timezone_name,
            ),
        )
        return _event_result(response)

    def delete_event(
        self,
        *,
        user_id: str,
        connected_account_id: str,
        calendar_id: str,
        event_id: str,
    ) -> None:
        self._execute(
            "GOOGLECALENDAR_DELETE_EVENT",
            user_id=user_id,
            connected_account_id=connected_account_id,
            arguments={"calendar_id": calendar_id, "event_id": event_id},
            allow_not_found=True,
        )

    def _auth_config_id(self) -> str:
        config_name = os.getenv("COMPOSIO_GOOGLE_AUTH_CONFIG_NAME", _AUTH_CONFIG_NAME)
        try:
            configs = self.client.auth_configs.list({"toolkit": _TOOLKIT})
        except Exception as error:
            raise CalendarError(
                "Google Calendar connection is not available"
            ) from error
        for config in _collection(configs):
            if _string(config, "name") == config_name:
                return _required(config, "id", "nanoid")
        client_id = os.getenv("GOOGLE_CLIENT_ID")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise CalendarError("Google Calendar OAuth credentials are not configured")
        try:
            created = self.client.auth_configs.create(
                _TOOLKIT,
                {
                    "type": "use_custom_auth",
                    "name": config_name,
                    "auth_scheme": "OAUTH2",
                    "credentials": {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "oauth_redirect_uri": _OAUTH_REDIRECT_URI,
                        "scopes": _SCOPES,
                    },
                },
            )
        except Exception as error:
            raise CalendarError(
                "Google Calendar auth configuration is not available"
            ) from error
        return _required(created, "id", "nanoid")

    def create_connect_link(
        self, *, user_id: str, callback_url: str | None = None
    ) -> dict[str, str | None]:
        auth_config_id = self._auth_config_id()
        try:
            request = self.client.connected_accounts.link(
                user_id, auth_config_id, callback_url=callback_url
            )
        except Exception as error:
            if re.search(r"multiple connected accounts", str(error), re.I):
                raise CalendarError(
                    "Google Calendar is already connected. Sync calendars or try again."
                ) from error
            raise CalendarError(
                "Google Calendar could not be connected. Try again."
            ) from error
        return {
            "connected_account_id": _required(
                request, "id", "connected_account_id", "connectedAccountId"
            ),
            "redirect_url": _string(request, "redirect_url", "redirectUrl"),
            "status": _string(request, "status"),
        }

    def find_active_connection(self, *, user_id: str) -> dict[str, str | None] | None:
        auth_config_id = self._auth_config_id()
        try:
            accounts = self.client.connected_accounts.list(
                statuses=["ACTIVE"], user_ids=[user_id]
            )
        except Exception as error:
            raise CalendarError(
                "Google Calendar connection is not available"
            ) from error
        for account in _collection(accounts):
            config = _value(account, "auth_config", "authConfig")
            account_config_id = _string(
                account, "auth_config_id", "authConfigId"
            ) or _string(config, "id", "nanoid")
            toolkit = _value(account, "toolkit")
            toolkit_slug = _string(account, "toolkit_slug", "toolkitSlug") or (
                toolkit if isinstance(toolkit, str) else _string(toolkit, "slug")
            )
            status = _string(account, "status")
            if (account_config_id and account_config_id != auth_config_id) or (
                toolkit_slug and toolkit_slug != _TOOLKIT
            ):
                continue
            if status and status.upper() != "ACTIVE":
                continue
            account_id = _string(
                account, "id", "nanoid", "connected_account_id", "connectedAccountId"
            )
            if account_id:
                profile = _value(account, "profile", "metadata")
                return {
                    "connected_account_id": account_id,
                    "provider_account_email": _string(
                        account,
                        "email",
                        "provider_account_email",
                        "providerAccountEmail",
                        "account_email",
                        "accountEmail",
                    )
                    or _string(profile, "email"),
                    "status": status,
                }
        return None
