# Verification

## Backend integration tests

Start PostgreSQL and MinIO, configure `.env`, then:

```sh
uv sync --frozen
uv run alembic upgrade head
uv run alembic check
uv run ruff check market tests scripts
uv run ruff format --check market tests scripts
uv run pytest -q
```

Every case creates a unique PostgreSQL schema and drops only that schema. Image tests create and delete a unique MinIO bucket. The normal demo workspaces, stock and orders are untouched. The test user must be able to create schemas in the configured database.

Coverage includes buyer/seller role separation, workspace ownership, agent scope escalation, revocation, browser-only approval, CSRF/origin checks, private-context and competitor isolation, policy floors and intersections, matching exclusion reasons, exact-offer agreement, both approvals, stock allocation, concurrent competing approvals, idempotency and conflicting retries, expiring/reclaimed leases, offer expiry, controlled retries, image bytes/checksums/authorization, input injection and chunked body limits. Demo-reset guards are exercised only in the disposable test schemas.

`alembic check` checks the migrated schema against model metadata. Regenerate the checked-in API contract with `uv run python scripts/export_openapi.py` after changing routes or schemas; commit the changed JSON with the implementation.

## CLI tests

From `cli`:

```sh
uv sync --frozen
uv run ruff check market_cli tests
uv run pytest -q
uv run market --help
```

These tests cover JSON invocation, actionable error exits, credential-file permissions, allowed upload roots, safe checksummed image downloads and the absence of a human-approval command. They use mock HTTP responses. The backend's `scripts/demo_agents.py` additionally exercises the installed CLI against the real API, PostgreSQL and MinIO with distinct scoped buyer/seller credentials.

## Dashboard

From `frontend`, run `npm ci`, `npm run typecheck`, `npm run format:check`, and `npm run build`. The browser workflow is documented in the [frontend README](https://github.com/iivko/crossroads-frontend/blob/main/README.md). It consumes one prepared demo negotiation and allocates stock, so it is intentionally separate from isolated backend tests.

The browser workflow covers login, gallery selection, separate buyer/seller navigation, exact-offer acknowledgement, both human approvals, persisted order evidence and mobile layout. It uses real browser cookies/CSRF through the Next.js proxy. Screenshots are inspected at desktop and 390px widths.

The scripted negotiation is deterministic and does not test model reasoning quality. Actual image interpretation belongs to the external agent runtime and requires a vision-capable model. API rule enforcement is tested independently of model compliance.
