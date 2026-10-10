# Crossroads marketplace backend

Independent TypeScript/Fastify repository for the marketplace's authorization and
transaction rules. Requires **Node.js 24+**. The FE and MCP repositories communicate
over HTTP; there are no shared packages or sibling source imports.

This is a runnable **local development foundation**, not the complete paid pilot.
It follows the [product plan](docs/product-plan.md), starting with catalog data
services, one currency (USD), invited demo suppliers, and human-approved purchases.

## Run

```sh
npm ci
cp .env.example .env
npm run dev
```

API: `http://localhost:4000`. `GET /health` is public. All `/v1` routes require
`Authorization: Bearer <token>`. Responses are plain JSON objects/arrays; failures
use `{ "error": { "code": "...", "message": "..." } }`.

```sh
npm run check  # strict TypeScript, transaction tests, build
npm start     # compiled API; run npm run build first
```

The server requires `DEMO_MODE=true`, binds only to loopback, and refuses
`NODE_ENV=production`. CORS defaults to `http://localhost:3000`. Local persistence
uses Node's built-in SQLite module (experimental in Node 24), stored at
`.data/marketplace.db`; nothing is reset on restart. Use a different `DATABASE_PATH`
for a fresh demo. No external database is needed to try the workflow.

## Demo identities and limits

| Token | Identity | Permissions |
| --- | --- | --- |
| `demo-buyer-token` | Northstar human approver | Buyer flow, approve purchases, accept delivery |
| `demo-agent-token` | Northstar delegated agent | Discover, request quotes/approval, commit approved orders, dispute |
| `demo-seller-token` | Structured Studio human operator | Read addressed RFQs, quote, deliver |
| `demo-seller-agent-token` | Structured Studio delegated agent | Read addressed RFQs, quote, deliver |
| `demo-other-buyer-token` | Separate buyer organization | Tenant-isolation demonstrations |

These are public development fixtures, not production credentials. Verification
badges are simulated. Agent delegations expire 30 days after the database is first
seeded. Agent limits are $500 per purchase and $1,000 cumulative; the buyer
organization limit is $2,000 cumulative. All placed, delivered, disputed, and
accepted orders count toward committed spend; there is no budget reset/refund
release yet. Delegation and suspension records are enforced but there is no
administration UI or credential lifecycle API yet.

## Implemented workflow

1. Buyer discovers a listing and submits an RFQ with scope, deadline, budget, and
   acceptance criteria. Only that listing's supplier can see and quote the RFQ.
2. Supplier quotes in integer USD cents with a delivery duration and expiration.
3. Buyer or delegated agent requests a backend-generated preview, then an approval.
4. Human buyer reviews the exact preview and approves it. An agent cannot approve.
5. Buyer or agent commits using the preview, approval, and `Idempotency-Key` header.
6. Supplier submits an HTTPS delivery reference. The server does not fetch it.
7. Human buyer explicitly accepts delivery or the buyer opens a dispute.

The approval binds the quote version, supplier, price, scope, terms text/version,
deadline, and acceptance criteria using a SHA-256 transaction snapshot. Quote
revisions invalidate earlier previews. Human decisions and accepted contracts are
preserved with actor, organization, and timestamps. Each mutation records an audit
event within the same database transaction.

Order creation uses `BEGIN IMMEDIATE`: authority, eligibility, quote freshness,
snapshot matching, organization/agent budget checks, commitment reservation,
approval consumption, and idempotency storage occur in one transaction. Concurrent
attempts cannot reserve the same budget twice. One RFQ can produce only one order.
Retries with the same key and payload return the original order; different
payloads with that key return a conflict. A new key cannot reorder a consumed quote.

Quote, order, and payment state are separate fields. Payment statuses explicitly
use `simulated_pending` and `simulated_settled`. No funds are charged, transferred,
refunded, or held. Fees and taxes are zero in this demo, not a pricing/tax decision.

## API

See [API contract](docs/api.md) for routes, payloads, statuses, and curl examples.
Development documentation consulted: [Fastify validation](https://fastify.dev/docs/latest/Reference/Validation-and-Serialization/)
and [Node SQLite](https://nodejs.org/api/sqlite.html).

## Validation

The tests cover the complete transaction, forbidden agent approval/acceptance,
forged approval fields, tenant isolation, quote revisions, expired quotes,
suspended suppliers, revoked agents, cumulative and per-transaction limits,
simultaneous commitments, idempotent replay and conflicts, separate dispute/payment
state, and recovery across a database restart. They use Fastify injection and
temporary SQLite stores, not live payment-provider calls.

## Remaining pilot work

- Confirm launch country, merchant of record, and reviewed contract/data terms.
- Replace demo identity with human sessions and scoped revocable agent credentials.
- Move persistence to PostgreSQL with migrations, relational constraints, tenant
  controls, transaction locking, and production budget accounting. The JSON-record
  SQLite store is intentionally limited to local development.
- Add onboarding, listing management, multi-supplier RFQs, quote comparison at scale,
  verification providers, admin moderation, and delegation management.
- Add private uploads with explicit sharing authorization and technical acceptance
  checks. Current delivery links do not validate file contents or transfer files.
- Add hosted payments, authenticated provider webhooks, reconciliation, refund and
  dispute resolution, background jobs, and operational monitoring.
- Add remote MCP authorization and the narrow supplier A2A integration described in
  the plan. The current adapter is stdio only.

These boundaries are deliberate: no public deployment or real purchases are
enabled by the development bootstrap.
