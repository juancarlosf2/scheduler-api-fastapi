# Architecture and extension points

This repository is a feature-based FastAPI scheduling backend. A feature owns
its routes, validation, persistence tables, and business rules. Shared
infrastructure lives in `app/core/`; `app/main.py` composes the application.

```text
HTTP request
  -> app/main.py (router composition and error translation)
  -> app/features/<feature>/api.py or workspace_api.py (HTTP contract)
  -> app/features/<feature>/service.py (business decisions)
  -> app/features/<feature>/models.py (SQLAlchemy tables)
  -> app/features/<feature>/providers/ (optional external effects)
```

The package map and extension steps are in
[docs/FEATURE_ARCHITECTURE.md](docs/FEATURE_ARCHITECTURE.md).

## Feature boundaries

- `hosts` owns local registration and the host record. WorkOS AuthKit login
  remains in `app/workos_auth.py`; `app/core/dependencies.py` resolves a
  verified session or local development key to one host.
- `profiles` owns public profile lookup and authenticated profile settings.
- `availability` owns schedules, interval validation, and pure slot generation.
- `event_types` owns drafts, publication, and host event configuration.
- `bookings` owns public actions, host meetings, reservation locking, token
  rotation, and the composed `SchedulerService` transaction boundary.
- `calendars` owns account settings and the optional Composio adapter.
- `contacts`, `onboarding`, and `workflows` own their host workspace state.
- `notifications` owns the email outbox, retry worker, signed webhook, and
  optional Resend adapter.

The old flat modules such as `app/service.py`, `app/models.py`, and
`app/availability.py` export the same objects for existing users. New code
should import feature modules directly. All tables share the `Base` registry
in `app/core/model.py`.

## Booking transaction

Public booking resolves an active event and generates slots in its schedule's
IANA timezone, including local and connected-calendar busy times. It acquires
a host reservation lock and rechecks the slot in the transaction. The local
booking, answers, and durable notification rows commit before provider calls.
A provider failure does not erase the local booking.

Cancel and reschedule actions use different random tokens. Only hashes are
stored; rescheduling rotates both tokens. Booking instants are UTC, while
availability intervals use local wall-clock minutes. Date-specific intervals
replace weekly intervals for that date. Protected queries derive ownership
from the authenticated host, never a request-supplied host ID.

## Replace a provider

`app/features/calendars/ports.py` defines the calendar operations used by
settings and booking lifecycle code. The Composio implementation is in
`app/features/calendars/providers/composio.py`. Inject an implementation into
`SchedulerService(calendar_adapter=...)` and the calendar route dependency.
Keep SDK imports inside adapters. Preserve the recorded calendar destination
ID, and reconcile an unknown calendar-create outcome before retrying.

`app/features/notifications/ports.py` defines email sending and webhook
verification. The Resend implementation is in
`app/features/notifications/providers/resend.py`. Booking enqueues delivery
rows in its transaction. The API attempts delivery after commit, and
`python -m app.notifications` processes due rows on an operator-managed
schedule. Tests can inject fake transports without provider credentials.

## Database lifecycle

`app/core/database.py` creates the engine and session factory. SQLite is the
default for local exploration; PostgreSQL is supported through `DATABASE_URL`.
Feature models are registered before `create_all` runs. First-run table
creation does not migrate existing schemas. Add Alembic migrations before a
production schema change. Keep the host-scoped reservation lock or an
equivalent database-enforced exclusion rule, and run the concurrency test
against the intended production database engine.

## HTTP contract

The HTTP method, path, request and response shape, and status code are part of
this API's contract. Review `/openapi.json` when changing a route or schema,
and update [FEATURE_STATUS.md](FEATURE_STATUS.md) when capabilities change.
