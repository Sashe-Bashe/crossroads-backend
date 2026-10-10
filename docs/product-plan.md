# A2A/B2B Marketplace — Product and Implementation Plan

## Overview

Build an **agent-enabled B2B marketplace**, where businesses can buy and sell through either a normal web interface or their own AI agents.

The important distinction: **this is not just a directory of agents. It is the system that turns a business request into a verified, approved, trackable transaction.**

The original sketch identifies the main pieces: seller companies, buyers, contracts and verification, an MCP/CLI interface, the marketplace backend, a frontend, and marketplace rules.

**Working assumption:** A2A means *agent-to-agent*. Since the sketch does not specify what is being traded, this plan uses repeatable digital business services as the starting category. Physical goods would need additional inventory, shipping, and returns workflows.

## 1. Define the product around one complete transaction

### Product concept

> A marketplace where businesses and their AI agents discover verified suppliers, request structured quotes, purchase services within defined permissions, and track delivery—with human approval wherever required.

Think of it as three connected layers:

| Layer | What it does |
|---|---|
| **Marketplace** | Supplier profiles, service listings, search, requests for quotes, orders, and reviews. |
| **Agent access** | Lets buyer and seller agents participate through MCP, APIs, and A2A integrations. |
| **Trust and transaction controls** | Verification, delegated authority, contracts, approvals, spending limits, payments, disputes, and audit records. |

Make the third layer central to the product. The goal is not merely “agents can talk.” It is **“a business can safely let an agent participate in procurement.”**

### Start with a narrow category

The recommended starting point is **fixed-scope digital services with measurable outputs**. One example is product-catalog data cleanup and normalization.

An illustrative buyer request:

> “Normalize these 5,000 product records into this schema, flag duplicates, and return the results by Friday. Maximum total price: $500. Ask me before placing the order.”

That gives an explicit input, deliverable, budget, deadline, and acceptance test.

For the first release, choose **one service category, one buyer profile, one launch country, and one currency**. Use invite-only suppliers.

Avoid starting with “anything any business might buy.” That would require designing too many different transaction and fulfillment models before validating one.

## 2. Turn the company, buyer, and contract notes into a data model

The first adjustment to the sketch: **do not create entirely separate company models for buyers and sellers.** Use one organization model with buyer and seller capabilities. The same business can have both.

Keep human users, businesses, and agents separate.

| Entity | What it should contain |
|---|---|
| **Organization** | Business identity, billing details, country, buyer/seller capabilities, account status. |
| **User + membership** | A human account, its organization memberships, and roles such as owner, buyer, approver, or seller operator. |
| **Agent + delegation** | Owning organization, credentials, permitted actions, delegating user or administrator, spending limits, expiry, and revocation status. |
| **Listing** | Seller, service category, input requirements, output format, pricing model, delivery expectations, and applicable terms. |
| **RFQ + quote** | A request for quotation, supplier responses, scope, price, currency, expiration, and quote versions. |
| **Contract + acceptance** | Exact terms version, transaction scope, counterparties, acceptance evidence, and relevant approvals. |
| **Order + fulfillment** | Accepted quote, execution status, deliverables, acceptance checks, and completion records. |
| **Policy + approval** | Organization rules and approvals tied to specific proposed transactions. |
| **Payment + financial records** | Provider references, charges, fees, refunds, transfers, and reconciliation status. |
| **Verification + dispute + audit event** | Evidence of checks, exception cases, and the history of important actions. |

Two modeling decisions matter especially:

**An agent is not just a user with an API key.** Give it an explicit delegation record that says which organization it represents and what that organization allows it to do.

**An order is not just a chat conversation.** Store scope, price, delivery requirements, and terms as structured records. Messages can explain the transaction, but they should not be the only record of what was agreed.

For the first release, support companies and their human representatives. The “person as buyer” model can later support sole proprietors or independent professionals; general consumer purchasing should be a separate scope decision.

## 3. Design the core buyer-to-seller workflow

Build one reliable path:

**Discover → Request quote → Compare → Approve → Order → Deliver → Accept → Settle**

### Discovery and quoting

The buyer enters a request through the website or their agent. The marketplace converts it into a structured request containing the required service, input description, expected output, budget, deadline, and acceptance criteria.

The backend filters eligible listings first. An AI layer can help interpret the request or explain matches, but it should not invent supplier capabilities, prices, or certifications.

Sellers receive the RFQ through their dashboard or agent integration. Their quote must specify the exact scope, total price, delivery commitment, expiration, and terms.

