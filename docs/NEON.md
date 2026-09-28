# Run with Neon Postgres

The API uses SQLAlchemy's synchronous psycopg 3 driver. Neon is a hosted PostgreSQL database, so the app needs only a Neon connection string in `DATABASE_URL`.

1. Create a Neon project and copy its **pooled** connection string from the Neon dashboard. Use a separate Neon branch for development or tests.
2. Set `DATABASE_URL` in your untracked `.env` or deployment secret store. Keep the `sslmode=require` option supplied by Neon. For example:

   ```env
   DATABASE_URL=postgresql://USER:PASSWORD@ep-example-pooler.us-east-1.aws.neon.tech/neondb?sslmode=require
   ```

3. Set your authentication mode and credentials as described in the [README](../README.md#authentication-modes), then start the API. The first database-backed request creates the starter tables in the selected database.
4. Run the email recovery command with the same `DATABASE_URL` if you schedule it separately.

The app accepts Neon's copied `postgresql://` URL directly and selects its installed psycopg 3 driver. It also accepts `postgres://` and explicit `postgresql+psycopg://` URLs. Credentials with special URL characters must remain URL encoded.

The pooled endpoint is appropriate for normal API traffic. Use a **direct** Neon connection for a future migration tool or other session-level database operations. This starter currently calls `Base.metadata.create_all()` for first-run setup; it has no migration framework and will not alter existing tables when models change. Add a migration workflow before updating a deployed schema.

No live Neon credentials or database are included in this repository. The URL tests construct an engine without opening a network connection; run an end-to-end booking and concurrency test against your own Neon branch before deploying.
