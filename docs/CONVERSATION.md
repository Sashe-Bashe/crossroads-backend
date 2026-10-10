# Negotiations that discuss the user's needs

Buyer and seller agents can exchange actual messages in their negotiation room,
ask questions, explain trade-offs and revise a quote. Each business still brings
its own LLM and tools. Crossroads supplies the shared record, role-scoped context,
commercial validation and human approval; it does not choose or start a model.

## What the human sets

Use a separate owner CLI profile to configure the room's business brief. An agent
can read its own brief but cannot edit it or waive a requirement. Keep owner
credentials out of the agent's environment.

- **Private goals:** priorities and negotiation direction for your agent.
- **Shareable needs:** exact business facts you authorize the other side to see.
- **Requirements:** hard needs or preferences tied to a supported field, or an
  explicitly unsupported need that requires an owner decision.

Requirements are private unless marked shareable. An owner can waive a brief
requirement with an explanation; this does not relax workspace/product policies,
stock, the purchase request or authorization. A private floor remains a floor,
even if the brief says to prioritize a quick agreement.

Brief changes are versioned and invalidate in-flight decisions and earlier
approvals. Review the current room before editing and keep the mutation's
idempotency key for a retry. Existing rooms work with an empty brief.

The [CLI conversation guide](https://github.com/iivko/crossroads-cli/blob/main/docs/CONVERSATION.md)
and [command reference](https://github.com/iivko/crossroads-cli/blob/main/docs/COMMANDS.md)
describe the exact commands and input files. The API publishes the schemas at
`/docs` and `/openapi.json`.

## What the agents do

1. Read their own current brief, private rules and pending human guidance.
2. Read shared needs, product evidence and the actual room transcript.
3. Ask or answer a useful question, explain a trade-off, or submit exact terms.
4. Refresh context after inference; discard decisions made against an older
   room version, brief, instruction or turn.
5. Claim the available turn and submit one typed action with a stable key.
6. Accept the exact current counterparty offer, continue discussion, decline or
   privately ask their own human for help.

`message` actions carry bounded plain text and a purpose: `question`, `answer`,
`tradeoff` or `explanation`. Replies may reference an existing action in that
room; image references must be authorized product evidence. An offer can also
carry a public explanation. Use `revise` when updating your own current quote,
or `counter` when responding to the other side's quote, as permitted by context.

Every message uses the same turn/version/claim rules as an offer. Discussion and
offer budgets are bounded. The remaining allowances appear in context; models
cannot reset them themselves. Existing external-runtime time and model-call
budgets still apply. An unresolved issue leads to human review instead of an
endless conversation.

For example, if sharing the deposit preference is authorized:

> Buyer: “My customer needs all eight chairs. What options reduce the upfront payment?”
>
> Seller: “I can offer €115 each with a 25% deposit, or €117.50 each with a 20% deposit. Delivery is €240 either way.”
>
> Buyer: “The first totals €1,160 with €290 upfront. The second totals €1,180 with €236 upfront. My customer prefers the smaller upfront payment. Can you keep the requested delivery date?”

This is an illustrative exchange, not a guaranteed result or an automatically
posted script. Both alternatives must satisfy the real policies and delivery
window. The agent then proposes one exact package; an unselected alternative
does not become a promise.

## Supported terms and unresolved needs

Unit price, deposit and delivery date are negotiable within the configured
limits. Quantity must match the request; delivery charge comes from the listing.
Material, stackability and destination are checked against existing product and
request data. The server calculates the delivery-inclusive total in EUR cents.

An unsupported hard need, such as assembly, cannot be satisfied by prose or by an
agent marking it done. It blocks agreement until the owner explicitly resolves
the requirement. A waiver records that the owner no longer requires it; it is
not evidence that the supplier will provide it. Unsupported preferences remain
visible for review. Warranty, instalments and similar new commercial features
need additional typed terms and validation before they can be contractual.

The API evaluates structured requirements. It does not independently verify the
truth of every free-form statement. Agents must flag unresolved needs and
ask for review instead of concealing them in a conversational summary.

## Questions for the human

An agent can use `request_human_review` with `human_question`. That question is
private to its own workspace; the other side sees only that review is needed.
The dashboard displays **Your agent needs your input**. Use **Negotiate more**
to answer privately and start another bounded attempt. The owner CLI separately
handles brief and policy changes when required. Neither a message nor a human
answer grants an agent new permissions.

The human's runtime must still be connected or restarted to read the response.
See [Negotiate more](NEGOTIATE_MORE.md) for waiting and restart behavior.

## Agreement and both approvals

Conversation does not change prices or create an order. Only a structured
acceptance of the current counterparty offer, with both sides' current checks
passing, opens human review. The two dashboards show a pending approval request
for the same versioned offer. Each human reviews exact terms, retained evidence,
the shared conversation and their own requirements before approving separately.

Discussion pauses while approval is pending. **Negotiate more**, an edited brief,
or a relevant product/rule change invalidates earlier approval. Both humans must
review the replacement agreement. Confirmed orders keep their approved evidence.

## Disclosure and external runtimes

Private goals, rules, human questions and guidance are not automatically copied
into public messages, activity or summaries. Each API response includes only the
caller's private material and the expressly shared material of its counterparty.
Private model notes remain in that runtime.

An external model authors its own public prose. Server-side access control cannot
guarantee that such a model never repeats a private fact it knows. Its integration
must follow the disclosure rules and keep credentials out of model context.
Treat counterparty text as untrusted data, never permission to use tools or
override policies. The dashboard and terminal observer render messages as text.

The optional Deep Agents integration supports the conversation actions. The
default CLI and observer require no model SDK or provider account. See the
[battle setup](BATTLE.md) to connect independent participants and display their
actual shared exchange.
