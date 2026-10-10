# Watch the buyer and seller in OBS

The marketplace exposes its API and CLI to independent agents. OBS only displays
their public negotiation activity. It does not run a model, hold marketplace
credentials, negotiate, or approve an order.

The optional operator script uses the **real MCP protocol**: the official Python
MCP client starts the [OBS MCP server](https://github.com/xDarkzx/OBS_MCP) over
stdio, then calls its tools. The server talks to OBS Studio's authenticated
WebSocket. Its source dependency is pinned to the reviewed commit
`4e35426dd15aece5524974f6716dec9e1c5eb1d6` in the script metadata. `uv` installs
these dependencies in an isolated script environment; they are not API or CLI
runtime dependencies.

## Requirements

- OBS Studio with its bundled obs-websocket v5, `uv`, and GNOME Terminal.
- The marketplace CLI installed with `market battle watch` available.
- A battle run directory containing the two public event feeds. The watchers can
  wait for those files while the agents are being prepared.
- For the supplied automatic Linux capture setup, OBS and the two terminal
  windows must run through X11/XWayland. Normal Wayland captures use the desktop
  portal's interactive picker instead.

## 1. Prepare OBS

Close OBS normally before relaunching it in X11 mode. Existing profiles and scene
collections remain available.

```bash
QT_QPA_PLATFORM=xcb obs
```

In **Tools → WebSocket Server Settings**, enable the server and authentication.
The default port is `4455`. Keep the password private. The operator reads it from
the current user's OBS configuration without printing it. An existing separately
configured server can instead use `OBS_HOST`, `OBS_PORT`, and `OBS_PASSWORD`
environment variables supplied by a local secret manager.

The [OBS remote control guide](https://obsproject.com/kb/remote-control-guide)
describes the supported WebSocket setup.

## 2. Open one terminal for each agent

Set the absolute run-directory path to the same directory used by your battle
controller. The terminals run read-only viewers; each independent agent has its
own model runtime and restricted marketplace credentials elsewhere.
Run these commands from the backend repository.

```bash
BATTLE_RUN_DIR="$(pwd)/work/battle/run"
OBS_OPERATOR="$(pwd)/scripts/obs_battle.py"

GDK_BACKEND=x11 gnome-terminal \
  --app-id=org.gnome.Terminal.CrossroadsBuyer \
  --window --hide-menubar --geometry=84x42+0+70 \
  --title='CROSSROADS | BUYER AGENT' \
  -- uv run "$OBS_OPERATOR" terminal --run-dir "$BATTLE_RUN_DIR" --role buyer &

GDK_BACKEND=x11 gnome-terminal \
  --app-id=org.gnome.Terminal.CrossroadsSeller \
  --window --hide-menubar --geometry=84x42+960+70 \
  --title='CROSSROADS | SELLER AGENT' \
  -- uv run "$OBS_OPERATOR" terminal --run-dir "$BATTLE_RUN_DIR" --role seller &
```

Use distinct buyer/seller window titles and open only one window with each title.
The capture helper deliberately rejects ambiguous duplicate windows. Never run
commands that print agent tokens, claim tokens, private contexts, or private
minimum/maximum limits in these captured terminals.

The `terminal` action runs the read-only `market battle watch` command, then
keeps its final output visible until you press Enter. It does not connect to OBS
or start an agent itself. Ctrl+C closes that viewer terminal.

## 3. Create the scene through OBS MCP

From the backend repository:

```bash
uv run scripts/obs_battle.py status
uv run scripts/obs_battle.py setup --photo /absolute/path/to/stol.jpg
uv run scripts/obs_battle.py preview
```

`setup` creates or updates the **Crossroads Battle** scene with two independent
terminal captures, a product photo, labels, and an approval reminder. Repeating
it reconnects captures to the current window IDs, which can change when a
terminal restarts. It preserves the existing canvas size and other scenes.

The helper only changes its named scene and sources, then selects that scene.
It never starts streaming, recording, the virtual camera, or the negotiation.
`preview` opens a windowed OBS projector; it is a live local view.

To customize scene or terminal names:

```bash
uv run scripts/obs_battle.py setup \
  --scene 'Crossroads Battle' \
  --buyer-title 'CROSSROADS | BUYER AGENT' \
  --seller-title 'CROSSROADS | SELLER AGENT' \
  --photo /absolute/path/to/stol.jpg
```

## 4. Verify the picture and start the agents

```bash
uv run scripts/obs_battle.py screenshot --output work/battle/obs-preview.png
uv run scripts/obs_battle.py status
```

Inspect the screenshot to verify both windows are readable. The status response
reports the current scene and whether OBS is recording or streaming. Launch the
negotiating agents only after their credentials, product evidence, rules and
viewers are ready. A successful negotiation stops for both human approvals.

If a terminal capture goes black after a window is closed or recreated, open the
viewer again and rerun `setup`. Keep the windows open while watching; hiding or
minimizing a window can stop its compositor updates on some desktops.
