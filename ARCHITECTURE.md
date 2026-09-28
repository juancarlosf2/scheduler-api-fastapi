# Architecture and extension points

This repository is a compact starting point for a scheduling backend. The layers are intentionally small:

```text
HTTP request
  -> app/main.py or app/workspace_routes.py (validation and response contract)
  -> app/service.py (booking decisions)
  -> app/availability.py (pure slot calculation)
  -> app/models.py and app/storage.py (persistence)
  -> app/integrations/ (optional provider adapters)
```

## Domain boundaries

- A host owns a default availability schedule, event types, bookings, and contacts. Protected endpoints resolve the host from a verified WorkOS session or local development key; callers cannot supply another host ID to cross that boundary.
- A public profile exposes active event types only. A public booking always rechecks a generated slot in a database transaction before it saves.
- Invitee cancel and reschedule links carry different random tokens. Only token hashes are stored. Rescheduling rotates both tokens.
- UTC instants are stored for bookings. Availability intervals use local wall-clock minutes in an IANA timezone. Date-specific intervals override weekly intervals for that date.

## Replace the starter auth

The deployed path uses WorkOS AuthKit. The login callback verifies the OAuth state and binds a local host to the WorkOS user ID. Host routes authenticate the sealed session and derive the host from that ID. Local mode issues a one-time bearer key for development without provider credentials. Preserve the rule that ownership comes from a verified principal, never a request-supplied host ID.

## Add a calendar provider

`SchedulerService` reads host-scoped calendar settings and uses the optional Composio adapter to add external busy intervals and create, patch, or delete events. It records the intended provider operation before the call so a provider failure does not erase the local reservation. An interrupted create can have an unknown provider outcome; a found event ID can be reconciled through the host endpoint, while confirming absence for an `inflight` attempt requires operators to quiesce workers and repair state first. Google Meet publication requires an active connection and destination calendar. The adapter is inactive without provider credentials; live provider behavior still needs an end-to-end check.

## Add notifications

Booking persistence and provider calls are separate phases. The booking transaction stores email delivery rows with stable occurrence keys. The API attempts delivery after commit, and `python -m app.notifications` processes due rows when run by an operator or scheduler. Host routes expose delivery status and retry. The optional Resend adapter sends rendered snapshots; signed webhooks update state with event deduplication. Production operators must schedule the recovery command and monitor failures.

## Database lifecycle

The starter defaults to SQLite and supports SQLAlchemy PostgreSQL URLs. Table creation at startup is for first-run exploration and does not alter existing tables. Adopt Alembic migrations before production schema changes. For concurrent bookings, keep a host-scoped transaction lock or an equivalent database-enforced exclusion rule and retain the unique scheduled event/start constraint. Run the reservation concurrency test against the production database engine before launch.

## Contract and compatibility

The HTTP paths are purpose-built REST endpoints. They are not wire-compatible with the TanStack Start server functions that inspired the initial booking rules. Check [PARITY.md](PARITY.md) before migrating a client from that application.
