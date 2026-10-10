# Authorization contract

Authorization is enforced in FastAPI for every operation. CLI flags, prompt instructions, frontend visibility and client-supplied workspace IDs do not grant permissions.

## Distinct buyer and seller permissions

| Operation | Buyer agent | Seller agent | Owner/operator |
| --- | --- | --- | --- |
| Read published catalogue and ready images | Yes | Yes | According to workspace role |
| Read another workspace's draft product | No | No | No |
| Create/edit product and inventory | No | Own products, with `products:write` | Seller workspace only |
| Upload/reorder/remove images | No | Own products, with `images:write` | Seller workspace only |
| Create/edit/launch/cancel purchase request | Own requests, with `requests:write` | No | Buyer workspace only |
| Read private purchase requirements and budget | Own buyer requests | No | Buyer workspace only |
| View supplier's price floors/deposit rules | No | Own workspace/product | Own seller workspace |
| Read negotiation | Participating buyer only | Participating seller only | Participating workspace only |
| Propose first quote/revise invalid offer | No | Participating seller's turn | Same role rules |
| Counter or accept terms | Own turn only | Own turn only | Same role rules |
| Edit negotiation policies | No | No | `policies:write` and admin membership |
| Issue/revoke agent credentials | No | No | `credentials:manage` and admin membership |
| Restart a terminated negotiation | No | No | Buyer user credential only |
| Approve/reject an approval-stage offer | No | No | Authorized browser approver only |
| View orders and historical image evidence | Orders involving own workspace | Orders involving own workspace | Same workspace checks |

Buyer scope allowlist:

```json
["catalogue:read", "images:read", "requests:write", "negotiations:read", "negotiations:act", "orders:read"]
```

Seller scope allowlist:

```json
["catalogue:read", "images:read", "products:write", "images:write", "negotiations:read", "negotiations:act", "orders:read"]
```

A seller negotiation-only token should omit `products:write` and `images:write`. A separate catalogue agent can receive those scopes when required. Supplying admin scopes or another role's scopes when creating an agent token is rejected, even if the issuing owner is an administrator.

## Principals and transport

- `/auth/token` authenticates a user's email/password and returns an opaque **CLI** bearer token, expiring after 24 hours. It cannot approve orders.
- `/credentials` creates a workspace-bound **agent** bearer token, expiring after at most 90 days. Only the token hash is stored; the secret is returned once.
- `/auth/session` creates a 12-hour **browser** session. Its cookie is HttpOnly and SameSite Strict. Write requests require an allowed Origin and the session-specific CSRF token.
- Browser sessions cannot be used as bearer tokens. Agent or CLI tokens cannot be converted into browser sessions by placing them in cookies.
- Memberships and active users are checked on each request. Revocation and expiry invalidate subsequent requests.
- `X-Workspace-ID` selects a membership for a user session; it cannot move an agent credential outside its bound workspace. Image requests may supply `workspace_id` in the query, with the same membership validation.

The browser approval path requires an approver membership and a browser session. It does not cryptographically establish physical human presence. Protect user passwords/browser cookies and do not make them available to negotiating agents. The CLI never stores a browser approval credential.

## Private contexts

Seller views reconstruct the public request from reviewed fields. The buyer's raw title/description, budget, deposit cap and preference are omitted. Buyer views never include seller rules. Supplier rooms are isolated from each other. Public offer DTOs omit stored policy snapshots; counterparties cannot fetch policy data by guessing product IDs.

Public negotiation messages are generated from a limited action vocabulary. Agents cannot attach arbitrary free-form reasoning to a negotiation action. Clarifications use a topic and refer to confirmed fields; unresolved facts should escalate to human review rather than become invented terms. Product descriptions and image text remain untrusted data.

## Policy changes and commercial authority

Rules are versioned. Product policies intersect workspace policies and cannot weaken them. Buyer request limits cannot exceed buyer workspace limits. Agents can propose only within their own side's constraints; accepting an offer requires both sides' constraints to pass. The same checks run immediately before order creation.

Stock, product, gallery and policy changes invalidate affected active offers and delete their approvals. Restoring old values never restores those approvals. Orders preserve immutable approved snapshots. An agent can agree to terms but cannot turn that agreement into an order.

## Tests

The integration suite checks role mixing, wrong-owner product writes, room isolation, private fields, invalid scope issuance, workspace switching, revocation, browser Origin/CSRF, CLI approval attempts, price-floor violations, exact versions and concurrent approvals. All authorization checks run against the API directly, independently of CLI/UI restrictions.