For the MVP, allow straightforward quotes and revisions. Do not build open-ended autonomous negotiation yet.

### Approval and order creation

The buyer or their agent selects a quote. The backend generates an **order preview** showing the supplier, deliverable, full price, applicable fees and taxes, contract version, deadline, and required approvals.

Initially, require a human to approve every purchase.

Bind that approval to the exact transaction snapshot. A later change to the supplier, price, scope, or contract must invalidate the approval and require another review.

Only then should the backend create the order and initiate the approved payment flow.

### Delivery and acceptance

The seller submits the deliverable against the order. The system runs the agreed technical checks—for example, whether the output matches the required schema—and presents the result to the buyer.

For the MVP, the buyer accepts the work or opens an issue. A seller agent saying “task completed” must not automatically mean “the buyer accepted the service.”

Record settlement, refunds, and disputes separately from delivery.

**Keep separate state machines for quotes, orders, and payments.** An order can be delivered while a payment is pending, or accepted while a refund dispute remains unresolved. One giant `status` field will make those situations difficult to represent correctly.

## 4. Build one backend with multiple interfaces

The MCP/CLI → backend sketch is the right foundation. Extend it as follows:

```text
Buyer AI agent ── MCP ─────────┐
Web application ── API ────────┼── Marketplace backend
CLI / integrations ── API ─────┘        │
                                        ├── Identity and permissions
                                        ├── Listings and matching
                                        ├── RFQs and quotes
                                        ├── Contracts and approvals
                                        ├── Orders and fulfillment
                                        ├── Payments and reconciliation
                                        └── Policies and audit records
                                                 │
                                  Seller dashboard / seller agent
```

**Every interface must go through the same authorization, policy, and transaction logic.** The MCP server should not have a shortcut around checks enforced in the website.

### MCP, A2A, and CLI have different jobs

