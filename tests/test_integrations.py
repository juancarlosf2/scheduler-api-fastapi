"""Provider-free contract tests for optional calendar and email adapters."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from app.integrations.calendar import (
    CalendarConnectionExpired,
    CalendarConnectionRequiresReconnect,
    CalendarError,
    GoogleCalendarAdapter,
    GOOGLE_CALENDAR_TOOLKIT_VERSION,
)
from app.integrations.email import (
    EmailDeliveryError,
    ResendEmailAdapter,
    booking_email_idempotency_key,
)


def _calendar_client(response):
    return SimpleNamespace(tools=SimpleNamespace(execute=Mock(return_value=response)))


def test_calendar_list_and_busy_normalization():
    client = _calendar_client({"successful": True, "data": {"response_data": {"items": [
        {"id": "primary", "primary": True, "summary": "Work", "timeZone": "America/New_York"}
    ]}}})
    adapter = GoogleCalendarAdapter(client)
    assert adapter.list_calendars(user_id="host", connected_account_id="ca_1") == [
        {"id": "primary", "is_primary": True, "name": "Work", "time_zone": "America/New_York"}
    ]
    client.tools.execute.assert_called_once_with(
        "GOOGLECALENDAR_LIST_CALENDARS", arguments={"max_results": 250, "show_deleted": False, "show_hidden": False},
        connected_account_id="ca_1", user_id="host", version=GOOGLE_CALENDAR_TOOLKIT_VERSION,
    )
    client.tools.execute.return_value = {"successful": True, "data": {"timeZone": "America/New_York", "items": [
        {"id": "cancelled", "status": "cancelled"},
        {"id": "all-day", "start": {"date": "2026-10-01"}, "end": {"date": "2026-10-02"}},
        {"id": "event-1", "summary": "Busy", "start": {"dateTime": "2026-10-01T10:00:00Z"},
         "end": {"date_time": "2026-10-01T10:30:00+00:00"}},
    ]}}
    intervals = adapter.list_busy_intervals(
        user_id="host", connected_account_id="ca_1", calendar_id="primary",
        time_min=datetime(2026, 10, 1, tzinfo=timezone.utc),
        time_max=datetime(2026, 10, 2, tzinfo=timezone.utc),
    )
    assert intervals == [
        {"event_id": "all-day", "summary": None,
         "start_at": datetime(2026, 10, 1, tzinfo=ZoneInfo("America/New_York")),
         "end_at": datetime(2026, 10, 2, tzinfo=ZoneInfo("America/New_York"))},
        {"event_id": "event-1", "summary": "Busy",
         "start_at": datetime(2026, 10, 1, 10, tzinfo=timezone.utc),
         "end_at": datetime(2026, 10, 1, 10, 30, tzinfo=timezone.utc)},
    ]


def test_calendar_all_day_uses_event_zone_and_exclusive_end_across_dst():
    client = _calendar_client({"successful": True, "data": {"response_data": {
        "timeZone": "UTC", "items": [{
            "id": "all-day", "start": {"date": "2026-11-01", "timeZone": "America/New_York"},
            "end": {"date": "2026-11-02"},
        }],
    }}})
    intervals = GoogleCalendarAdapter(client).list_busy_intervals(
        user_id="host", connected_account_id="ca_1", calendar_id="primary",
        time_min=datetime(2026, 11, 1, tzinfo=timezone.utc),
        time_max=datetime(2026, 11, 3, tzinfo=timezone.utc),
    )
    assert intervals[0]["start_at"].astimezone(timezone.utc) == datetime(2026, 11, 1, 4, tzinfo=timezone.utc)
    assert intervals[0]["end_at"].astimezone(timezone.utc) == datetime(2026, 11, 2, 5, tzinfo=timezone.utc)


def test_calendar_busy_intervals_reads_second_page():
    client = _calendar_client(None)
    client.tools.execute.side_effect = [
        {"successful": True, "data": {"response_data": {
            "timeZone": "UTC", "items": [], "nextPageToken": "page-2",
        }}},
        {"successful": True, "data": {"response_data": {"items": [{
            "id": "second-page", "start": {"date": "2026-10-01"}, "end": {"date": "2026-10-02"},
        }]}}},
    ]
    intervals = GoogleCalendarAdapter(client).list_busy_intervals(
        user_id="host", connected_account_id="ca_1", calendar_id="primary",
        time_min=datetime(2026, 10, 1, tzinfo=timezone.utc),
        time_max=datetime(2026, 10, 2, tzinfo=timezone.utc),
    )
    assert intervals == [{"event_id": "second-page", "summary": None,
                          "start_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
                          "end_at": datetime(2026, 10, 2, tzinfo=timezone.utc)}]
    first, second = client.tools.execute.call_args_list
    assert second.args == first.args == ("GOOGLECALENDAR_EVENTS_LIST",)
    assert second.kwargs["arguments"] == {**first.kwargs["arguments"], "pageToken": "page-2"}
    assert second.kwargs["connected_account_id"] == first.kwargs["connected_account_id"] == "ca_1"
    assert second.kwargs["user_id"] == first.kwargs["user_id"] == "host"


@pytest.mark.parametrize("bad_page", [
    {"items": [], "nextPageToken": ""},
    {"items": [], "nextPageToken": 42},
    {"items": "invalid"},
])
def test_calendar_busy_intervals_rejects_malformed_pages(bad_page):
    client = _calendar_client({"successful": True, "data": {"response_data": bad_page}})
    with pytest.raises(CalendarError, match="events .* invalid"):
        GoogleCalendarAdapter(client).list_busy_intervals(
            user_id="host", connected_account_id="ca_1", calendar_id="primary",
            time_min=datetime(2026, 10, 1, tzinfo=timezone.utc),
            time_max=datetime(2026, 10, 2, tzinfo=timezone.utc),
        )


def test_calendar_busy_intervals_rejects_repeated_page_token():
    client = _calendar_client({"successful": True, "data": {"items": [], "nextPageToken": "repeat"}})
    with pytest.raises(CalendarError, match="pagination was invalid"):
        GoogleCalendarAdapter(client).list_busy_intervals(
            user_id="host", connected_account_id="ca_1", calendar_id="primary",
            time_min=datetime(2026, 10, 1, tzinfo=timezone.utc),
            time_max=datetime(2026, 10, 2, tzinfo=timezone.utc),
        )
    assert client.tools.execute.call_count == 2


def test_calendar_all_day_without_timezone_fails_closed():
    client = _calendar_client({"successful": True, "data": {"items": [{
        "id": "all-day", "start": {"date": "2026-10-01"}, "end": {"date": "2026-10-02"},
    }]}})
    with pytest.raises(CalendarError, match="timezone is missing"):
        GoogleCalendarAdapter(client).list_busy_intervals(
            user_id="host", connected_account_id="ca_1", calendar_id="primary",
            time_min=datetime(2026, 10, 1, tzinfo=timezone.utc),
            time_max=datetime(2026, 10, 2, tzinfo=timezone.utc),
        )


def test_calendar_create_and_patch_contract():
    client = _calendar_client({"successful": True, "data": {"response_data": {
        "id": "event-1", "htmlLink": "https://calendar.google.com/event",
        "conferenceData": {"entryPoints": [{"entryPointType": "video", "uri": "https://meet.google.com/abc"}]},
    }}})
    adapter = GoogleCalendarAdapter(client)
    start = datetime(2026, 10, 1, 14, tzinfo=timezone.utc)
    end = datetime(2026, 10, 1, 15, 30, tzinfo=timezone.utc)
    assert adapter.create_event(user_id="host", connected_account_id="ca_1", calendar_id="primary",
                                start_at=start, end_at=end, timezone_name="America/New_York", summary="Call",
                                attendees=["invitee@example.com"], conference_provider="google_meet") == {
                                    "event_id": "event-1", "html_link": "https://calendar.google.com/event",
                                    "meeting_join_url": "https://meet.google.com/abc"}
    args = client.tools.execute.call_args
    assert args.args == ("GOOGLECALENDAR_CREATE_EVENT",)
    assert args.kwargs["arguments"] == {
        "attendees": ["invitee@example.com"], "calendar_id": "primary", "create_meeting_room": True,
        "event_duration_hour": 1, "event_duration_minutes": 30, "send_updates": "all",
        "start_datetime": "2026-10-01T10:00:00", "summary": "Call", "timezone": "America/New_York",
    }
    adapter.patch_event(user_id="host", connected_account_id="ca_1", calendar_id="primary",
                        event_id="event-1", summary="Updated")
    assert client.tools.execute.call_args.kwargs["arguments"] == {
        "calendar_id": "primary", "event_id": "event-1", "send_updates": "all", "summary": "Updated"
    }
    with pytest.raises(CalendarError, match="end time must be after"):
        adapter.create_event(user_id="host", connected_account_id="ca_1", calendar_id="primary",
                             start_at=end, end_at=start, timezone_name="UTC", summary="Invalid")


@pytest.mark.parametrize("missing_response", [
    {"successful": False, "error": {"code": 404, "message": "Not Found"}},
    {"successful": False, "error": "Event not found"},
])
def test_calendar_delete_treats_missing_event_as_success(missing_response):
    client = _calendar_client(missing_response)
    GoogleCalendarAdapter(client).delete_event(
        user_id="host", connected_account_id="ca_1", calendar_id="primary", event_id="event-1",
    )
    client.tools.execute.assert_called_once_with(
        "GOOGLECALENDAR_DELETE_EVENT", arguments={"calendar_id": "primary", "event_id": "event-1"},
        connected_account_id="ca_1", user_id="host", version=GOOGLE_CALENDAR_TOOLKIT_VERSION,
    )


def test_calendar_delete_treats_thrown_404_as_success_but_preserves_other_errors():
    client = _calendar_client(None)
    client.tools.execute.side_effect = RuntimeError("HTTP 404 from provider")
    adapter = GoogleCalendarAdapter(client)
    adapter.delete_event(user_id="host", connected_account_id="ca_1", calendar_id="primary", event_id="event-1")
    client.tools.execute.side_effect = RuntimeError("HTTP 403 from provider")
    with pytest.raises(CalendarError, match="Google Calendar action failed"):
        adapter.delete_event(user_id="host", connected_account_id="ca_1", calendar_id="primary", event_id="event-1")


@pytest.mark.parametrize("error,expected", [
    ("Connected account ca_1 is EXPIRED state 410", CalendarConnectionExpired),
    ("ACCESS_TOKEN_SCOPE_INSUFFICIENT", CalendarConnectionRequiresReconnect),
])
def test_calendar_error_categories(error, expected):
    client = _calendar_client({"successful": False, "error": error})
    with pytest.raises(expected):
        GoogleCalendarAdapter(client).list_calendars(user_id="host", connected_account_id="ca_1")


def test_calendar_requires_configuration_before_network(monkeypatch):
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    with pytest.raises(CalendarError, match="not configured"):
        GoogleCalendarAdapter()


def test_calendar_connect_and_find_active_connection():
    auth_configs = SimpleNamespace(list=Mock(return_value={"items": [{
        "id": "ac_1", "name": "Scheduler Google Calendar OAuth v2",
    }]}))
    connected_accounts = SimpleNamespace(
        link=Mock(return_value={"connected_account_id": "ca_1", "redirect_url": "https://connect.example"}),
        list=Mock(return_value={"items": [
            {"id": "wrong", "auth_config_id": "ac_other", "status": "ACTIVE"},
            {"id": "ca_1", "auth_config_id": "ac_1", "status": "ACTIVE",
             "toolkit": {"slug": "googlecalendar"}, "profile": {"email": "host@example.com"}},
        ]}),
    )
    adapter = GoogleCalendarAdapter(SimpleNamespace(auth_configs=auth_configs, connected_accounts=connected_accounts))
    assert adapter.create_connect_link(user_id="host", callback_url="https://app.example/callback") == {
        "connected_account_id": "ca_1", "redirect_url": "https://connect.example", "status": None,
    }
    connected_accounts.link.assert_called_once_with("host", "ac_1", callback_url="https://app.example/callback")
    assert adapter.find_active_connection(user_id="host") == {
        "connected_account_id": "ca_1", "provider_account_email": "host@example.com", "status": "ACTIVE",
    }
    connected_accounts.list.assert_called_once_with(statuses=["ACTIVE"], user_ids=["host"])


def test_email_send_and_idempotency():
    client = SimpleNamespace(Emails=SimpleNamespace(send=Mock(return_value={"id": "email-1"})))
    adapter = ResendEmailAdapter(client, from_email="noreply@example.com")
    key = booking_email_idempotency_key("booking-1", "confirmed", "invitee")
    assert key == "booking-confirmation/booking-1"
    assert booking_email_idempotency_key("booking-1", "confirmed", "host") == "host-booking-confirmation/booking-1"
    assert booking_email_idempotency_key("booking-1", "rescheduled", "invitee",
                                         datetime(2026, 10, 1, tzinfo=timezone.utc)) == (
                                             "booking-rescheduled/booking-1/2026-10-01T00:00:00.000Z")
    assert adapter.send(to="invitee@example.com", subject="Confirmed", text="Booking confirmed",
                        idempotency_key=key, tags=[{"name": "booking_id", "value": "booking-1"}]) == "email-1"
    client.Emails.send.assert_called_once_with(
        {"from": "noreply@example.com", "to": "invitee@example.com", "subject": "Confirmed",
         "text": "Booking confirmed", "tags": [{"name": "booking_id", "value": "booking-1"}]},
        idempotency_key=key,
    )
    client.Emails.send.side_effect = RuntimeError("provider secret in error")
    with pytest.raises(EmailDeliveryError, match="Failed to send email"):
        adapter.send(to="invitee@example.com", subject="Confirmed", text="Booking confirmed")


def test_email_webhook_rejects_missing_secret_or_signature(monkeypatch):
    client = SimpleNamespace(Webhooks=SimpleNamespace(verify=Mock()))
    adapter = ResendEmailAdapter(client, from_email="noreply@example.com")
    monkeypatch.delenv("RESEND_WEBHOOK_SECRET", raising=False)
    with pytest.raises(EmailDeliveryError, match="not configured"):
        adapter.verify_webhook(raw_payload="{}", headers={})
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", "whsec_test")
    with pytest.raises(EmailDeliveryError, match="Invalid email webhook"):
        adapter.verify_webhook(raw_payload="{}", headers={})
    client.Webhooks.verify.assert_not_called()
    client.Webhooks.verify.return_value = {"type": "email.delivered"}
    assert adapter.verify_webhook(raw_payload='{"type":"email.delivered"}', headers={
        "Svix-ID": "msg_1", "Svix-Timestamp": "123", "Svix-Signature": "v1,sig",
    }) == {"type": "email.delivered"}
    client.Webhooks.verify.assert_called_once_with({
        "payload": '{"type":"email.delivered"}',
        "headers": {"id": "msg_1", "timestamp": "123", "signature": "v1,sig"},
        "webhook_secret": "whsec_test",
    })


def test_email_requires_configuration_before_network(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    with pytest.raises(EmailDeliveryError, match="not configured"):
        ResendEmailAdapter(from_email="noreply@example.com")
