# Scheduler API FastAPI

A standalone Python/FastAPI backend for building a scheduling product. It has its own API and database, with WorkOS AuthKit for deployed host sign-in and optional Composio Google Calendar and Resend integrations. FastAPI publishes an OpenAPI document at `/openapi.json` and interactive API docs at `/docs`.

## Run locally

Python 3.11 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
uvicorn app.main:app --reload --env-file .env
```

The default database is a local SQLite file for exploration. Set `DATABASE_URL` to a PostgreSQL connection URL for a shared deployment. The starter creates its tables on startup; use a managed migration workflow before deploying schema changes to an existing installation.

Run tests with `python -m pytest -q`.

The API needs no provider account for local bookings. Optional provider adapter packages are installed with `python -m pip install -e '.[providers]'`.

## Authentication modes

`APP_AUTH_MODE=local` explicitly enables local development. It lets `POST /v1/hosts` create a host and return one bearer API key. The API stores only a hash. Without an explicit auth mode or complete WorkOS configuration, host authentication fails closed. Do not use local mode for an internet-facing deployment.

For a deployed service, set `APP_AUTH_MODE=workos`, `APP_ENV=production`, `WORKOS_API_KEY`, `WORKOS_CLIENT_ID`, `WORKOS_COOKIE_PASSWORD`, and `WORKOS_REDIRECT_URI`. Configure that same redirect URI in WorkOS AuthKit. Visit `/v1/auth/login`; the callback provisions a host bound to the verified WorkOS user ID and sets a sealed, HttpOnly session cookie. `/v1/auth/logout` ends the session. WorkOS manages the hosted sign-in, verification, and password recovery experience. In this mode, the local API key registration endpoint is disabled.

The cookie password must be a Fernet key. Generate one locally with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` and keep it outside Git. A partial WorkOS configuration fails closed; production mode refuses local API key authentication. This integration was tested with SDK 9.1 using mocked WorkOS responses; a real AuthKit tenant is still required for an end-to-end sign-in check. See [WorkOS Python SDK](https://workos.com/docs/sdks/python) and [AuthKit session helpers](https://workos.com/docs/reference/authkit/session-helpers).

## First host and booking

1. In local mode, `POST /v1/hosts` with a username, display name, email, and IANA timezone. Save the `api_key` in the response. It is returned once. In WorkOS mode, sign in at `/v1/auth/login` instead.
2. In local mode, send `Authorization: Bearer <api_key>` to host routes under `/v1/hosts/me`. In WorkOS mode, your client sends the session cookie from AuthKit sign-in and an exact matching `Origin` header for changes. Set weekly availability and create an event type.
3. Publish the event type. Invitees use the public profile, event, and slots routes under `/v1/public/{username}` without a host credential.
4. Post a booking against a listed slot. The response includes separate action tokens. Keep those tokens private and use the matching token for cancellation or rescheduling.

For example, register a local host:

```bash
curl -sS -X POST http://127.0.0.1:8000/v1/hosts \
  -H 'Content-Type: application/json' \
  -d '{"username":"alex","display_name":"Alex","email":"alex@example.com","timezone":"America/New_York"}'
```

Copy the returned `api_key` into your own shell variable, then set availability and create an event. API keys are secrets; do not commit them or paste them into issue reports.

Every protected host request derives the host identity from its verified credential. Public lookup returns only published events. Booking creation rechecks availability and booked time before writing; callers must handle a conflict response if another invitee booked the slot first.

## Endpoint groups

| Group | Routes |
| --- | --- |
| Health and contract | `GET /health`, `GET /openapi.json`, `GET /docs` |
| Host and auth | Local host registration, AuthKit login/callback/logout, `GET /v1/hosts/me` |
| Availability | `GET/PUT /v1/hosts/me/availability` |
| Event types | List, create, draft, update, publish, change status, duplicate, and delete under `/v1/hosts/me/event-types` |
| Public booking | Public profile, event, slots, and booking under `/v1/public/{username}` |
| Booking actions | `POST /v1/bookings/{booking_id}/cancel` and `/reschedule` |
| Host workspace | Profile settings, onboarding, meeting list/CSV/host cancellation, contact list/notes, and workflow list under `/v1/hosts/me` |
| Calendar settings | Connection link, sync, and calendar preferences under `/v1/hosts/me/calendar` when configured |
| Email delivery | Durable booking notification outbox, host retry, and verified Resend webhook |

The exact request and response shapes are generated from the running app at `/docs`. The OpenAPI file can also drive a typed client generator.

## Provider recovery

Booking transactions save notification rows before calling Resend. With both `RESEND_API_KEY` and `RESEND_FROM_EMAIL` configured, the API attempts delivery after the booking commits. Run `python -m app.notifications` on a schedule to recover queued, due failed, and stale processing rows after interruptions. Each invocation processes a bounded batch. Hosts can inspect a booking's deliveries at `GET /v1/hosts/me/bookings/{booking_id}/email-deliveries` and retry an eligible delivery through its host route. The worker needs the same `DATABASE_URL` and Resend configuration as the API.

Calendar create attempts are recorded before the provider call. A failed or interrupted create can have an unknown outcome because the pinned Composio create tool does not provide a proven idempotency key. After a returned failure, the host must inspect the destination Google Calendar, then call `POST /v1/hosts/me/meetings/{booking_id}/reconcile-calendar-create` with either `{"reconciled_event_id":"..."}` if the event exists or `{"confirmed_absent":true}` if it does not. For an interrupted request still marked `inflight`, the endpoint accepts a found event ID; confirming absence requires an operator to quiesce API workers and repair the state before retrying, because a live request could still finish. Calendar sync status is visible on booking and meeting responses.

## Relationship to the original application

The original application exposes named TanStack Start server functions, so the FastAPI paths are a new HTTP contract. This starter has a separate database schema and does not read or migrate the original database.

The original application uses Better Auth. This starter uses WorkOS AuthKit instead, so existing browser sessions cannot be shared. Calendar and email provider adapters are present, but real account configuration and provider-backed tests are still needed. Booking emails are informational: action tokens are returned once in the booking API response and are never stored in plaintext email snapshots. This is not a drop-in replacement for the existing production API.

See [PARITY.md](PARITY.md) for the current feature-by-feature status.

## Contributing

Open an issue with a failing scenario or a focused proposal. For changes to booking behavior, add a test that exercises the public HTTP route and relevant host ownership boundary. Do not commit `.env`, database files, or host API keys.
