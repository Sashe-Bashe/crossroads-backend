# Local operations

Run Compose commands from `backend`. Services are `postgres`, `minio`, one-shot `migrate`, `api`, `worker`, and `frontend`. Host bindings are loopback-only. API and UI run as non-root users; object storage is private.

## Startup, shutdown and updates

```sh
docker compose up --build -d
docker compose ps
docker compose logs --tail=80 api worker
docker compose stop
# Later: preserves database and object volumes
docker compose up -d
```

Database migrations run before API/worker startup. Seeding is always explicit and idempotent. Normal restarts never recreate tables, reset stock or overwrite accounts. After source changes, rebuild with `docker compose up --build -d`. Preserve all three sibling checkout paths for the frontend build context.

The MinIO image builds a pinned upstream source commit in `docker/MinIO.Dockerfile`; the first build is slower than subsequent builds. Its Go compiler is confined to the build stage. Upstream license files are copied into the runtime image.

## Configuration

Copy `.env.example` once. Host processes use `DATABASE_URL` and `MINIO_ENDPOINT` from `.env`; Compose overrides their hosts to the service names. Defaults use database port 55433, API 8100, dashboard 3100, and MinIO 9100/9101.

Compose optional variables: `POSTGRES_PASSWORD`, `POSTGRES_PORT`, `API_PORT`, `FRONTEND_PORT`, `MINIO_PORT`. If changing the database password, update host `DATABASE_URL` too; an existing PostgreSQL volume retains its original password. If changing frontend ports, update `FRONTEND_URL` and `ALLOWED_ORIGINS`. Changing demo passwords in `.env` does not change already-seeded hashes.

`COOKIE_SECURE=false` is for local HTTP. A future deployed environment needs HTTPS, `COOKIE_SECURE=true`, appropriate exact origins, non-demo credentials and an ingress policy. This Compose file is the approved local environment, not a public deployment configuration.

## Credentials and permissions

Use separate buyer/seller CLI profiles and scoped agent tokens. Revoke compromised tokens with `market credentials revoke ID`; the next API request rechecks revocation and workspace membership. User CLI tokens expire after 24 hours; browser sessions after 12 hours; agents after 1–90 days. Use the operator `add-user` command to create a role-bound workspace. See [authorization](AUTHORIZATION.md) for the full matrix.

Passwords, bearer tokens, session cookies and private policies are never written to activity events. Uvicorn access logs contain HTTP paths/statuses; keep secrets out of URLs. CLI credentials are stored in a local file with mode 0600, or passed by environment without persistence.

## Image jobs and recovery

Uploads create a database job and a private staging object. The worker verifies/normalizes image content, strips metadata, produces display and thumbnail JPEGs, records dimensions and SHA-256, then publishes the image. A successful gallery change increments the product revision and invalidates pending offers.

A worker claims a three-minute job lease. If it exits during processing, another process can reclaim after the lease expires. Attempts use a short backoff and stop after three failures. The image reports `failed`; re-upload it with a new idempotency key after fixing the cause. The original source is not public. JPEG/PNG/WebP are supported, with 10 MiB, 25 million pixels and ten images per product limits. Animated inputs are rejected.

The worker also expires offers/rooms, clears outdated approvals and removes abandoned staging/unreferenced output objects older than 24 hours. Referenced image objects, including deleted gallery entries retained by product/order history, are preserved. There is intentionally no automatic deletion of order evidence.

## Negotiation recovery

- Read fresh `negotiations context` before acting. A claim expires after 180 seconds; reclaim if the agent stops or inference takes too long.
- For a lost action response, retry the same payload and idempotency key. Reusing that key with different data fails.
- A stock, gallery, listing or policy change invalidates old approvals. The seller must propose a fresh offer, both agents agree, then both humans review again.
- After decline, expiry or human-review escalation, a buyer user can run `negotiations retry ROOM`. Agent tokens cannot reset their own round limit. Earlier offers/actions remain visible.
- Every agreement still requires both human approvals of the same offer. Approval of an already-confirmed identical offer returns the existing order without allocating more stock.

## Demo reset

```sh
docker compose exec api python -m market.manage reset-demo --confirm-demo-reset
```

This deliberate destructive command requires `DEMO_ENABLED=true`. It refuses a database containing any non-demo user or workspace. It removes demo requests, offers, orders, credentials and stock history, removes only image keys recorded in that database, and seeds a fresh demo. Stop any active demo agent loops first. Existing sessions will need to sign in again. Do not use it to clear a mixed real/demo database; refusal is intentional.

## Persistence and backup

`docker compose down` retains volumes. Do not use `down --volumes` if data should survive. For a consistent local backup, stop API and worker writers, take a PostgreSQL dump and copy the MinIO volume, then restart writers. Database and object-store snapshots belong together: restoring only the database may leave image references without bytes. Back up `.env` separately as a secret.

A basic database dump while writers are stopped:

```sh
docker compose stop api worker frontend
docker compose exec -T postgres pg_dump -U market -d market -Fc > market-backup.dump
# Also snapshot the crossroads_minio-data volume with your backup tooling.
docker compose up -d
```

This release serializes commerce writes with a PostgreSQL transaction advisory lock. Before high-volume deployment, replace the global lock with resource-level coordination, add operational monitoring and evaluate queue throughput. The current lock intentionally prioritizes local correctness and predictable concurrent approval behavior.
