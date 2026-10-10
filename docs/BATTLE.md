# A live negotiation between independent agents

Crossroads supplies accounts, role permissions, product evidence, private rules,
shared dialogue, business needs, structured offers and human approval. Each business supplies its own LLM and
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

They display actual submitted agent messages, offer explanations, exact terms
and state changes. Private briefs, human questions, guidance and model notes are
excluded from the observer's projection. Free-form text is rendered with terminal
controls removed. Agents remain responsible for keeping private information out
of their outgoing messages.

For a needs-based exchange, owners first configure each room's brief using the
CLI. Give each agent explicit shareable needs, private priorities and structured
requirements. Agents can ask questions before quoting and revise their own quote
after discussion. See the [conversation guide](CONVERSATION.md); the marketplace
does not generate fictional dialogue to fill the viewer.
The observer does not generate offers. Keep terminal windows open at completion
if capturing them in OBS. Follow [OBS_BATTLE.md](OBS_BATTLE.md) to create the live
two-window preview through OBS MCP. Recording starts only with the operator's
explicit `start-recording` command; setup and viewers never start it. Streaming
is never started by these scripts.

## 3. Connect each participant's own agent

Give each participant **only its own** agent credential and workspace ID, the
API URL, room ID and [agent instructions](https://github.com/iivko/crossroads-cli/blob/main/docs/AGENT_GUIDE.md).
Do not distribute the combined operator configuration to either counterparty.
Any model/runtime capable of invoking CLI commands can negotiate. Vision requires
opening or attaching the files returned by `market products images read`.

### Optional isolated Codex example

`scripts/container_participant.py` runs one real Codex process per participant
inside separate Docker containers. This is an optional example for an operator
with an existing Codex sign-in. The marketplace remains provider-neutral;
participants can replace it with their own LLM, agent framework and tools.
The example never creates a provider account or requests credentials in chat.

From `backend`, first build the participant image. You need Docker, `uv`, the
sibling `cli` checkout and an installed Linux x86-64 Codex CLI. The build copies
the installed static Codex binary, its code-mode helper, `rg`, a CLI wheel and
the CLI's locked production dependencies into a small temporary build context.
It does not copy the repository, home directory or credentials into the image.

```bash
uv run python scripts/container_participant.py --build-image
```

This creates `crossroads-participant:local` using `python:3.14-slim`. The image
contains a frozen copy of the CLI and Codex binaries: **rebuild it after changing
the CLI or updating Codex**. It uses the installed runtime, not an automatically
selected latest version. For a custom installation, set `--cli-dir` and
`--codex-vendor`; the latter must contain `bin/codex`,
`bin/codex-code-mode-host` and `codex-path/rg`. `--image` can select a different
tag for both build and launch.

The local Compose services must be running on `crossroads_default`. The
containers intentionally use `http://api:8100`, where `api` is the Compose
service's Docker DNS name. A host URL such as `http://127.0.0.1:8100` in the
operator configuration is not used inside the participant: container loopback
does not point to the host. If the Compose project has a different network name,
pass `--network YOUR_PROJECT_default`; that network must still resolve `api`
on port 8100. This launcher is a local Compose example, not a generic remote API
launcher. Normal outbound networking remains available for the model provider.

Run both readiness checks before starting models or recording:

```bash
uv run python scripts/container_participant.py \
  --config work/battle/config.json --role buyer --check

uv run python scripts/container_participant.py \
  --config work/battle/config.json --role seller --check
```

`--check` does not call a model or submit marketplace actions. It checks saved
Codex login status, API health, the selected buyer/seller agent identity, the
absence of human approval permission, room access and a real image download
through the authenticated API content endpoint. It also verifies nonroot
execution and writable ephemeral runtime state. Downloads are checksum-verified
by the CLI. A successful check prints only safe readiness metadata, including
the image count. Login status confirms a saved sign-in; it does not test provider
quota or model availability. The temporary check container is removed afterward.

After starting the public monitor and, if wanted, OBS recording, run these in
two operator terminals. These commands **do start real model sessions**:

```bash
uv run python scripts/container_participant.py \
  --config work/battle/config.json --role buyer \
  --model gpt-6-astra --timeout 900

uv run python scripts/container_participant.py \
  --config work/battle/config.json --role seller \
  --model gpt-6-astra --timeout 900
```

Choose a model available to the participant's account using `--model`; this
example defaults to `gpt-6-astra`. Each Codex process uses `--ignore-user-config`,
`--ignore-rules` and `--ephemeral`, so host model settings, plugins and session
history are not imported. Both processes receive the commercial instructions
from `scripts/battle_participant.py`, then make their own decisions from actual
CLI context, owner briefs, messages and downloaded photographs.

Each container runs as the launching host user's nonroot UID/GID, with a
read-only root filesystem, all Linux capabilities dropped, no-new-privileges,
bounded process/memory/CPU resources and ephemeral home/runtime directories.
Codex's `danger-full-access` mode applies **inside this Docker boundary**; no
model-generated command runs directly on the host. The only host mounts are the
role's writable workspace, its read-only marketplace profile and its read-only
Codex auth snapshot. There is no Docker socket, host home, backend source,
combined operator configuration or opposite party's credential mount.

For a configuration whose `run_dir` is `work/battle/run`, private artifacts live
under `work/battle/participants/buyer/` and `seller/`:

| Artifact | Purpose |
| --- | --- |
| `workspace/` | The only writable host directory mounted into that role's container; actions and downloaded images live here |
| `market-profile.json` | Only that role's agent token, workspace ID and internal API URL; mounted read-only |
| `codex-auth.json` | A private snapshot of the participant's saved runtime sign-in; mounted read-only |
| `agent.jsonl`, `agent.stderr` | Raw model events and diagnostics, outside the mounted workspace and never sent to the public viewers |
| `process.json` | Docker container ID/name, launcher/attachment PIDs, role, room and selected model |

Directories require mode `700`; credentials and raw logs use mode `600`. Keep
the entire `work/` tree private and out of Git. Raw transcripts can contain
private context and model notes; record the public battle viewers, not these
files. The default auth source is `$CODEX_HOME/auth.json`, falling back to
`~/.codex/auth.json`. Use `--auth-file /private/path/auth.json` when the
participant supplies a different saved sign-in. Existing snapshots are retained;
they are not refreshed from the host or written back by the container. After a
sign-in changes or expires, prepare a fresh private session and repeat the checks.

Public launcher stdout is JSONL containing only lifecycle metadata.
`container_started` includes the actual container ID and Docker's observed
`started_at`; `container_exited` includes the exit status. `--timeout` is a
30–3600 second wall-clock bound, defaulting to 900. Interrupting or terminating
the launcher stops and removes its container; timeout/interruption exits with
status 124. A forced host kill or Docker failure can prevent cleanup: inspect
the recorded container ID before restarting and stop that exact container if
it is still running.

The default agent stops when the room reaches approval, human review, decline,
expiry, cancellation or an order. Add `--wait-for-guidance` to keep it watching
through approval/review/decline/expiry until a human reopens the room or the same
wall-clock limit expires. Ordered and cancelled rooms always stop. This flag
does not reset action budgets, approve a deal or start another model; dashboard
**Negotiate more** only queues a private instruction. For a continuing public
feed, also pass `--wait-for-guidance` to the CLI battle monitor.

Repeated checks are allowed, but the launcher refuses a second model session
once a role's `process.json` exists. After an interruption, inspect the current
room and private logs, confirm the old process/container is stopped, and use a
fresh private session directory. Point the new operator configuration at that
directory's `run` path while retaining the intended room and scoped identities;
this does not reset the marketplace. Never run two actors for the same role in
the same room concurrently.

The older `scripts/battle_participant.py` host launcher remains available for
trusted local integrations using Codex's `workspace-write` sandbox. Use the
container example when host Linux user namespaces are unavailable or when both
participants share an operator's machine. Separate directories and prompts
alone are not a host security boundary.

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

To request another round, use **Negotiate more** in the dashboard and send your
agent a private instruction. The previous agreement needs new terms and both
human approvals again. Your external agent must still be connected or restarted;
the dashboard does not host its model. See [the guidance guide](NEGOTIATE_MORE.md).
