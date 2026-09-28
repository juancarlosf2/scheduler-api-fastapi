# Contributing

Use Python 3.11 or newer. Install the development dependencies with `python -m pip install -e '.[dev]'`, then run `ruff check app` and `python -m pytest -q` before opening a pull request.

Keep changes within the feature boundaries in `app/features/`: HTTP validation in routes and schemas, business decisions in services, pure time calculations in `availability/slots.py`, and provider calls in `providers/`. Add a focused test for any change to slot generation, ownership, booking conflicts, or token handling. Update the README and PARITY status when adding an endpoint or external integration. Keep `app/main.py` for application composition and the flat `app/*.py` modules as compatibility exports.

Do not include real credentials, host API keys, local databases, or invitee personal information in issues, fixtures, or commits. Use invented test addresses and times.

For new provider adapters, implement the calendar or email port and make network clients injectable so tests can use fakes. An adapter is not complete until the booking lifecycle invokes it and handles both success and failure without losing the local booking.
