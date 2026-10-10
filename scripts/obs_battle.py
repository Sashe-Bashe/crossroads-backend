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
per-window XComposite capture. This operator never streams or records.
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
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def connection_environment() -> dict[str, str]:
    """Use supplied credentials, or read OBS's local configuration privately."""
    environment = os.environ.copy()
    config_root = Path(environment.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    config_root /= "obs-studio"
    config = configparser.ConfigParser(interpolation=None)
    config.read(config_root / "global.ini")
    settings: dict = {}
    modern_config = config_root / "plugin_config/obs-websocket/config.json"
    if modern_config.exists():
        settings = json.loads(modern_config.read_text())
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
    response = await session.call_tool(name, arguments)
    if response.isError:
        # Avoid echoing credentials or arbitrary source settings in failures.
        raise RuntimeError(f"OBS MCP tool {name} failed; check the source names and OBS configuration.")
    for content in response.content:
        if content.type == "text":
            return json.loads(content.text)
    raise RuntimeError(f"OBS MCP tool {name} returned no JSON result.")


async def setup(session: ClientSession, args: argparse.Namespace) -> dict:
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

    await text("Title", "CROSSROADS  /  CHAIR PRICE BATTLE", 42, 140, 25, bold=True)
    await text("Subtitle", "8 chairs  ·  Two independent agents  ·  Real CLI offers", 24, 142, 76)
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


async def operate(args: argparse.Namespace) -> None:
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
    parser.add_argument("action", choices=["status", "setup", "preview", "screenshot", "terminal"])
    parser.add_argument("--scene", default="Crossroads Battle")
    parser.add_argument("--buyer-title", default="CROSSROADS | BUYER AGENT")
    parser.add_argument("--seller-title", default="CROSSROADS | SELLER AGENT")
    parser.add_argument("--photo", type=Path)
    parser.add_argument("--output", type=Path, default=Path("work/battle/obs-preview.png"))
    parser.add_argument("--role", choices=["buyer", "seller"])
    parser.add_argument("--run-dir", type=Path)
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


if __name__ == "__main__":
    main()
