# Crossroads backend

FastAPI authority for a provider-neutral B2B marketplace. PostgreSQL stores commerce and authorization; MinIO stores private images. External agents use the [Python CLI](https://github.com/iivko/crossroads-cli/blob/main/README.md). Humans inspect products and approve exact offers in the [Next.js dashboard](https://github.com/iivko/crossroads-frontend/blob/main/README.md).

## Start the complete local stack

Requires Docker with Compose. Clone the public repositories into sibling directories named `backend`, `frontend` and `cli`:

```sh
mkdir crossroads && cd crossroads
git clone https://github.com/iivko/crossroads-backend.git backend
git clone https://github.com/iivko/crossroads-frontend.git frontend
git clone https://github.com/iivko/crossroads-cli.git cli
cd backend
```

Start the stack:

```sh
cp .env.example .env
docker compose up --build -d
docker compose exec api python -m market.manage seed
```

The first MinIO build compiles a pinned upstream source revision and can take several minutes. The seed is explicit and idempotent. The worker processes its two original demo illustrations per product. It does not run an LLM or negotiate automatically.

- Dashboard: http://localhost:3100
- API health: http://127.0.0.1:8100/api/v1/health
- Interactive API documentation: http://127.0.0.1:8100/docs
- OpenAPI contract: http://127.0.0.1:8100/openapi.json
- MinIO console: http://127.0.0.1:9101
- PostgreSQL: `127.0.0.1:55433`

These ports avoid the unrelated services already using 3000 and 8000. Bindings are loopback-only. Database and object data persist in Compose volumes.

## Demo accounts

The sample `.env` sets `DEMO_PASSWORD=local-market-demo`. All four fictional accounts use that password **only when explicitly seeded**. Changing the environment later does not rotate existing account passwords.

| Email | Workspace | Role |
| --- | --- | --- |
| `buyer@demo.local` | Forma Studio | Buyer |
| `forma@demo.local` | Forma Supply | Seller |
| `nord@demo.local` | Nord Office | Seller |
| `atelier@demo.local` | Atelier Works | Seller |

The demo catalog has 240 Forma chairs, 150 Nord chairs, and 45 Atelier chairs. An 80-chair request for Sofia in 14 days matches Forma and Nord; Atelier fails stock and delivery. Seed illustrations are original code-generated CC0 fixtures, not photographs or proof of a real product.

## Run the scripted external-agent demonstration

With Python 3.12 and `uv` installed on the host:

```sh
uv sync --frozen
uv run python scripts/demo_agents.py
```

The script issues separate restricted buyer/seller tokens, invokes the actual CLI, verifies two image downloads, negotiates €6,880 with Forma, and declines Nord. It creates a new request; it never resets data, approves an order, or calls a model. Open the printed approval link, sign in as the buyer and approve, then sign out and sign in as Forma to approve the same offer. The resulting order allocates 80 units once. Repeated demonstrations consume stock only if humans confirm orders.

## Host development

```sh
docker compose up -d --build postgres minio
uv sync --frozen
uv run alembic upgrade head
uv run python -m market.manage storage-init
uv run python -m market.manage seed
uv run uvicorn market.main:app --reload --host 127.0.0.1 --port 8100
```

In a second terminal, run `uv run python -m market.worker`. Frontend and CLI setup is documented in their repositories. Do not run host API/worker processes and the Compose equivalents on the same port when switching environments.

Create a non-demo role-bound workspace and account with the operator command:

```sh
uv run python -m market.manage add-user --email owner@example.com --name 'Workspace Owner' --workspace 'Acme Procurement' --role buyer
```

The command prompts for a password. One user can have multiple memberships in the database; each workspace and each agent credential has a single buyer or seller role. The current release has no public registration, SSO or membership-management UI.

## Tests

```sh
uv run pytest -q
uv run alembic check
```

Tests use a disposable PostgreSQL schema per case and delete only their own schema. Media tests use a uniquely named MinIO bucket and delete only their own objects. Start PostgreSQL and MinIO first. Tests do not reset the normal demo database. See [TESTING.md](docs/TESTING.md).

## Architecture and guarantees

- `market/auth.py`: Argon2 password hashing, opaque hashed credentials, role allowlists, workspace membership checks, scope restriction and browser CSRF.
- `market/service.py`: matching, effective rules, typed actions, immutable offers, exact-version approval and atomic orders.
- `market/policies.py`: integer-cent commercial checks; sender policies on proposal, both policies on agreement and ordering.
- `market/media.py`: image validation, normalized JPEGs, thumbnails, content authorization and durable jobs.
- `market/worker.py`: retryable image processing and expiry maintenance.
- `market/main.py`: versioned REST API and safe public errors.
- `alembic/versions`: tracked schema migrations; web startup does not create tables or seed accounts.

Commercial writes use a short PostgreSQL transaction-level advisory lock in this local release, plus row locking and unique order/allocation constraints. This intentionally serializes business mutations for predictable correctness. It is not a high-throughput multi-host queue design. No external model calls occur while a database lock is held.

Offers use one SKU and EUR final demo prices, with no tax calculation. Agent agreement only opens human review. Bearer credentials, including owner CLI sessions, cannot approve. A browser session is an authorization boundary, not proof that a human physically clicked; keep user passwords and approval-session cookies out of agent runtimes.

There are no payments, escrow, real fulfilment, external model SDKs, or automatic LLM execution. Any agent runtime capable of invoking commands can use the CLI. Actual image interpretation requires that runtime to pass downloaded images into a vision-capable model.

Further documentation: [authorization](docs/AUTHORIZATION.md), [API](docs/api.md), [operations](docs/OPERATIONS.md), [testing](docs/TESTING.md).
