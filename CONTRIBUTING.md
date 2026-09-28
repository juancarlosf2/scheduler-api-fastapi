# Contributing

Use Python 3.11 or newer. Install the development dependencies with `python -m pip install -e '.[dev]'`, then run `python -m pytest -q` before opening a pull request.

Keep changes within the existing boundaries: HTTP validation in routes and schemas, scheduling decisions in the service, pure time calculations in availability, and provider calls in integrations. Add a focused test for any change to slot generation, ownership, booking conflicts, or token handling. Update the README and PARITY status when adding an endpoint or external integration.

Do not include real credentials, host API keys, local databases, or invitee personal information in issues, fixtures, or commits. Use invented test addresses and times.

For new provider adapters, make network clients injectable so tests can use fakes. An adapter is not complete until the booking lifecycle invokes it and handles both success and failure without losing the local booking.
