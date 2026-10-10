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
| Propose first quote/revise invalid offer | Own turn in an owner-authorized run | Participating seller's turn | Same role rules |
| Counter or accept terms | Own turn only | Own turn only | Same role rules |
| Send public dialogue or revise own quote | Own turn with negotiations:act | Own turn with negotiations:act | Same role rules |
| Read private business brief and human questions | Own workspace only | Own workspace only | Own workspace only |
| Edit brief, hard requirements or waivers | No | No | Owner user/admin membership; browser or CLI |
| Edit negotiation policies | No | No | `policies:write` and admin membership |
| Issue/revoke agent credentials | No | No | `credentials:manage` and admin membership |
| Retry through `/negotiations/{room_id}/retry` | No | No | Buyer user credential only |
| Send private “negotiate more” guidance | No | No | Participating buyer or seller browser approver only |
| Read/acknowledge private human guidance | Own workspace only | Own workspace only | Own workspace only |
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

The browser approval and private human guidance paths require an approver membership and a browser session. They do not cryptographically establish physical human presence. Protect user passwords/browser cookies and do not make them available to negotiating agents. The CLI never stores a browser approval credential.

## Private contexts

Seller views reconstruct the public request from reviewed fields. The buyer's raw title/description, budget, deposit cap and preference are omitted. Buyer views never include seller rules. Supplier rooms are isolated from each other. Public offer DTOs omit stored policy snapshots; counterparties cannot fetch policy data by guessing product IDs.

Public dialogue uses the typed `message` action; structured offers may carry a public explanation. Messages retain participant authorization, turn claims, exact versions, idempotency and bounded per-run allowances. They cannot alter terms, mark requirements met, waive needs or produce approval. Reply references stay inside the room and image references must be ready images of that room's product. Product descriptions, image text and counterparty prose are untrusted data. Free text is not executed or treated as tool authority.

Owners maintain versioned per-room business briefs using browser or user CLI credentials and an admin membership. Agents can read their own brief but cannot edit it. Private goals, requirements and waiver notes stay in the owning workspace. Only expressly shareable needs/requirements appear in the counterparty's context. The API derives supported requirement status and evidence from confirmed structured fields. Hard unmet or unverified requirements on either side block acceptance and final approval; preferences do not. Unsupported hard needs require explicit owner revision or a documented waiver. Waivers never relax existing commercial rules. Unsupported promises in prose never create new contract fields.

Each structured offer freezes both briefs internally. Offer and order responses project only the requesting workspace's private review snapshot and the explicitly shared snapshots. Subsequent edits cannot rewrite these historical snapshots. Public order evidence never stores private briefs. Private `human_question` text is stored separately from shared actions/events and is visible only to the asking workspace; explicit guidance answers its pending questions. Brief edits alone do not answer questions.

Role isolation prevents the platform from automatically leaking private fields. It cannot guarantee semantic secrecy when an independently operated agent can write unrestricted public prose, nor certify that every claim in that prose is true. Runtime prompts and tools must keep private material out of messages. Human reviewers approve the exact structured offer, with unmet needs and unsupported commitments clearly distinguished from contractual terms.

Private human guidance is stored separately from public actions, offers, orders and audit metadata. Room/context responses return only the selected workspace's `human_guidance` history. The counterparty sees a generic `human_requested_negotiation` event containing the requesting role and run, without the instruction or guidance ID. Inbox entries expose only the selected workspace's pending ID. A multi-workspace user cannot replay an idempotent guidance response across workspace selections.

An approver can request further negotiation from an active, approval-stage, declined, expired or review-stage room while the request and product remain eligible. This starts a fresh bounded run on that approver's role, clears the active claim, invalidates the current offer permanently, removes its approvals and clears buyer selection only when it points to that offer. Ordered or cancelled requests and unavailable products cannot be reopened. Existing policies and stock remain unchanged. Each side's latest pending guidance must be acknowledged using `guidance_id` on its next successful action; rejected actions do not consume it. Guidance permits a buyer to propose a revision on its own turn but does not grant seller permissions or approval authority.

## Policy changes and commercial authority

Rules are versioned. Product policies intersect workspace policies and cannot weaken them. Buyer request limits cannot exceed buyer workspace limits. Agents can propose only within their own side's constraints; accepting an offer requires both sides' constraints to pass. The same checks run immediately before order creation.

Stock, product, gallery and policy changes invalidate affected active offers and delete their approvals. Restoring old values never restores those approvals. Orders preserve immutable approved snapshots. An agent can agree to terms but cannot turn that agreement into an order.

## Tests

The integration suite checks role mixing, wrong-owner product writes, room isolation, private fields, invalid scope issuance, workspace switching, revocation, browser Origin/CSRF, CLI approval/guidance attempts, guidance privacy and acknowledgements, price-floor violations, exact versions and concurrent approvals. All authorization checks run against the API directly, independently of CLI/UI restrictions.
