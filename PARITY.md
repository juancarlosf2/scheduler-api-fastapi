# Feature status

This tracks the starter's implemented features and its remaining gaps relative to a full scheduling application. "Implemented" means a corresponding FastAPI path exists and is exercised locally.

| Area | FastAPI starter status |
| --- | --- |
| Host identity | WorkOS AuthKit sealed sessions for deployment; per-host API keys for local development. Existing Better Auth sessions are not portable |
| Default availability | Local persistence and slot calculation |
| Event types | Core create, edit, draft, publish, duplicate, status, delete |
| Public profile and event lookup | Core public read paths |
| Public slots and booking | Local transaction and host-wide conflict checks; configured Composio calendars add busy-time checks |
| Invitee cancel and reschedule | Token-protected local lifecycle |
| Host meetings and contacts | Basic list paths |
| Host settings and onboarding | Local profile settings and setup/guide routes implemented |
| Google Calendar connection, sync, preferences | Composio adapter and host-scoped settings routes; live provider flow unverified |
| Host cancellation and external sync retry | Host cancellation and retry routes implemented; ambiguous creates require host reconciliation; provider-backed behavior unverified |
| CSV export and workflows | Meeting CSV and workflow listing implemented; workflow dispatch pending |
| Resend emails and webhook state | Durable outbox, bounded recovery command, host delivery list/retry, and signed webhook handling; no provider-backed test; emails have no action links |
| Audit retention | Pending |

The slot generator skips nonexistent local times during spring DST transitions. Local busy-booking checks protect the whole host across event types. Keep both rules explicit when adapting this starter to an existing scheduler.

Update this file whenever a feature becomes available or its behavior changes.
