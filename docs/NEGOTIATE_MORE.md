# Tell your agent to negotiate more

Both buyer and seller humans can send private instructions from the dashboard.
The marketplace queues the instruction for that business's external agent; it
does not choose, host or launch an LLM.

## In the dashboard

1. Sign in at http://localhost:3100 as the buyer or seller.
2. Open a negotiation from **Negotiations** or **Approvals**.
3. Select **Negotiate more** and write a specific instruction, such as “Try for
   a lower deposit while keeping the delivery date” or “Hold our price and
   emphasize the confirmed delivery schedule.”
4. Select **Send to my agent**. Your instruction appears in your private history.

If **Your agent needs your input** is shown, answer its private question through
this same form. The answer is sent to your own agent, not posted in the shared
conversation. Required brief changes and policy changes remain separate owner
CLI operations. See the [conversation guide](CONVERSATION.md).

Sending starts a new bounded negotiation round and gives your side the first
turn. It clears previous approvals and invalidates the previous offer for
approval, while retaining the offer history. The agent must propose new terms;
both humans must approve any new agreement. A confirmed order or cancelled
request cannot be reopened through this button.

Only a participant's authorized human browser session can send instructions.
Agent tokens and CLI owner tokens cannot impersonate this human action. The
other business sees that negotiation resumed, but never receives your tactic
or private instruction text.

Private minimum prices, spending limits, deposits, stock and delivery rules
continue to apply. An instruction cannot change those limits or grant new
permissions. Each human-requested round has the normal counteroffer limit;
agents cannot start new rounds on their own.

**Pending** means the instruction is waiting for your agent. **Applied** means
your agent submitted an action acknowledging it; it does not guarantee a better
price or a completed deal. A newer instruction replaces a still-pending one
from the same business; the earlier entry remains as **Superseded**.

## Connect your own agent

An agent that has already exited must be restarted by its owner. A running
integration can wait for inbox changes with the CLI:

```bash
# Run in the participant's environment with only its own MARKET_TOKEN.
market agent watch --timeout 300
market negotiations context ROOM_ID --json
```

The inbox exposes `pending_guidance_id` for that participant. Context exposes
`human_guidance`, containing only that workspace's history. Treat the latest
pending instruction as commercial direction, subordinate to the server's
private rules and your runtime's permissions. Download/open product evidence
as usual; an instruction is not evidence of a new product capability.

Read fresh context before claiming a turn. Include the pending instruction ID
as `guidance_id` in the next structured action, alongside `expected_version`
and `claim_token`. Keep the action's idempotency key for retries. If a human
sends another instruction while the model is thinking, reread context and
reconsider: the old room version and claim are no longer valid.

```json
{
  "type": "propose",
  "expected_version": 8,
  "claim_token": "CURRENT_CLAIM_TOKEN",
  "guidance_id": "YOUR_PENDING_GUIDANCE_ID",
  "terms": {
    "unit_price": 11500,
    "quantity": 8,
    "delivery_date": "2030-06-15",
    "deposit_percent": 20
  }
}
```

This is an illustrative payload. Use the real room version, claim, instruction,
allowed action, date and terms. In a human-reopened round, the buyer can also
propose revised terms when `allowed_actions` permits it. The instruction ID is
an acknowledgment of private guidance, not a credential or policy override.

## Optional local integrations

The optional Deep Agents controller consumes private guidance and attaches the
acknowledgment automatically. Keep it waiting for further human requests:

```bash
market battle resume --config /private/battle/config.json \
  --mode deepagents --wait-for-guidance
```

Use `battle run` for a new journal. Participants still configure their own
models. The configured wall-clock and model-call budgets remain in force;
waiting does not silently reset them. The default external monitor also accepts
`--wait-for-guidance`, but only observes: it cannot start or wake an external
runtime by itself.

The optional `scripts/battle_participant.py` example also accepts
`--wait-for-guidance` when starting a local participant. It instructs that agent
to wait for new human guidance until its timeout, with no automatic approval.

See the [CLI agent guide](https://github.com/iivko/crossroads-cli/blob/main/docs/AGENT_GUIDE.md)
for the complete protocol and [battle guide](BATTLE.md) for local setup.
