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

The `setup` action only changes its named scene and sources, then selects that
scene. It refuses to alter the scene while recording or streaming is active.
Setup, status, screenshots and preview never start recording, streaming, the
virtual camera, or the negotiation. Recording requires the explicit commands below.
`preview` opens a windowed OBS projector; it is a live local view.

To customize scene or terminal names:

```bash
uv run scripts/obs_battle.py setup \
  --scene 'Crossroads Battle' \
  --buyer-title 'CROSSROADS | BUYER AGENT' \
  --seller-title 'CROSSROADS | SELLER AGENT' \
  --photo /absolute/path/to/stol.jpg
```

## 4. Verify the picture

```bash
uv run scripts/obs_battle.py screenshot --output work/battle/obs-preview.png
uv run scripts/obs_battle.py status
```

Inspect the screenshot to verify both windows are readable. The status response
reports the current scene and whether OBS is recording or streaming. Prepare the
negotiating agents' credentials, product evidence and rules before launching
them. For a saved demo, start recording in step 5 before launching the agents.
A successful negotiation stops for both human approvals.

If a terminal capture goes black after a window is closed or recreated, open the
viewer again and rerun `setup`. Keep the windows open while watching; hiding or
minimizing a window can stop its compositor updates on some desktops.

## 5. Explicitly record an authorized demo

Use this step when you intend to save a local recording. Verify the selected
scene and both terminal captures first. OBS records the configured scene and
audio; the helper preserves other scenes and does not change audio settings.
The marketplace viewers show the agents' public messages and structured offers,
while each real agent process runs separately with its own credentials.

```bash
RECORDING_DIR="$(pwd)/work/recorded-demo/$(date -u +%Y%m%dT%H%M%SZ)/recording"
uv run scripts/obs_battle.py start-recording \
  --scene 'Crossroads Battle' --record-dir "$RECORDING_DIR"
```

Start the two prepared external agents only after this command reports
`"recording": true`. It uses the OBS MCP tools `get_record_status`,
`get_stream_status`, `get_scene_list`, `get_record_directory`,
`set_record_directory` and `start_record`. An existing recording or active stream
is refused without stopping it. The requested scene must already be selected;
the command does not switch scenes or start a model.
OBS starts recording asynchronously. The helper polls for confirmation for
approximately ten seconds rather than treating the first inactive status as a
failure. If confirmation times out, inspect `status` before launching agents or
retrying; the pending start journal and recording directory remain intact.

When negotiation finishes or the run is stopped, finalize the recording:

```bash
uv run scripts/obs_battle.py stop-recording --record-dir "$RECORDING_DIR"
```

The result includes the verified `output_path`, byte count and
`previous_directory_restored`. OBS chooses the container and codec using its
existing recording settings; the script does not assume MP4 or re-encode video.
The stop command uses OBS MCP `stop_record`, verifies that the returned file
exists and is nonempty in the requested directory, and restores the prior recording directory
when OBS remains idle. It never starts a stream or changes another scene.

Recordings live under the ignored `backend/work/recorded-demo` directory. Each
run has a private `.obs-recording.json` journal recording its directory, scene,
start time and final output path; it contains no OBS password or marketplace
credentials. A new run requires a fresh directory. Omitting `--record-dir` on
start creates a timestamped directory and reports its absolute path; stop always
requires that path explicitly.

Stop refuses a missing journal, another recording directory or a substantially
newer recording that replaced the original. OBS does not provide a durable
recording ID, so avoid starting/stopping recordings manually during a managed
run. A repeated successful stop returns the saved path without stopping a new
recording. If directory restoration failed, repeating stop can retry restoration
without losing the saved video. An unverifiable saved file is reported as an
error; directory restoration is still attempted, and a repeated stop cannot stop
another recording. A lost or delayed start response never triggers an automatic
stop or directory rollback: inspect `status`, then use the same directory's
explicit stop command once OBS confirms recording. Only failures before the
start request was sent restore the previous directory automatically while idle.