**MCP** is the interface through which a buyer’s AI application can use the marketplace’s tools and data. The protocol defines client/server interactions and primitives such as tools and resources. [Source: MCP architecture](https://modelcontextprotocol.io/docs/learn/architecture)

**A2A** is useful when independent buyer and seller agents need to exchange messages, coordinate tasks, and return results. Its specification includes agent discovery through Agent Cards and task-oriented communication. It is complementary to MCP rather than a replacement for it. [Source: A2A specification](https://a2a-protocol.org/latest/specification/)

**CLI** is a developer and operator interface over the API. Keep it thin; it should not contain its own business rules.

Recommended sequencing: **core API first, MCP access next, then a narrow A2A integration with one pilot supplier**. Keep seller-dashboard participation available throughout, so suppliers do not need to build agents just to join.

### Implementation approach

For a TypeScript team, the suggested default is a React/Next.js frontend, a modular TypeScript backend, PostgreSQL, private object storage, and a managed background-job queue.

Start with **one modular backend, not microservices**. Keep clear boundaries between identity, procurement, fulfillment, and payments, but avoid distributed-system overhead in the pilot.

Use PostgreSQL row-level security as an additional tenant-isolation control where appropriate—not as a substitute for application authorization. PostgreSQL supports per-row policies, but privileged roles and table ownership need careful handling in the deployment design. [Source: PostgreSQL row security policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)

Do not host arbitrary seller-provided agent code in the MVP. Integrate with approved external endpoints or let sellers fulfill through the dashboard.

## 5. Separate verification, contracts, and payment responsibilities

The sketch groups verification and terms together. Implement them as related but distinct systems.

### Business verification

Track exactly what has been checked: business identity, representative identity or authority where applicable, domain ownership, and payment-provider onboarding status.

Use precise labels such as **“business identity checked”** rather than a blanket “trusted” badge. Keep service-quality evidence separate: sample-job results, completed orders, delivery performance, and buyer feedback.

Prefer provider-hosted onboarding for payment-related verification. Do not assume verification is a one-time event: Stripe’s documentation, for example, describes requirements that depend on account circumstances and can change, requiring platforms to monitor updates. [Source: Stripe Connect verification](https://docs.stripe.com/connect/handling-api-verification)

### Contracts and acceptance

Separate the agreements into three categories:

**Platform agreements:** the terms under which buyers and sellers use the marketplace.

**Transaction terms:** the specific scope, price, deadline, acceptance criteria, cancellation conditions, and other terms governing an order.

**Data-handling terms:** what suppliers may do with uploaded information, including retention, access, and any restrictions on model training.

For every accepted transaction, preserve the exact terms version and the actor, organization, timestamp, and approval evidence. Route nonstandard contractual changes to humans rather than allowing an agent to improvise acceptance.

Have counsel review the transaction structure, delegated-authority approach, and templates for the launch jurisdiction before enabling live purchases.

### Payments

Decide who will be the **merchant of record** before implementing the payment flow. In Stripe Connect, for example, that can be the platform or the connected business depending on configuration, and the customer-facing flow must identify the responsible party. [Source: Stripe merchant of record](https://docs.stripe.com/connect/merchant-of-record)

Use an established marketplace-capable payment provider, subject to approval for the country and business model. Keep raw payment credentials out of agent context, and reconcile internal records against provider events.

**Do not describe delayed payouts as escrow.** Stripe explicitly distinguishes its manual-payout functionality from escrow services and applies country-dependent payout deadlines. [Source: Stripe manual payouts](https://docs.stripe.com/connect/manual-payouts)

For the pilot, use one agreed payment and settlement model. Defer wallets, credit, financing, and complex cross-border payment flows.

## 6. Make the guardrails enforceable in code

The “rules / restrictions / guardrails” section of the notes should become a **server-side policy engine**, not just instructions in an AI prompt.

### Identity and authorization

Every agent action must resolve to an organization, an authorized principal, and an active delegation. Use narrow permissions, revocable credentials, and explicit checks on each resource.

For remote MCP access, follow the protocol’s authorization requirements and validate that credentials are intended for the server. MCP’s authorization requirements reject token passthrough and require audience validation. [Source: MCP authorization specification](https://modelcontextprotocol.io/specification/latest/basic/authorization)

### Spending and commitments

Enforce limits per transaction, agent, and organization. Check **existing spend plus reserved commitments**, not only completed payments.

Make budget reservation atomic: two agents must not simultaneously consume the same remaining budget. Require idempotency keys for order creation and payment initiation so retries cannot create duplicate purchases.

### Human approval

At launch, require human approval for purchases, contractual exceptions, payout-detail changes, and dispute resolutions.

Later, allow low-value repeat purchases only for approved suppliers, standard terms, and explicit spending policies. Do not jump directly from “agent can search” to “agent can spend freely.”

### Untrusted content

Treat seller descriptions, messages, files, and agent responses as untrusted input. A listing that says “ignore approval rules” must have no authority over the transaction system.

OWASP identifies external content as a route for indirect prompt injection and recommends least privilege, deterministic validation, segregation of untrusted content, and human approval for high-risk actions. Those controls belong in the architecture. [Source: OWASP prompt injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)

### Data and operational controls

Require explicit authorization before sharing buyer files with a supplier. Use private storage, restricted access, retention policies, and redacted logs.

Validate external endpoints and callbacks, restrict outbound connections, and authenticate webhook messages. Provide a kill switch to revoke an agent, suspend a seller, or pause new orders.

For important actions, record **who acted, for which company, under which permission, on which transaction version, and with what result**.

## 7. Keep the first agent toolset small

Expose narrow operations rather than a powerful generic “do anything” tool.

| Tool group | Initial operations |
|---|---|
| **Discovery** | `search_listings`, `get_listing`, `get_seller_verification` |
| **Quoting** | `create_rfq`, `list_quotes`, seller-scoped `submit_quote` |
| **Purchase preparation** | `preview_order`, `request_approval` |
| **Commitment** | `commit_order`, requiring valid server-side approval and an idempotency key |
| **Fulfillment** | `get_order`, seller-scoped `submit_delivery`, buyer-scoped `open_dispute` |

An agent may **request** approval. It must not be able to manufacture an approved record by passing `approved: true`.

The backend should retrieve authoritative prices, contract versions, and approval records itself. Do not trust amounts or approval claims supplied by the model.

For longer-running operations, return an order or task identifier and support status checks and notifications. A temporary connection failure must not cause the client to start the purchase again.

### Human-facing screens

The buyer needs search, an RFQ form, quote comparison, an approval inbox, and order tracking.

The seller needs onboarding, listing management, an RFQ inbox, quote submission, delivery submission, and payment visibility.

The administrator needs verification review, moderation, dispute handling, transaction inspection, and account suspension.

The frontend should be useful without AI. It is also where humans resolve exceptions that agents cannot safely handle.

## 8. Use a 12-week plan for an invite-only paid pilot

This is a planning estimate for **two experienced engineers plus a founder handling supplier onboarding and customer operations**, with legal/security support as needed. Provider approval and legal setup can extend the schedule.

| Period | Main work | Exit condition |
|---|---|---|
| **Weeks 1–2: Validate the transaction** | Interview roughly 10–15 target buyers, recruit about five suppliers, define one service schema, and manually facilitate pilot jobs. | Several buyers commit real jobs; suppliers agree to quote and deliver against a common specification. |
| **Weeks 3–4: Build the foundation** | Organizations, memberships, seller profiles, listings, verification workflow, basic search, and tenant controls. | An approved seller can publish a listing and a buyer can submit an RFQ. |
| **Weeks 5–6: Build the commercial workflow** | Structured quotes, comparison, terms snapshots, human approvals, order states, and audit events. | A complete transaction can run in a test environment without ambiguous scope or authority. |
| **Weeks 7–8: Add delivery and payments** | Hosted payment flow, verified webhooks, financial reconciliation, delivery submission, acceptance, and refund/dispute handling. | An approved live pilot transaction can complete end to end. |
| **Weeks 9–10: Add agent interfaces** | MCP tools, thin CLI, delegation controls, and one seller-side A2A integration. | A buyer agent and pilot seller agent participate without bypassing the commercial controls. |
| **Weeks 11–12: Harden and run the beta** | Security tests, duplicate-request tests, failure recovery, monitoring, operator tools, and paid customer use. | The invited cohort can transact reliably, with documented intervention procedures. |

Before increasing transaction volume, test failures deliberately: expired quotes, revoked agents, changed terms, duplicate submissions, late deliveries, seller suspension, payment failures, and cross-company access attempts.

**Keep out of this pilot:** unrestricted autonomous purchasing, custom AI-negotiated legal terms, arbitrary agent hosting, every service category, native mobile apps, and complex financing.

## 9. Launch through buyers’ actual jobs—not an empty catalog

Acquire the initial supply around a small group of committed buyers.

Recruit a handful of businesses with recurring needs in the chosen category. Ask them to bring real work. Then onboard enough qualified suppliers to fulfill those specific jobs, ideally with more than one viable supplier for a typical request.

For the early transactions, manually help with scoping, supplier matching, and exceptions. Record where intervention was necessary. That becomes the automation backlog.

The first promise should be something concrete:

> “Submit a well-defined request, compare qualified quotes, and complete the purchase with a clear record of scope, approval, and delivery.”

Measure whether the agent interface actually improves that process. Compare time to submit an RFQ, time to select a supplier, human interventions, and successful completion between agent-assisted and web-only orders.

Do not assume customers value A2A itself. Validate whether they value the outcome it enables.

## 10. Test a simple revenue model and clear success criteria

### Revenue

The initial pricing hypothesis is a **transaction fee on successfully completed orders**, with no listing fee during the pilot.

For illustration—not a forecast—at an 8% fee:

| Illustrative activity | Amount |
|---|---:|
| 100 monthly orders × $500 average order value | $50,000 transaction volume |
| Marketplace fee at 8% | $4,000 gross fee revenue |

That $4,000 is not profit. Account for payment costs borne by the platform, infrastructure, support, fee refunds, and transaction losses.

Track contribution per order:

**Net platform revenue − payment costs − variable infrastructure − support cost − transaction losses.**

Later, test a buyer subscription for procurement controls, approved-supplier catalogs, reporting, and integrations. First establish that transactions repeat and that support requirements are economically manageable.

### Pilot success criteria

These are initial internal targets, not industry benchmarks:

- **Demand:** five buyer companies complete around 20 paid orders, with at least two returning within the category’s normal purchasing cycle.
- **Fulfillment:** qualified quotes arrive within the promised response window, and most jobs complete without founder-led rescue.
- **Safety:** every commitment has valid authority and approval evidence; retry and concurrency tests produce no duplicate purchases or spending-limit breaches.
- **Economics:** support effort per order declines, and the observed transaction margin supports the proposed fee.

The long-term advantage to build toward is **reliable supply, structured purchasing, clear authority, and verified delivery history**—not simply a larger list of agent endpoints.

## Final recommendation

**Build the first version as a human-approved B2B transaction system with agent access. Prove one category, then progressively automate the parts that are repeatable and controllable.**

### Decisions to confirm before implementation

Confirm what the first listings will be—physical products, business services, or access to APIs/AI agents—and which country the marketplace will launch in. The plan above assumes repeatable digital business services; changing the initial category may change fulfillment, acceptance, payment, and compliance requirements.
