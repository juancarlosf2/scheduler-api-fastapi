# Scheduler API Agent Guide

This is the operating guide for the standalone FastAPI scheduler. Keep it short enough to read before a change; use the linked documents and code for detail. The implementation is a feature-based modular monolith. [docs/FEATURE_ARCHITECTURE.md](docs/FEATURE_ARCHITECTURE.md) describes the implemented package layout and extension path.

## Start here

- [README.md](README.md): setup, authentication modes, HTTP routes, provider recovery, and known limits.
- [ARCHITECTURE.md](ARCHITECTURE.md): current request flow, booking rules, provider boundaries, and database lifecycle.
- [docs/FEATURE_ARCHITECTURE.md](docs/FEATURE_ARCHITECTURE.md): implemented feature modules, dependency rules, and extension steps.
- [PARITY.md](PARITY.md): behavior differences from the original application.
- [CONTRIBUTING.md](CONTRIBUTING.md): contribution and test expectations.
- `/docs` and `/openapi.json` on a running server: exact HTTP request and response contract.

## Current code map

| Area | Current files |
| --- | --- |
| App composition and shared HTTP wiring | `app/main.py`, `app/core/http.py`, `app/core/errors.py` |
| Identity and host resolution | `app/core/auth.py`, `app/core/dependencies.py`, `app/workos_auth.py` |
| Engine, session, and ORM registry | `app/core/database.py`, `app/core/model.py` |
| Host, profile, availability, event types, bookings | `app/features/hosts/`, `profiles/`, `availability/`, `event_types/`, `bookings/` |
| Calendar settings and Google adapter | `app/features/calendars/` |
| Contacts, onboarding, and workflows | `app/features/contacts/`, `onboarding/`, `workflows/` |
| Delivery outbox, webhook, Resend adapter | `app/features/notifications/` |
| Legacy import compatibility | Thin exports in `app/models.py`, `app/schemas.py`, `app/service.py`, and other flat modules |
| Contract and behavior tests | `tests/` |

The normal path is feature router -> validated Pydantic input -> feature service -> feature SQLAlchemy model/session -> optional provider port and adapter. A route should derive the current host through `get_current_host`; never trust a host ID from a body or query parameter. Public booking routes have no host session, so they must resolve only public, active event types and verify action tokens for invitee actions. New code imports from `app/features/` and `app/core/`; the old flat modules are compatibility exports.

## Booking and data invariants

- Store booking instants in UTC; interpret availability intervals in the schedule's IANA timezone. Date overrides replace weekly intervals for that date.
- Recheck the requested slot under the host reservation lock before committing. Preserve the scheduled-booking uniqueness and cross-event conflict tests.
- Save the local booking and durable side-effect intent first. Calendar and email calls happen after the local commit. An external failure must not silently remove a valid local reservation.
- Keep cancellation and reschedule tokens separate, hashed at rest, and rotated according to the lifecycle. Never log or return token hashes.
- Scope every host read and mutation to the authenticated host, including contacts, calendar preferences, outbox status, retries, and reconciliation.
- Preserve calendar destination IDs on bookings so a later preference change cannot redirect patch or delete operations.
- Do not automatically repeat a calendar create whose provider outcome is unknown. Use the documented reconciliation path before retrying.
- Keep provider keys, WorkOS secrets, database URLs, raw session cookies, and invitee personal data out of logs, fixtures, commits, and client-visible errors.

## Authentication and integrations

`APP_AUTH_MODE=local` is for development only and issues a one-time host API key. `APP_AUTH_MODE=workos` uses AuthKit sign-in, a verified sealed session, and a local host mapping keyed by WorkOS user ID. Production must not fall back to local API keys. WorkOS owns the hosted sign-in, email verification, and recovery experience; feature modules should only receive a verified host.

Composio and Resend are optional provider adapters. The interfaces are in `app/features/calendars/ports.py` and `app/features/notifications/ports.py`; keep SDK imports in `providers/`, inject fake implementations for tests, and use the pinned Google toolkit version from the adapter. Do not claim live provider behavior is verified from mocked tests. The email worker (`python -m app.notifications`) needs an operator-managed schedule and the same database/provider configuration as the API.

The current starter creates tables for first-run exploration. Use migrations before changing a deployed schema. Do not run a schema mutation against an existing user database just to check a local change.

## Development and verification

From this repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
uvicorn app.main:app --reload --env-file .env
python -m pytest -q
ruff check app
```

Install `.[providers]` only when working on the optional SDK adapters. Keep `.env` and local databases untracked. Test the affected HTTP route and service boundary, then run the suite for changes to booking, auth, schema, or provider lifecycle. For docs-only edits, verify links, file names, and commands instead of making live bookings. A passing mock test is not proof of a real WorkOS, Composio, or Resend connection.

## Astra-led implementation workflow

The parent agent owns technical direction, scope, architecture, acceptance criteria, integration decisions, and final review. Use `gpt-6-astra` with `high` reasoning for that role. Delegate substantial bounded UI work to `ui_implementer` and other bounded implementation to `implementer`, both `gpt-6-sol` with `high` reasoning. After implementation, use a fresh `independent_reviewer` at `gpt-6-sol` with `high` reasoning and proven read-only permissions. Do not silently change these assignments or escalate reasoning. Delegate only when it saves work or adds useful independence; answer simple read-only questions directly.

Before delegating, inspect any project-scoped agent definitions because they can shadow personal agents. Every implementation packet must state the required behavior, owned files or entry points, patterns to reuse, non-goals, interfaces and invariants, acceptance criteria, validation, and when to return to Astra. Keep edit scopes disjoint. Implementers should report evidence-backed conflicts to Astra instead of expanding scope. The reviewer should distinguish blocking defects from optional improvements; implementers repair accepted findings. Astra resolves routine technical disagreements and asks the user only for product decisions or actions that truly need authorization.

For feature changes, preserve HTTP method/path, validation, response, error status, data ownership, and concurrency behavior. Run the affected tests and inspect the OpenAPI diff when reorganizing routes or schemas. Update the architecture and parity documents when actual behavior changes.
