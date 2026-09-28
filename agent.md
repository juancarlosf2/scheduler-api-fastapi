# Agent working guide

This project is a standalone FastAPI scheduling backend. It uses a shared
SQLAlchemy database and organizes behavior by feature. Read
[AGENTS.md](AGENTS.md) for the full operating contract and
[docs/FEATURE_ARCHITECTURE.md](docs/FEATURE_ARCHITECTURE.md) for the package
map and extension steps.

## How a request works

`app/main.py` builds the FastAPI app and includes feature routers. A router in
`app/features/<feature>/` validates input and obtains the current host from
`app/core/dependencies.py` when the route is protected. Feature services make
business decisions with a SQLAlchemy session and feature-owned models.
Calendar and email side effects go through provider ports and adapters. The
flat `app/*.py` modules preserve older imports; add new logic in `app/core/`
or `app/features/`.

## Main features

| Feature | Responsibility |
| --- | --- |
| Hosts and profiles | Verified host identity, registration, public profile, settings |
| Availability and event types | Schedule rules, timezone-aware slots, event drafts and publishing |
| Bookings | Reservation lock, public actions, host meetings, action tokens |
| Calendars | Connection settings, external busy times, event sync and reconciliation |
| Notifications | Durable email outbox, delivery retry, signed webhook |
| Contacts, onboarding, workflows | Host workspace state and routes |

WorkOS AuthKit session handling is in `app/workos_auth.py`; local API keys are
for development. `app/core/database.py` creates the engine and registers all
feature models. SQLite is convenient locally; a deployed schema needs managed
migrations before changes to existing tables.

## Run and verify

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
uvicorn app.main:app --reload --env-file .env
ruff check app
python -m pytest -q
```

For scheduling changes, verify host ownership, timezone behavior, overlap
prevention, token rotation, and external failure recovery. Keep booking and
notification intent durable before provider calls. Do not commit secrets or
invitee data. If routes or schemas move, compare `/openapi.json` with the
previous version.

## GPT-6 Astra workflow

The Astra parent owns architecture, scope, acceptance criteria, and final
review. Bounded implementation belongs to a designated implementer; a fresh
read-only reviewer checks the result. Preserve the model and reasoning
assignments specified in [AGENTS.md](AGENTS.md). Give implementers explicit
file ownership, invariants, tests, and escalation conditions. Keep scope
conflicts and product decisions with Astra and the user respectively.
