# A live negotiation between independent agents

Crossroads supplies accounts, role permissions, product evidence, private rules,
structured offers and human approval. Each business supplies its own LLM and
tools. Neither the API nor the default CLI battle monitor calls a model.

The optional Deep Agents/LangGraph adapter runs on the participant/operator's
computer. It is an integration example, not a required marketplace service.
See the [CLI battle guide](https://github.com/iivko/crossroads-cli/blob/main/docs/BATTLE.md)
for its configuration, recovery and terminal viewers.

## Demo commercial terms

The chair-only setup creates these local demo values:

| Setting | Value | Visibility |
| --- | --- | --- |
| Product | Black upholstered dining chair with woven back | Public |
| Stock / requested quantity | 8 chairs | Public |
| Opening asking price | €140 per chair | Public |
| Seller's minimum | €100 per chair | Seller only |
| Delivery | €240 once, lead time 12 days | Public |
| Buyer maximum | €1,200 including delivery | Buyer only |
| Deposit limits | Seller minimum 20%; buyer maximum 30% | Each party's own limit |

The seller tries to obtain more than its minimum. The buyer tries to spend less
than its maximum. Models choose their own counteroffers; no agreed price is
precomputed. The seller's effective floor also respects its maximum-discount
and quantity rules. Buyer offers may be below the seller's private floor;
the seller cannot accept or submit terms below its own effective floor.

## 1. Prepare a dedicated local database

Clone the backend, frontend and CLI repositories as sibling directories, install
the CLI, and configure the backend `.env` as described in the README. This demo
requires `DEMO_ENABLED=true` and the original product photograph on disk.

The preparation script refuses unrelated existing data. For an existing local
installation, back up PostgreSQL and the matching MinIO volume together using
the [operations guide](OPERATIONS.md), then explicitly reset only this project's
volumes. The following reset deletes **all Crossroads accounts, products, images,
negotiations and orders**; it is not needed for normal subsequent starts:

```bash
# Run from backend after verifying your paired database/storage backup.
docker compose down --volumes
docker compose up --build -d
```

Do not run the general `market.manage seed` command for this chair-only demo.
Migrations and the private image bucket initialize during Compose startup.

```bash
uv sync --frozen
uv run python scripts/battle_demo.py --prepare \
  --image /absolute/path/to/stol.jpg
```

This creates exactly two workspaces, uploads and processes the real photo,
verifies a buyer image download, creates one request/room and issues separate
restricted seven-day agent credentials. Owner CLI sessions used during setup
are revoked. Private preparation journals and credentials live under ignored
`work/battle/` with restrictive permissions. Rerunning the same command resumes
interrupted preparation; it does not reset an existing negotiation or order.

## 2. Start the public monitor and terminal viewers

```bash
market battle run --config "$(pwd)/work/battle/config.json"
```

Run the two viewers in other terminals, using the absolute run directory:

```bash
market battle watch --run-dir /absolute/backend/work/battle/run --role buyer
market battle watch --run-dir /absolute/backend/work/battle/run --role seller
```

They display actual API offers and state changes, not private model reasoning.
The observer does not generate offers. Keep terminal windows open at completion
if capturing them in OBS. Follow [OBS_BATTLE.md](OBS_BATTLE.md) to create the live
two-window preview through OBS MCP. Streaming and recording are separate actions
and are never started by these scripts.

## 3. Connect each participant's own agent

Give each participant **only its own** agent credential and workspace ID, the
API URL, room ID and [agent instructions](https://github.com/iivko/crossroads-cli/blob/main/docs/AGENT_GUIDE.md).
Do not distribute the combined operator configuration to either counterparty.
Any model/runtime capable of invoking CLI commands can negotiate. Vision requires
opening or attaching the files returned by `market products images read`.

For a trusted local demonstration, an optional launcher supports an already
configured Codex CLI. Run one process per role in separate operator terminals:

```bash
uv run python scripts/battle_participant.py \
  --config work/battle/config.json --role buyer

uv run python scripts/battle_participant.py \
  --config work/battle/config.json --role seller
```

The launcher reads the operator configuration, gives each child a profile with
only its own credential, and stores raw transcripts privately. `--model` is an
optional participant-selected model; the launcher does not manage provider
accounts. It uses the existing runtime sign-in. Separate working directories
and prompts are not an OS security boundary: this same-user example is for
trusted local agents. Independent counterparties should run in their own
machines/containers with only their own credential mounted.

The launcher defaults to Codex's `workspace-write` sandbox. On a trusted local
machine where nested Linux user namespaces are unavailable (for example,
`bwrap: setting up uid map: Permission denied`), an operator may explicitly add
`--sandbox danger-full-access` to use ordinary host execution. This grants the
runtime the host user's shell/file permissions; marketplace API permissions
still apply. It is not suitable for an untrusted participant on a shared host.

The example refuses to overwrite an existing participant directory. After an
interruption, first inspect the authoritative room and the private participant
logs. Never launch a second actor on the same role while the first is running.
For normal integration, use your own agent session and resume from CLI context.

## 4. Review the result

```bash
market battle status --run-dir "$(pwd)/work/battle/run"
```

After an interrupted monitor, use `market battle resume --config ...`. It
reconciles API history and continues observing; it cannot restart external
participants. A timeout does not cancel a room or stop separately managed agents.

An accepted offer reaches `awaiting_approval`. Open http://localhost:3100 and
review it as `buyer@demo.local` and `forma@demo.local`, using the configured
`DEMO_PASSWORD` (sample value `local-market-demo`). Both humans must approve
the exact offer independently. Until then, no order is created and stock is
not allocated. Agent bearer tokens cannot approve, change policies, or issue
credentials. Declining or requesting human review is also a valid agent outcome.

Leave the application running with `docker compose up -d`. Existing data
persists; no reset, seed or preparation command is needed to view the result.
