# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "mcp>=1.30,<2",
#   "obs-mcp @ git+https://github.com/xDarkzx/OBS_MCP.git@4e35426dd15aece5524974f6716dec9e1c5eb1d6",
# ]
# ///
"""Arrange two real terminal windows in OBS through the OBS MCP server.

Run with ``uv run scripts/obs_battle.py --help``. OBS must already be running
with its authenticated WebSocket enabled. On Linux/Wayland, launch OBS with
``QT_QPA_PLATFORM=xcb obs`` and the terminals with ``GDK_BACKEND=x11`` to use
per-window XComposite capture. Recording requires explicit start-recording and
stop-recording commands. This operator never starts a stream.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import configparser
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp import ClientSession

RECORDINGS_ROOT = Path(__file__).resolve().parents[1] / "work" / "recorded-demo"
RECORDING_JOURNAL = ".obs-recording.json"
START_STATUS_ATTEMPTS = 20
START_STATUS_INTERVAL = 0.5


def connection_environment() -> dict[str, str]:
    """Use supplied credentials, or read OBS's local configuration privately."""
    environment = os.environ.copy()
    config_root = Path(environment.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    config_root /= "obs-studio"
    config = configparser.ConfigParser(interpolation=None)
    try:
        config.read(config_root / "global.ini")
        settings: dict = {}
        modern_config = config_root / "plugin_config/obs-websocket/config.json"
        if modern_config.exists():
            settings = json.loads(modern_config.read_text())
            if not isinstance(settings, dict):
                raise ValueError("Invalid OBS configuration")
    except (configparser.Error, OSError, ValueError):
        raise ValueError("OBS configuration could not be read; check the local OBS settings.") from None
    environment.setdefault("OBS_HOST", "127.0.0.1")
    environment.setdefault(
        "OBS_PORT",
        str(settings.get("server_port", config.get("OBSWebSocket", "ServerPort", fallback="4455"))),
    )
    environment.setdefault(
        "OBS_PASSWORD",
        settings.get("server_password", config.get("OBSWebSocket", "ServerPassword", fallback="")),
    )
    if not environment["OBS_PASSWORD"]:
        raise ValueError("Enable authenticated OBS WebSocket or provide OBS_PASSWORD privately.")
    return environment


async def call(session: ClientSession, name: str, **arguments) -> dict:
    try:
        response = await session.call_tool(name, arguments)
    except Exception:
        raise RuntimeError(f"OBS MCP tool {name} could not complete; check the OBS connection.") from None
    if response.isError:
        # Avoid echoing credentials or arbitrary source settings in failures.
        raise RuntimeError(f"OBS MCP tool {name} failed; check the source names and OBS configuration.")
    for content in response.content:
        if content.type == "text":
            try:
                result = json.loads(content.text)
                if isinstance(result, dict):
                    return result
            except (ValueError, TypeError):
                pass
            raise RuntimeError(f"OBS MCP tool {name} returned an invalid JSON result.")
    raise RuntimeError(f"OBS MCP tool {name} returned no JSON result.")


async def setup(session: ClientSession, args: argparse.Namespace) -> dict:
    await require_idle(session)
    kinds = (await call(session, "get_input_kind_list"))["inputKinds"]
    if "xcomposite_input" not in kinds:
        raise RuntimeError(
            "XComposite is unavailable. Start OBS using QT_QPA_PLATFORM=xcb and "
            "open the two terminals using GDK_BACKEND=x11."
        )
    scenes = await call(session, "get_scene_list")
    if args.scene not in {scene["sceneName"] for scene in scenes["scenes"]}:
        await call(session, "create_scene", scene_name=args.scene)
    existing = {
        item["sourceName"]: item["sceneItemId"]
        for item in (await call(session, "get_scene_item_list", scene_name=args.scene))["sceneItems"]
    }

    async def source(name: str, kind: str, settings: dict) -> int:
        full_name = f"{args.scene} / {name}"
        if full_name in existing:
            await call(session, "set_input_settings", input_name=full_name, input_settings=settings)
            return existing[full_name]
        result = await call(
            session,
            "create_input",
            input_name=full_name,
            input_kind=kind,
            scene_name=args.scene,
            input_settings=settings,
        )
        existing[full_name] = result["sceneItemId"]
        return result["sceneItemId"]

    video = await call(session, "get_video_settings")
    width, height = video["baseWidth"], video["baseHeight"]
    scale = width / 1920

    async def position(item: int, x: float, y: float, **extra) -> None:
        await call(
            session,
            "set_scene_item_transform",
            scene_name=args.scene,
            scene_item_id=item,
            transform={"positionX": x * scale, "positionY": y * scale, **extra},
        )

    background = await source(
        "Background", "color_source_v3", {"color": 0xFF1B1612, "width": width, "height": height}
    )
    await call(session, "set_scene_item_index", scene_name=args.scene, scene_item_id=background, index=0)
    captures = []
    for role, title, x in [("Buyer", args.buyer_title, 24), ("Seller", args.seller_title, 980)]:
        item = await source(f"{role} Terminal", "xcomposite_input", {"show_cursor": False})
        full_name = f"{args.scene} / {role} Terminal"
        windows = await call(
            session,
            "get_input_properties_list_property_items",
            input_name=full_name,
            property_name="capture_window",
        )
        matches = [window for window in windows["propertyItems"] if window["itemName"] == title]
        if len(matches) != 1:
            raise RuntimeError(f"Expected one X11 terminal named {title!r}; found {len(matches)}.")
        await call(
            session,
            "set_input_settings",
            input_name=full_name,
            input_settings={"capture_window": matches[0]["itemValue"], "show_cursor": False},
        )
        await position(
            item,
            x,
            140,
            boundsType="OBS_BOUNDS_SCALE_INNER",
            boundsAlignment=5,
            boundsWidth=916 * scale,
            boundsHeight=max(100, height - 220 * scale),
        )
        captures.append(title)

    async def text(name: str, value: str, size: int, x: float, y: float, bold=False) -> None:
        item = await source(
            name,
            "text_ft2_source_v2",
            {
                "text": value,
                "font": {"face": "DejaVu Sans", "size": round(size * scale), "flags": int(bold)},
                "color1": 0xFFFFFFFF if bold else 0xFFB6B6B6,
                "color2": 0xFFFFFFFF if bold else 0xFFB6B6B6,
            },
        )
        await position(item, x, y)

    await text("Title", "CROSSROADS  /  AGENT NEGOTIATION", 42, 140, 25, bold=True)
    await text("Subtitle", "Two independent agents  ·  Real dialogue, tradeoffs and offers", 24, 142, 76)
    await text(
        "Footer",
        "LIVE VIEW  ·  Agreement pauses for buyer + seller human approval",
        25,
        40,
        height / scale - 50,
    )
    if args.photo:
        photo = args.photo.expanduser().resolve(strict=True)
        item = await source("Product Photo", "image_source", {"file": str(photo)})
        await position(
            item, 35, 18, boundsType="OBS_BOUNDS_SCALE_INNER", boundsWidth=84 * scale, boundsHeight=84 * scale
        )
    await call(session, "set_current_program_scene", scene_name=args.scene)
    return {"scene": args.scene, "terminals": captures, "canvas": [width, height]}


async def require_idle(session: ClientSession) -> None:
    if (await call(session, "get_record_status"))["outputActive"]:
        raise RuntimeError(
            "OBS is already recording. Leave that recording untouched; stop it with its owner first."
        )
    if (await call(session, "get_stream_status"))["outputActive"]:
        raise RuntimeError("OBS is streaming. This helper will not alter an active stream.")


def recording_directory(requested: Path | None) -> Path:
    root = RECORDINGS_ROOT.resolve()
    target = (requested or root / datetime.now(UTC).strftime("%Y%m%dT%H%M%S-%fZ")).expanduser().resolve()
    if target == root or not target.is_relative_to(root):
        raise ValueError(
            "Recording directories must be a dedicated subdirectory of backend/work/recorded-demo."
        )
    return target


def write_recording_journal(directory: Path, state: dict, *, create: bool = False) -> None:
    target = directory / RECORDING_JOURNAL
    if create:
        output = os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w")
    else:
        output = tempfile.NamedTemporaryFile(mode="w", dir=directory, prefix=".obs-journal-", delete=False)
    with output:
        json.dump(state, output, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    if not create:
        os.replace(output.name, target)


async def restore_record_directory(session: ClientSession, directory: Path, state: dict) -> bool:
    """Do not alter a replacement recording or another operator's settings."""
    try:
        if (await call(session, "get_record_status"))["outputActive"]:
            return False
        current = (await call(session, "get_record_directory"))["recordDirectory"]
        if Path(current).resolve() == Path(state["previous_directory"]).expanduser().resolve():
            return True
        if Path(current).resolve() != directory:
            return False
        await call(session, "set_record_directory", path=state["previous_directory"])
        return True
    except RuntimeError:
        return False


async def start_recording(session: ClientSession, args: argparse.Namespace) -> dict:
    """An explicit operator action; never join or replace an existing output."""
    await require_idle(session)
    scenes = await call(session, "get_scene_list")
    if scenes["currentProgramSceneName"] != args.scene:
        raise RuntimeError("Select the requested Crossroads scene with setup before starting the recording.")
    directory = recording_directory(args.record_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (directory / RECORDING_JOURNAL).exists():
        raise ValueError(
            "This directory already has a recording journal; choose a new timestamped directory."
        )
    previous = (await call(session, "get_record_directory"))["recordDirectory"]
    state = {
        "version": 1,
        "state": "prepared",
        "scene": args.scene,
        "record_directory": str(directory),
        "previous_directory": previous,
        "started_at": datetime.now(UTC).isoformat(),
        "output_path": None,
    }
    write_recording_journal(directory, state, create=True)
    start_dispatched = False
    try:
        await call(session, "set_record_directory", path=str(directory))
        # Recheck before the explicit start in case another operator acted.
        if (await call(session, "get_record_status"))["outputActive"]:
            raise RuntimeError("OBS started recording concurrently; no start command was sent.")
        state["state"] = "start_requested"
        state["started_at"] = datetime.now(UTC).isoformat()
        write_recording_journal(directory, state)
        start_dispatched = True
        await call(session, "start_record")
        for attempt in range(START_STATUS_ATTEMPTS):
            status = await call(session, "get_record_status")
            if status["outputActive"]:
                break
            if attempt + 1 < START_STATUS_ATTEMPTS:
                await asyncio.sleep(START_STATUS_INTERVAL)
        else:
            raise RuntimeError(
                "OBS start was requested but is not confirmed yet. Inspect status before proceeding; "
                "the recording directory and start journal have been preserved."
            )
        state["state"] = "recording"
        write_recording_journal(directory, state)
    except Exception:
        # OBS starts asynchronously. Once dispatched, even an inactive status
        # may mean that startup is pending; preserve its directory and journal.
        try:
            if not start_dispatched and not (await call(session, "get_record_status"))["outputActive"]:
                current = (await call(session, "get_record_directory"))["recordDirectory"]
                if Path(current).resolve() == directory:
                    await call(session, "set_record_directory", path=previous)
                state["state"] = "failed"
                write_recording_journal(directory, state)
        except Exception:
            pass
        raise
    return {
        "recording": True,
        "scene": args.scene,
        "record_directory": str(directory),
        "journal": str(directory / RECORDING_JOURNAL),
        "started_at": state["started_at"],
    }


async def stop_recording(session: ClientSession, args: argparse.Namespace) -> dict:
    """Stop only the explicitly started recording associated with this journal."""
    if args.record_dir is None:
        raise ValueError("stop-recording requires --record-dir from the start-recording result.")
    directory = recording_directory(args.record_dir)
    journal = directory / RECORDING_JOURNAL
    if not journal.is_file() or journal.is_symlink():
        raise ValueError("No regular recording journal exists in that directory.")
    state = json.loads(journal.read_text())
    if (
        not isinstance(state, dict)
        or state.get("version") != 1
        or state.get("record_directory") != str(directory)
    ):
        raise ValueError("The recording journal does not match this directory.")
    if state.get("state") in ("stopped", "stopped_unverified"):
        if not state.get("previous_directory_restored"):
            state["previous_directory_restored"] = await restore_record_directory(session, directory, state)
            write_recording_journal(directory, state)
        if state["state"] == "stopped_unverified":
            raise RuntimeError(
                "OBS was already stopped, but its output file remains unverified. "
                "Inspect the recording directory; no new recording was stopped."
            )
        return {
            "recording": False,
            "output_path": state["output_path"],
            "already_stopped": True,
            "previous_directory_restored": state.get("previous_directory_restored", False),
        }
    if state.get("state") not in ("recording", "start_requested"):
        raise ValueError("This journal does not identify a recording started by this helper.")
    status = await call(session, "get_record_status")
    if not status["outputActive"]:
        raise RuntimeError("OBS is not recording; it may have been stopped outside this helper.")
    current = (await call(session, "get_record_directory"))["recordDirectory"]
    if Path(current).resolve() != directory:
        raise RuntimeError("OBS is recording to another directory. That recording will not be stopped.")
    # OBS exposes duration rather than a recording ID. A much younger output
    # indicates that somebody replaced our recording; do not claim ownership.
    age_ms = (datetime.now(UTC) - datetime.fromisoformat(state["started_at"])).total_seconds() * 1000
    if isinstance(status.get("outputDuration"), (int, float)) and status["outputDuration"] + 10000 < age_ms:
        raise RuntimeError(
            "The active OBS recording no longer matches this start time. It will not be stopped."
        )
    result = await call(session, "stop_record")
    raw_output = result.get("outputPath")
    output = Path(raw_output).expanduser().resolve() if isinstance(raw_output, str) and raw_output else None
    if (
        output is None
        or not output.is_relative_to(directory)
        or not output.is_file()
        or output.stat().st_size == 0
    ):
        state.update(state="stopped_unverified", stopped_at=datetime.now(UTC).isoformat())
        write_recording_journal(directory, state)
        state["previous_directory_restored"] = await restore_record_directory(session, directory, state)
        write_recording_journal(directory, state)
        raise RuntimeError(
            "OBS stopped, but its output file could not be verified in the recording directory."
        )
    state.update(state="stopped", stopped_at=datetime.now(UTC).isoformat(), output_path=str(output))
    write_recording_journal(directory, state)
    state["previous_directory_restored"] = await restore_record_directory(session, directory, state)
    write_recording_journal(directory, state)
    return {
        "recording": False,
        "output_path": str(output),
        "bytes": output.stat().st_size,
        "record_directory": str(directory),
        "previous_directory_restored": state["previous_directory_restored"],
    }


async def operate(args: argparse.Namespace) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from obs_mcp.main import main; main()"],
        env=connection_environment(),
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if args.action == "status":
                stream = await call(session, "get_stream_status")
                record = await call(session, "get_record_status")
                scenes = await call(session, "get_scene_list")
                result = {
                    "scene": scenes["currentProgramSceneName"],
                    "streaming": stream["outputActive"],
                    "recording": record["outputActive"],
                    "scenes": [scene["sceneName"] for scene in scenes["scenes"]],
                }
            elif args.action == "setup":
                result = await setup(session, args)
            elif args.action == "preview":
                result = await call(session, "open_source_projector", source_name=args.scene)
            elif args.action == "start-recording":
                result = await start_recording(session, args)
            elif args.action == "stop-recording":
                result = await stop_recording(session, args)
            else:
                # A dormant OBS preview may need one render for font/image
                # sources to warm up before the screenshot we keep.
                await call(
                    session,
                    "get_source_screenshot",
                    source_name=args.scene,
                    image_format="png",
                )
                await asyncio.sleep(0.2)
                result = await call(
                    session, "get_source_screenshot", source_name=args.scene, image_format="png"
                )
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_bytes(base64.b64decode(result["imageData"].split(",", 1)[1]))
                result = {"screenshot": str(args.output.resolve())}
            print(json.dumps(result, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=["status", "setup", "preview", "screenshot", "terminal", "start-recording", "stop-recording"],
    )
    parser.add_argument("--scene", default="Crossroads Battle")
    parser.add_argument("--buyer-title", default="CROSSROADS | BUYER AGENT")
    parser.add_argument("--seller-title", default="CROSSROADS | SELLER AGENT")
    parser.add_argument("--photo", type=Path)
    parser.add_argument("--output", type=Path, default=Path("work/battle/obs-preview.png"))
    parser.add_argument("--role", choices=["buyer", "seller"])
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument(
        "--record-dir",
        type=Path,
        help="Dedicated directory under backend/work/recorded-demo; generated on start if omitted, required on stop",
    )
    try:
        args = parser.parse_args()
        if args.action == "terminal":
            if not args.role or not args.run_dir:
                parser.error("terminal requires --role and --run-dir")
            print(f"\033]0;CROSSROADS | {args.role.upper()} AGENT\007\033[2J\033[H", end="", flush=True)
            if sys.stdout.isatty():
                # Change only this terminal's palette, not the user's profile.
                print(
                    "\033]10;#e7edf5\007\033]11;#151d29\007"
                    "\033]4;3;#ffcf75\007\033]4;11;#ffcf75\007"
                    "\033]4;6;#6de7ff\007\033]4;14;#6de7ff\007",
                    end="",
                    flush=True,
                )
            try:
                result = subprocess.run(
                    ["market", "battle", "watch", "--run-dir", str(args.run_dir), "--role", args.role],
                    check=False,
                )
                if result.returncode:
                    print(f"Viewer exited with status {result.returncode}.")
                input("\nThe viewer has finished. Press Enter to close this terminal. ")
            except (KeyboardInterrupt, EOFError):
                pass
            return
        asyncio.run(operate(args))
    except (ValueError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")
    except Exception:
        # MCP transports may wrap failures in ExceptionGroup. Do not print raw
        # transport tracebacks, configuration values or source settings.
        parser.exit(
            1, "OBS MCP operation failed. Inspect status before retrying and check the OBS connection.\n"
        )


if __name__ == "__main__":
    main()
