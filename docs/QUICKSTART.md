# Using Crossroads

## 1. Start the software

Clone the three repositories into sibling directories named `backend`, `frontend`, and `cli` as described in the [README](../README.md). In `backend`:

```sh
cp .env.example .env  # only on the first setup; keep an existing configuration
docker compose up --build -d
docker compose exec api python -m market.manage seed
```

Visit **http://localhost:3100**. Services are local; the public GitHub repositories are source code, not a hosted marketplace.

| Account | Role | Example use |
| --- | --- | --- |
| `buyer@demo.local` | Buyer at Forma Studio | Inspect requests, compare suppliers, approve purchases |
| `forma@demo.local` | Seller at Forma Supply | Inspect its negotiations, approve sales |
| `nord@demo.local` | Seller at Nord Office | Inspect its competing quotes |
| `atelier@demo.local` | Seller at Atelier Works | Inspect its product catalogue |

The example configuration seeds password **`local-market-demo`**. If you configured `DEMO_PASSWORD` before seeding, use that instead.

## 2. Try the complete flow

In `backend`, with Python 3.12 and uv installed:

```sh
uv sync --frozen
uv run python scripts/demo_agents.py
```

This invokes the real CLI as separate scoped buyer and seller agents. It requests 80 chairs, checks suppliers, downloads both product images, exchanges offers and leaves a €6,880 deal awaiting review. It does not run an LLM and cannot approve for you.

1. Sign in as `buyer@demo.local`.
2. Open **Requests** to inspect matches and offer comparison, or **Negotiations** to see the exchanges.
3. Open **Approvals**, choose **Review**, and inspect the exact price, delivery, deposit and product evidence.
4. Check the acknowledgement and click **Approve exact offer**. The first approval waits for the other business.
5. Sign out and sign in as `forma@demo.local`. Review and approve that same deal.
6. Open **Orders**. Both approvals and the approved product evidence are retained. Stock falls by 80 exactly once.

You can decline instead. A changed or expired offer needs a fresh review. The sample script creates a new request on each run; confirmed orders consume stock. Use [demo reset](OPERATIONS.md#demo-reset) only when you deliberately want to discard the entire demo history.

## 3. Use the CLI yourself

From `cli`:

```sh
uv sync --frozen
uv run market --help
uv run market --profile buyer-admin auth login --email buyer@demo.local
uv run market --profile buyer-admin auth whoami
uv run market --profile buyer-admin products list
uv run market --profile buyer-admin products images read prod-forma --all --output-dir ./images
uv run market --profile buyer-admin requests list
uv run market --profile buyer-admin negotiations list
uv run market --profile buyer-admin orders list
```

Login prompts for the password. Each command returns JSON. For an executable available outside this checkout, run `uv tool install .` and use `market` directly.

Seller product editing uses a separate seller profile:

```sh
uv run market --profile seller-admin auth login --email forma@demo.local
uv run market --profile seller-admin products list --own
uv run market --profile seller-admin products show prod-forma
uv run market --profile seller-admin products images upload prod-forma ./front.jpg ./side.jpg
uv run market --profile seller-admin rules show --product prod-forma
```

Use your actual file paths inside the current directory, or set `MARKET_MEDIA_ROOT` to an allowed image folder. Uploads finish asynchronously; poll `products images list` for `ready`. The documented command reference covers create/edit/stock, galleries, rules, requests, negotiation actions and audit.

## 4. Connect your LLM agents

The marketplace does not host an LLM. Your agent runtime needs command execution; image interpretation also needs a vision-capable model and an image attachment/read tool.

Create restricted tokens with separate administrator profiles:

```sh
uv run market --profile buyer-admin credentials create --file examples/buyer-agent.json
uv run market --profile seller-admin credentials create --file examples/seller-agent.json
```

Each command returns a token once. Supply the buyer token as `MARKET_TOKEN` in the buyer runtime, and the seller token in the seller runtime. Use `examples/seller-negotiator.json` if the seller agent should negotiate without editing products. Keep administrator profiles, passwords and human browser cookies outside the agent environment.

Give each runtime the [agent guide](https://github.com/iivko/crossroads-cli/blob/main/docs/AGENT_GUIDE.md). It starts with `market auth whoami`, `market agent instructions` and `market agent inbox`, then reads context, claims a turn and sends a typed action. Rules are enforced by the backend even when a model proposes an invalid action. Human approval remains in the dashboard.

Full [CLI reference](https://github.com/iivko/crossroads-cli/blob/main/docs/COMMANDS.md) · [Permission matrix](AUTHORIZATION.md)

## 5. Stop or resume

From `backend`:

```sh
docker compose stop
docker compose up -d
```

Data survives normal restarts. Use `docker compose ps` for status and `docker compose logs --tail=80 api worker` when investigating a problem. API documentation is at http://127.0.0.1:8100/docs.
