# HTTP API

Base URL: `http://127.0.0.1:8100/api/v1`. The authoritative schemas are in `/openapi.json`, also exported to `docs/openapi.json`. Interactive docs are at `/docs`.

Bearer clients use `Authorization: Bearer TOKEN` and optionally `X-Workspace-ID`. Browser clients use cookies, an allowed Origin and `X-CSRF-Token` for writes. No API key or storage credentials are included in public DTOs.

Money is integer EUR cents. Dates are ISO calendar dates; timestamps are UTC. Unknown structured input fields are rejected. Errors have shape:

```json
{"error":{"code":"permission_denied","message":"This buyer credential cannot perform products:write","details":null}}
```

Lists of products, requests, negotiations, orders and activity support `limit`/`offset`. Responses contain `items`, `offset`, `limit`; all except activity also contain `total`. Defaults are 50; maximum page size is 100 (200 for activity).

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Database readiness |
| POST | `/auth/token` | User CLI login |
| POST | `/auth/session` | Browser login; Origin required |
| POST | `/auth/logout` | Revoke current credential |
| GET | `/auth/me`, `/workspaces` | Identity, role, scopes, memberships |
| GET/POST | `/credentials` | List/issue workspace agent tokens; admin only |
| DELETE | `/credentials/{id}` | Revoke agent token; admin only |
| GET | `/categories` | Supported structured category fields |
| GET/POST | `/products` | Search catalogue/create seller product |
| GET/PUT | `/products/{id}` | Read/full versioned update |
| PUT | `/products/{id}/stock` | Versioned inventory update |
| GET | `/products/{id}/history` | Seller's product revision history |
| GET/POST | `/products/{id}/images` | List images/upload one multipart file |
| PUT | `/products/{id}/images/cover` | Select cover image |
| PUT | `/products/{id}/images/order` | Reorder all ready images |
| DELETE | `/products/{id}/images/{image_id}` | Remove from gallery; `expected_version` query required |
| GET | `/images/{id}/content` | Authorized image bytes; optional `thumbnail=true` |
| GET/PUT | `/rules?product_id=…` | Own workspace or own seller product rules |
| GET | `/rules/history?product_id=…` | Own policy versions |
| GET/POST | `/requests` | Buyer's requests/create draft |
| GET/PUT | `/requests/{id}` | Buyer's request and rooms/edit unlaunched draft |
| POST | `/requests/{id}/launch` | Match and open negotiation rooms |
| POST | `/requests/{id}/cancel` | Stop an unconfirmed purchase |
| GET | `/requests/{id}/matches` | Eligibility reasons |
| GET | `/requests/{id}/compare` | Current offers, best fit and lowest price |
| GET | `/negotiations` | Participant's rooms |
| GET | `/negotiations/{id}` | Room, offers, approvals and action history |
| GET | `/negotiations/{id}/context` | Role-scoped context and permitted next actions |
| POST | `/negotiations/{id}/claim` | Acquire a 180-second turn lease |
| POST | `/negotiations/{id}/actions` | Submit a typed, versioned action |
| POST | `/negotiations/{id}/retry` | Buyer user starts a new bounded attempt |
| GET | `/approvals/{id}` | Review/status accessible to participants |
| POST | `/approvals/{id}/approve`, `/reject` | Browser approver only |
| GET | `/orders`, `/orders/{id}` | Own confirmed orders |
| GET | `/activity?resource_id=…` | Workspace audit events |
| GET | `/agent/instructions`, `/agent/inbox` | Agent schema/work discovery |

## Idempotency and versions

Supply `Idempotency-Key` (8–128 characters) for product creation, request creation/launch, uploads and agent actions. Replaying an identical payload under the same credential and operation returns its original result. Reusing a key for different content returns 409. Other state changes use an expected version or are naturally repeatable (revocation, cancellation); approval is idempotent for the exact confirmed offer.

Do not generate a new key when retrying an ambiguous network failure. Claims themselves are exclusive and cannot be reclaimed before expiry. If a claim response is lost, wait for its lease to expire and acquire a new one.

Product edits, stock, image ordering/cover/removal and policy changes require `expected_version`. Actions require the room's `expected_version` and its current `claim_token`. Human decisions require the current offer ID and room version.

## Negotiation lifecycle

A launched request opens at most 50 eligible product rooms. Each room starts with the seller's turn, lasts up to 48 hours, and alternates sides. Each offer lasts at most 24 hours. Each side may counter at most three times per attempt. The room permits four total clarification turns. An exhausted agent can accept, decline or request human review.

Agreement changes a room to `awaiting_approval`. Product/policy/stock/evidence changes move it to `needs_revision`, require a seller proposal and invalidate old approvals. Expiry is enforced during actions/approval and by the maintenance worker. Explicit buyer-user retries preserve history while starting a fresh bounded attempt. No model is called by the backend.

## Images

Upload one multipart `file` plus optional `caption`. Batch CLI uploads are individual requests with independent success/failure results. Limits: 10 active images per product, 10 MB/file, 25 million pixels, JPEG/PNG/WebP, no animation. Only ready normalized images are publicly visible within the authenticated catalogue. The service stores private object keys and proxies authorized bytes; clients do not receive MinIO credentials.

## Compatibility

API major version is in the URL. Both clients depend on HTTP schemas rather than importing backend code. `scripts/export_openapi.py` updates the tracked contract. Generate breaking changes in a new API major version; keep additional optional response fields compatible.
